/**
 * Whether a render may overwrite what the user typed (#91, #92).
 * docs/design/input-values.md has the reasoning.
 *
 * A morph copies the server's value into every input it touches. For a field
 * the user is editing, whose value the server has not seen yet, that erases
 * their typing whenever any render lands: another field's event, a broadcast,
 * a presence update. So a field the user edited keeps its value, except where
 * the server's value is an answer to the user:
 *
 * - The render answers a committing action (a submit, a change, leaving the
 *   field, Enter; see isCommitAction) from that field or its form, and the
 *   field still holds what was sent: Enter in a todo input, a form submit, a
 *   blur that saves. The server's value, usually empty, is the result.
 *   Keystrokes typed after the action was sent are never covered by it.
 * - The server rendered a different value for a field that is not focused,
 *   so it means to change it. A focused field is never changed under the
 *   user's cursor, as in Phoenix LiveView; the server can still set it with
 *   `JS().set_value` (push_js), which does not go through a morph.
 *
 * Pure function, no DOM: tested with `node --test tests/js/`.
 */

/**
 * @param {object} field
 * @param {boolean} field.edited - its value differs from the server's last one (`value !== defaultValue`)
 * @param {boolean} field.focused - it is `document.activeElement`
 * @param {boolean} field.committing - this render answers a committing action from it or its form,
 *   and the user has not typed in it since that action was sent
 * @param {boolean} field.serverChanged - the new render's value differs from the last one
 * @param {boolean} [field.typedSinceSent] - the field no longer holds what a committing action
 *   sent from it: the user typed (or deleted) after sending, and the server has not seen that
 * @returns {boolean} true to keep the user's value
 */
export function keepsUserValue({ edited, focused, committing, serverChanged, typedSinceSent = false }) {
  if (typedSinceSent) return true;
  if (!edited || committing) return false;
  return focused || !serverChanged;
}

/** Events that settle a field's value; any other event leaves it to the user (#92). */
const COMMIT_EVENTS = new Set(["submit", "change", "blur", "focusout"]);

/**
 * Whether an event commits the fields it comes from, so the render answering
 * it may reset them: a submit, a change, leaving the field, Enter, or a click
 * on a form's submit button (a save handled with `click.prevent`). Arrow keys,
 * Escape, other clicks and `input` do not: in a search box an arrow key only
 * moves the selection, and must not undo a query not yet sent.
 * @param {string | undefined} type - `event.type`
 * @param {{name: string, arg?: string}[]} [steps] - the binding's modifiers (events.mjs)
 * @param {{submitter?: boolean}} [element] - the bound element is a form's submit button
 * @returns {boolean}
 */
export function isCommitAction(type, steps = [], { submitter = false } = {}) {
  if (!type) return false;
  if (COMMIT_EVENTS.has(type)) return true;
  if (type === "click") return submitter;
  if (type === "keydown" || type === "keypress" || type === "keyup") {
    return steps.some(
      (step) =>
        (step.name === "key" && String(step.arg).toLowerCase() === "enter") ||
        (step.name === "key_code" && String(step.arg) === "13")
    );
  }
  return false;
}

/** Input types whose value the user types or picks, as opposed to toggles and buttons. */
const NOT_EDITABLE = new Set(["checkbox", "radio", "file", "hidden", "submit", "button", "reset", "image"]);

/**
 * @param {string} tagName - `element.tagName`
 * @param {string} [type] - the input's `type`
 * @returns {boolean}
 */
export function isEditableField(tagName, type) {
  if (tagName === "TEXTAREA") return true;
  return tagName === "INPUT" && !NOT_EDITABLE.has((type || "text").toLowerCase());
}

/**
 * @typedef {{tagName: string, type?: string, value: string, defaultValue: string, isConnected: boolean}} Field
 * @typedef {Map<Field, string>} Sent - fields and the values a committing action sent
 */

/**
 * The fields a committing action from `element` covers: exactly the fields it
 * sends. That is the element's form when the form is the scope serialized for
 * the event, and otherwise only the element itself. A LiveComponent inside an
 * ancestor form, targeted with `myself`, sends its own scope, not the form.
 * @param {any} element - the bound element (a form, a field, a button)
 * @param {any} formScope - the element the event serializes (a form or a component)
 * @returns {Sent}
 */
export function commitScope(element, formScope) {
  const form =
    element.tagName === "FORM" ? element : (element.form ?? (element.closest ? element.closest("form") : null));
  const fields = form && form === formScope ? Array.from(form.elements) : [element];
  /** @type {Sent} */
  const sent = new Map();
  for (const field of fields) {
    if (isEditableField(field.tagName, field.type)) sent.set(field, field.value);
  }
  return sent;
}

/**
 * Which fields a render may reset, paired with the events that asked (#92).
 *
 * A committing action records its fields and their sent values under its
 * `ref`. The render carrying that `ref` turns them into a permission, which
 * the caller passes to the one morph that render causes, and to nothing else:
 * another render cannot use it, earlier or later, so the frame the morph runs
 * in does not matter. An answer also settles every earlier ref, because the
 * server handles a connection's events in order: a ref still unanswered then
 * never will be (its handler raised, its component was gone).
 *
 * A server that does not echo refs gets `record(null, ...)`: the first morph
 * that touches such a field may reset it, which is #91's first behaviour.
 */
export class ValueGuard {
  /** @param {{activeElement?: () => any}} [options] */
  constructor({ activeElement = () => (typeof document === "undefined" ? null : document.activeElement) } = {}) {
    this.activeElement = activeElement;
    /** @type {Map<number, Sent>} unanswered refs */
    this.pending = new Map();
    /** @type {Sent} fields marked without a ref */
    this.unpaired = new Map();
  }

  /**
   * @param {number | null} ref - the event's ref, or null when the server does not echo refs
   * @param {Sent} fields
   */
  record(ref, fields) {
    this.prune();
    if (!fields.size) return;
    if (ref === null) {
      for (const [field, sent] of fields) this.unpaired.set(field, sent);
    } else {
      this.pending.set(ref, fields);
    }
  }

  /**
   * The render answering `ref` arrived: its permission, for its own morph.
   * @param {number} ref
   * @returns {Sent}
   */
  answer(ref) {
    const fields = this.pending.get(ref) ?? new Map();
    for (const earlier of [...this.pending.keys()]) {
      if (earlier <= ref) this.pending.delete(earlier);
    }
    return fields;
  }

  /** The connection closed: no answer to anything sent on it will come. */
  clear() {
    this.pending.clear();
    this.unpaired.clear();
  }

  /** Forget fields that left the page. */
  prune() {
    for (const [ref, fields] of this.pending) {
      for (const field of fields.keys()) if (!field.isConnected) fields.delete(field);
      if (!fields.size) this.pending.delete(ref);
    }
    for (const field of this.unpaired.keys()) if (!field.isConnected) this.unpaired.delete(field);
  }

  /** @returns {number} */
  pendingCount() {
    return this.pending.size;
  }

  /**
   * What an unanswered committing action last sent from this field, if any.
   * @param {Field} field
   * @returns {string | undefined}
   */
  lastSent(field) {
    let sent;
    for (const fields of this.pending.values()) if (fields.has(field)) sent = fields.get(field);
    return sent;
  }

  /**
   * Decide for one field about to be morphed. When it keeps the user's value,
   * the server's value still goes to `defaultValue`: a field the user edited
   * does not change what it shows when that changes, and the next render
   * compares against what the server actually sent.
   * @param {any} field - the element in the page
   * @param {any} next - the element the render would morph it into
   * @param {Sent} [permission] - from answer(), for the morph of the answering render only
   * @returns {boolean} true when the value must not be touched
   */
  keep(field, next, permission) {
    if (!isEditableField(field.tagName, field.type) || field.tagName !== next.tagName) return false;
    let sent = permission?.get(field);
    if (sent === undefined && this.unpaired.has(field)) {
      sent = this.unpaired.get(field);
      this.unpaired.delete(field);
    }
    const committing = sent !== undefined && sent === field.value;
    const covering = sent ?? this.lastSent(field);
    const keep = keepsUserValue({
      edited: field.value !== field.defaultValue,
      focused: field === this.activeElement(),
      committing,
      serverChanged: next.defaultValue !== field.defaultValue,
      typedSinceSent: covering !== undefined && covering !== field.value,
    });
    if (keep) field.defaultValue = next.defaultValue;
    return keep;
  }
}
