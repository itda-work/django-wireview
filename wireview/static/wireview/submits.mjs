/**
 * A form's submit that waits for the form's uploads (#264).
 *
 * An upload allowed with `auto_upload=False` goes up when the form it is in is
 * submitted, before the submit's event: the handler the event runs then finds
 * the files in `consume_uploads()`. Before this nothing ever started such an
 * upload -- the file stayed registered, and the handler found nothing.
 *
 * What a submit waits for is fixed when it is made: the entries of the form's
 * manual uploads that have not settled yet (chosen, registered, or on their
 * way). One whose upload's config has not come yet waits for it, and takes the
 * entries that config makes once it is a manual one. An entry already settled
 * -- done, or failed earlier -- is not waited for: a form submitted again after
 * a failure sends what is left.
 *
 * From there each step judges the submit again:
 *   - "send" once every entry it waits for is done, and the server said so;
 *   - "fail" when one ended in an error or was cancelled -- the event is not
 *     sent, and the form can be submitted again;
 *   - "abandon" when an entry is gone: its manager ended with its instance (the
 *     component left, a join replaced it, the connection closed);
 *   - "wait" otherwise, with the registered entries nobody started yet to start.
 *
 * Pure bookkeeping; wireview.js finds the form's uploads, starts them and sends
 * the event.
 */

/**
 * @typedef {{ ref: string, status: string, confirmed?: boolean }} EntryView
 * @typedef {{ id: string, name: string }} Slot - a component's upload
 * @typedef {{ id: string, name: string, ref: string }} Start
 * @typedef {{
 *   config(id: string, name: string): { auto_upload?: boolean } | undefined,
 *   entries(id: string, name: string): EntryView[],
 *   waiting(id: string, name: string): number,
 *   alive(id: string): boolean,
 * }} UploadView - what the page's upload managers hold now
 */

/** The statuses an entry no longer leaves. */
const SETTLED = new Set(["error", "cancelled"]);

/**
 * Whether an entry is still to go up, as far as a submit is concerned: not
 * failed, and not confirmed done by the server.
 * @param {EntryView} entry
 */
function unsettled(entry) {
  return !SETTLED.has(entry.status) && !entry.confirmed;
}

/**
 * @param {Slot} slot
 * @param {string} ref
 */
function keyOf(slot, ref) {
  return JSON.stringify([slot.id, slot.name, ref]);
}

/**
 * @template F, D
 * @typedef {{ data: D, entries: Map<string, { slot: Slot, ref: string }>, unconfigured: Slot[] }} Held
 */

/** @template F, D */
export class SubmitUploads {
  constructor() {
    /** @type {Map<F, Held<F, D>>} */
    this.held = new Map();
  }

  /**
   * Whether a submit of `form` is waiting for its uploads: a second submit of
   * the form meanwhile is ignored.
   * @param {F} form
   */
  has(form) {
    return this.held.has(form);
  }

  /**
   * The form is submitted. Its submit waits when one of its manual uploads has
   * an entry to send, or files waiting for their upload's config.
   * @param {F} form
   * @param {Slot[]} slots - the uploads the form holds
   * @param {UploadView} view
   * @param {D} data - what the caller needs to send the event, or to give it up
   * @returns {boolean} whether the submit waits; false: send it now
   */
  begin(form, slots, view, data) {
    /** @type {Held<F, D>} */
    const held = { data, entries: new Map(), unconfigured: [] };
    for (const slot of slots) {
      const config = view.config(slot.id, slot.name);
      if (config === undefined) {
        if (view.waiting(slot.id, slot.name) > 0) held.unconfigured.push(slot);
      } else if (!config.auto_upload) {
        this.#take(held, slot, view);
      }
    }
    if (!held.entries.size && !held.unconfigured.length) return false;
    this.held.set(form, held);
    return true;
  }

  /**
   * Judge every waiting submit again: an upload changed, a config came, a
   * manager ended. A submit that is decided is forgotten.
   * @param {UploadView} view
   * @returns {{ starts: Start[], decided: { form: F, data: D, outcome: "send" | "fail" | "abandon" }[] }}
   */
  advance(view) {
    /** @type {Start[]} */
    const starts = [];
    /** @type {{ form: F, data: D, outcome: "send" | "fail" | "abandon" }[]} */
    const decided = [];
    for (const [form, held] of this.held) {
      const outcome = this.#judge(held, view, starts);
      if (outcome === "wait") continue;
      this.held.delete(form);
      decided.push({ form, data: held.data, outcome });
    }
    // An entry two submits wait for is started once
    const seen = new Set();
    const unique = starts.filter((start) => {
      const key = keyOf(start, start.ref);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return { starts: unique, decided };
  }

  /**
   * The connection closed: nothing a submit waits for will finish on it, and
   * the event is not sent on the next one.
   * @returns {{ form: F, data: D }[]} the submits given up
   */
  clear() {
    const all = [...this.held].map(([form, held]) => ({ form, data: held.data }));
    this.held.clear();
    return all;
  }

  /**
   * @param {Held<F, D>} held
   * @param {UploadView} view
   * @param {Start[]} starts
   * @returns {"send" | "fail" | "abandon" | "wait"}
   */
  #judge(held, view, starts) {
    for (const slot of [...held.unconfigured]) {
      if (!view.alive(slot.id)) return "abandon";
      const config = view.config(slot.id, slot.name);
      if (config === undefined) {
        if (view.waiting(slot.id, slot.name) > 0) continue;
        // The files are gone without a config: nothing of this upload to wait for
      } else if (!config.auto_upload) {
        this.#take(held, slot, view);
      }
      held.unconfigured.splice(held.unconfigured.indexOf(slot), 1);
    }

    let done = true;
    for (const { slot, ref } of held.entries.values()) {
      if (!view.alive(slot.id)) return "abandon";
      const entry = view.entries(slot.id, slot.name).find((each) => each.ref === ref);
      if (!entry) return "abandon";
      if (SETTLED.has(entry.status)) return "fail";
      if (!entry.confirmed) done = false;
      if (entry.status === "registered") starts.push({ id: slot.id, name: slot.name, ref });
    }
    return done && !held.unconfigured.length ? "send" : "wait";
  }

  /**
   * @param {Held<F, D>} held
   * @param {Slot} slot
   * @param {UploadView} view
   */
  #take(held, slot, view) {
    for (const entry of view.entries(slot.id, slot.name)) {
      if (unsettled(entry)) held.entries.set(keyOf(slot, entry.ref), { slot, ref: entry.ref });
    }
  }
}
