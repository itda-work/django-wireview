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
 * The same holds for what the user picks rather than types (#271): a
 * checkbox's or a radio's check and a select's options. A render answering an
 * older event carries the server's state from before the newer pick, and the
 * morph would put it back; the pick stands until its own committing action's
 * answer, or until the server renders a different state for it.
 *
 * Neither applies to a field an IME is composing in (#169): writing its value
 * ends the composition under the IME, and the next keystroke starts another,
 * doubling the syllable. That holds even when the field still shows exactly
 * what the server last rendered, which the rules above would call unedited.
 * The server's value goes to `defaultValue` as for any kept field; once the
 * composition ends, the field holds what the user composed and is an edited
 * field like any other -- the user's text stands until a committing action's
 * answer or a later change while it is not focused.
 *
 * A composition can also end without `compositionend`: a script writing
 * another value into the field (`JS().set_value`, an input mask) drops it, and
 * so does taking the element out and putting it back. So a field counts as
 * composing only while it has the focus a composition needs, and losing or
 * regaining focus forgets it (ValueGuard.compose). A dropped composition never
 * holds a field against the server once the user has left it.
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
 * @param {boolean} [field.composing] - an IME composition is open in it
 * @returns {boolean} true to keep the user's value
 */
export function keepsUserValue({ edited, focused, committing, serverChanged, typedSinceSent = false, composing = false }) {
  if (composing || typedSinceSent) return true;
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

/** Input types with nothing for the user to type or pick. */
const NOT_GUARDED = new Set(["file", "hidden", "submit", "button", "reset", "image"]);

/**
 * What the user sets in a field: `text` they type (its `value`), a `toggle`'s
 * check (checkbox, radio) or a `select`'s options. Null for an element whose
 * state no render needs to guard.
 * @param {string} tagName - `element.tagName`
 * @param {string} [type] - the input's `type`
 * @returns {"text" | "toggle" | "select" | null}
 */
export function fieldKind(tagName, type) {
  if (tagName === "TEXTAREA") return "text";
  if (tagName === "SELECT") return "select";
  if (tagName !== "INPUT") return null;
  const kind = (type || "text").toLowerCase();
  if (kind === "checkbox" || kind === "radio") return "toggle";
  return NOT_GUARDED.has(kind) ? null : "text";
}

/**
 * The values of a select's options picked by `picked`, as one string.
 * @param {any} select
 * @param {(option: any) => boolean} picked
 * @returns {string}
 */
function optionValues(select, picked) {
  return JSON.stringify(Array.from(select.options).filter(picked).map((option) => option.value));
}

/**
 * The options a select shows before the user picks: the ones marked `selected`,
 * or for a single select the last of them, and without any the first enabled
 * option of a drop-down (HTML's selectedness setting algorithm).
 * @param {any} select
 * @returns {string}
 */
function defaultOptions(select) {
  const options = Array.from(select.options);
  let picked = options.filter((option) => option.defaultSelected);
  if (!select.multiple) {
    if (picked.length) picked = picked.slice(-1);
    else if (!(select.size > 1)) picked = options.filter((option) => !option.disabled).slice(0, 1);
  }
  return JSON.stringify(picked.map((option) => option.value));
}

/**
 * What the field shows now, as a string to compare: the text, the check, the
 * picked options.
 * @param {any} field
 * @returns {string}
 */
export function shownState(field) {
  const kind = fieldKind(field.tagName, field.type);
  if (kind === "toggle") return field.checked ? "checked" : "";
  if (kind === "select") return optionValues(field, (option) => option.selected);
  return field.value;
}

/**
 * What the server last rendered for the field, in shownState's terms: its
 * `defaultValue`, `defaultChecked`, or options marked `selected`.
 * @param {any} field - a field in the page, or the element a render would morph it into
 * @returns {string}
 */
export function serverState(field) {
  const kind = fieldKind(field.tagName, field.type);
  if (kind === "toggle") return field.defaultChecked ? "checked" : "";
  if (kind === "select") return defaultOptions(field);
  return field.defaultValue;
}

/**
 * For a toggle or a select the guard keeps: puts back what it shows after the
 * morph. The morph is left to copy the render's attributes, so the field's
 * defaults become the server's, and then sets the checks and options the
 * render has; this undoes the second half. Its attributes cannot be held
 * instead: a radio, or a select's option, that the user did not touch follows
 * its `checked` or `selected` attribute, and moves the group's pick with it.
 * Text fields need none: the guard keeps their value attribute (keep()).
 * @param {any} field
 * @param {string} [shown] - what it showed before the morph began (radiosShown)
 * @returns {(() => void) | null} call after the morph; null for a text field
 */
export function holdShown(field, shown = shownState(field)) {
  const kind = fieldKind(field.tagName, field.type);
  if (kind === "toggle") {
    const checked = shown === "checked";
    return () => {
      if (field.isConnected) field.checked = checked;
    };
  }
  if (kind === "select") {
    const values = new Set(JSON.parse(shown));
    return () => {
      if (!field.isConnected) return;
      const options = Array.from(field.options);
      if (field.multiple) {
        for (const option of options) option.selected = values.has(option.value);
      } else {
        const option = options.find((each) => values.has(each.value));
        if (option) option.selected = true;
      }
    };
  }
  return null;
}

/**
 * What every radio under `root` shows before a morph begins. A morph that
 * checks the server's radio unchecks the group's others before it reaches
 * them, so a radio the user checked reads as unchecked, and unedited, by the
 * time the guard looks at it: keep() and holdShown() take this instead.
 * Checkboxes and selects change only when the morph reaches them.
 * @param {any} root - the element about to be morphed
 * @returns {Map<any, string>}
 */
export function radiosShown(root) {
  /** @type {Map<any, string>} */
  const shown = new Map();
  const radios = root.querySelectorAll ? Array.from(root.querySelectorAll("input[type=radio i]")) : [];
  if (root.tagName === "INPUT" && String(root.type).toLowerCase() === "radio") radios.push(root);
  for (const radio of radios) shown.set(radio, shownState(radio));
  return shown;
}

/**
 * @typedef {{tagName: string, type?: string, value: string, defaultValue: string, isConnected: boolean}} Field
 *   a text field; a toggle has `checked` and `defaultChecked`, a select `options` and `multiple`
 * @typedef {Map<Field, string>} Sent - fields and what a committing action sent from them (shownState)
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
    if (fieldKind(field.tagName, field.type)) sent.set(field, shownState(field));
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
    /** @type {Set<Field>} fields an IME is composing in (compositionstart, not yet compositionend) */
    this.composing = new Set();
  }

  /**
   * An IME composition opened or closed in `field`. Not a server matter, so
   * `clear()` leaves it.
   * @param {Field} field
   * @param {boolean} open
   */
  compose(field, open) {
    if (open) this.composing.add(field);
    else this.composing.delete(field);
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
    for (const field of this.composing) if (!field.isConnected) this.composing.delete(field);
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
   * Decide for one field about to be morphed. When a text field keeps the
   * user's value, the server's value still goes to `defaultValue`: a field
   * the user edited does not change what it shows when that changes, and the
   * next render compares against what the server actually sent. A toggle or
   * a select the guard keeps takes the server's state the same way, through
   * the morph's attributes; holdShown() puts back what it shows.
   * @param {any} field - the element in the page
   * @param {any} next - the element the render would morph it into
   * @param {Sent} [permission] - from answer(), for the morph of the answering render only
   * @param {string} [shown] - what it showed before the morph began, for a radio (radiosShown)
   * @returns {boolean} true when what the field shows must not be touched
   */
  keep(field, next, permission, shown) {
    const kind = fieldKind(field.tagName, field.type);
    if (!kind || field.tagName !== next.tagName || fieldKind(next.tagName, next.type) !== kind) return false;
    shown ??= shownState(field);
    let sent = permission?.get(field);
    if (sent === undefined && this.unpaired.has(field)) {
      sent = this.unpaired.get(field);
      this.unpaired.delete(field);
    }
    const committing = sent !== undefined && sent === shown;
    const covering = sent ?? this.lastSent(field);
    const keep = keepsUserValue({
      edited: shown !== serverState(field),
      focused: field === this.activeElement(),
      committing,
      serverChanged: serverState(next) !== serverState(field),
      typedSinceSent: covering !== undefined && covering !== shown,
      // Only under the user's cursor: a composition a script's write to the
      // value or a re-insertion dropped ends without compositionend (#169)
      composing: kind === "text" && this.composing.has(field) && field === this.activeElement(),
    });
    if (keep && kind === "text") field.defaultValue = next.defaultValue;
    return keep;
  }
}
