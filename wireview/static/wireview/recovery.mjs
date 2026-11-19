/**
 * Which forms a new instance gets back, and when (#245, GAP-049).
 *
 * The server holds only the state of the last render; a form the user typed
 * into keeps that on the page alone. Two things build a new instance for a
 * component that was live, from the state its element carries:
 *
 * - a reconnect, whose socket joins every component again, and
 * - an `error` during an event: the instance that raised is gone and the page
 *   joins its element again, the rollback (#94).
 *
 * A `wire-auto-recover` form then gives its values to the new instance, once.
 * The forms are taken when the join goes out (`arm`), and sent when that join
 * has landed -- its `joined`, which the server sends behind the join's render
 * and whatever `joined()` queued -- and its render has been patched in:
 *
 * - Not before. A join the server answers with `renew` or `reload` mounted
 *   nothing; a recovery behind it found no instance and was answered `gone`,
 *   and on the socket a `renew` opens it reached an instance of another
 *   identity (#209). A socket opened for a `renew` recovers nothing.
 * - The same form only: still in the new render, with the id it had (none, if
 *   it had none: then the same element), in the same component, and still
 *   asking for recovery. One the render dropped, renamed or turned off
 *   (`wire-auto-recover="false"`) is not recovered.
 * - What the form holds at that moment: what the user typed since the join
 *   went out is in it, and the morph keeps a field the user edited.
 *
 * A recovery that raises would be rolled back with its form still there, and
 * recovered again, for good. So after an `error` whose rollback recovered,
 * the next `error` of that component rejoins without recovering -- unless the
 * user pressed or typed something in between (`userActed`: a trusted
 * pointerdown or keydown; what a recovery replays is no such thing). Not the answer to an event: a form's
 * recovery answered before another form's recovery raises.
 *
 * Pure bookkeeping; wireview.js finds the forms and sends.
 */

/** The value that turns a form's recovery off. `ignore` stays a handler's name. */
export const RECOVERY_OFF = "false";

/**
 * @typedef {{ kind: "change" } | { kind: "handler", handler: string } | { kind: "off" }} Recovery
 */

/**
 * What a form's `wire-auto-recover` asks for.
 * @param {string | null | undefined} value - the attribute, null when absent
 * @returns {Recovery | null} null when the form does not have the attribute
 */
export function recoveryOf(value) {
  if (value === null || value === undefined) return null;
  if (value === RECOVERY_OFF) return { kind: "off" };
  // A bare attribute, and the "true" a template's boolean renders
  if (value === "" || value === "true") return { kind: "change" };
  return { kind: "handler", handler: value };
}

/**
 * @template F
 * @typedef {{ form: F, id: string | null, owner: string }} Armed
 *   a form taken when its component's join went out: the element, its id
 *   attribute, and the id of the component it belongs to
 */

/**
 * @typedef {{ connected: boolean, id: string | null, owner: string | null, value: string | null }} Found
 *   the form as the page holds it now: whether it is in the document, its id
 *   attribute, the component it belongs to, its `wire-auto-recover`
 */

/**
 * What to recover of an armed form now, or null when it is not the same form.
 * @template F
 * @param {Armed<F>} armed
 * @param {Found | null} found - what the page holds under the form's id, or
 *   the armed element itself when it had none; null when nothing is there
 * @returns {{ kind: "change" } | { kind: "handler", handler: string } | null}
 */
export function stillRecovers(armed, found) {
  if (!found || !found.connected) return null;
  if (found.id !== armed.id || found.owner !== armed.owner) return null;
  const recovery = recoveryOf(found.value);
  if (recovery === null || recovery.kind === "off") return null;
  return recovery;
}

/**
 * @template F
 */
export class Recoveries {
  constructor() {
    /**
     * The forms each join waits to give back, by the id of the component the
     * join is for.
     * @type {Map<string, { ref: number | null, forms: Armed<F>[], afterError: boolean }>}
     */
    this.byId = new Map();
    /** @type {Set<string>} components whose last `error` recovered, with nothing the user did since */
    this.recoveredAfterError = new Set();
    /**
     * Counts `clear()`s: a recovery taken from `landed` and still waiting for
     * its frame goes only while this is what it was then.
     * @type {number}
     */
    this.generation = 0;
  }

  /**
   * A join goes out for `id`. Whatever an earlier join under the id waited to
   * give back is not this one's: the join replaces its instance.
   * @param {string} id
   * @param {number | null | undefined} ref - the join's ref, when it carries one
   * @param {Armed<F>[]} forms - the forms to give back once it lands; none for
   *   a join that recovers nothing (a page's first, a navigation's)
   * @param {{ afterError?: boolean }} [options] - the join is an `error`'s rollback
   */
  arm(id, ref, forms, { afterError = false } = {}) {
    if (!forms.length) {
      this.byId.delete(id);
      return;
    }
    this.byId.set(id, { ref: ref ?? null, forms, afterError });
  }

  /**
   * The join for `id` landed (`joined`): the forms it waited to give back,
   * once. Nothing when the answer is for another join under the id.
   * @param {string} id
   * @param {number | undefined} ref - the `joined`'s
   * @returns {{ forms: Armed<F>[], afterError: boolean }}
   */
  landed(id, ref) {
    const armed = this.byId.get(id);
    if (!armed || (armed.ref !== null && ref !== armed.ref)) return { forms: [], afterError: false };
    this.byId.delete(id);
    return { forms: armed.forms, afterError: armed.afterError };
  }

  /**
   * An `error` during an event of `id`: whether its rollback recovers. Not
   * when the error before it recovered and no event was answered since --
   * that recovery may be what raised.
   * @param {string} id
   * @returns {boolean}
   */
  recoversAfterError(id) {
    if (this.recoveredAfterError.has(id)) {
      this.recoveredAfterError.delete(id);
      return false;
    }
    return true;
  }

  /**
   * An error's rollback gave `id` its forms back.
   * @param {string} id
   */
  recovered(id) {
    this.recoveredAfterError.add(id);
  }

  /**
   * The user did something on the page: an `error` from here is not a
   * recovery's raising again, whatever component it is for.
   */
  userActed() {
    this.recoveredAfterError.clear();
  }

  /**
   * The join for `id` will not land as an instance -- it failed, the server
   * said `renew` or `reload`, or the component left the page.
   * @param {string} id
   */
  drop(id) {
    this.byId.delete(id);
  }

  /** The connection closed, or a `renew` replaces it: nothing it armed is given back. */
  clear() {
    this.byId.clear();
    this.recoveredAfterError.clear();
    this.generation += 1;
  }
}
