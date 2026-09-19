/**
 * Whether a render may overwrite what the user typed (#91).
 *
 * A morph copies the server's value into every input it touches. For a field
 * the user is editing, whose value the server has not seen yet, that erases
 * their typing whenever any render lands: another field's event, a broadcast,
 * a presence update. So a field the user edited keeps its value, except where
 * the server's value is an answer to the user:
 *
 * - The render answers an action (any event but `input`) from that field or
 *   its form: Enter in a todo input, a form submit, a blur that saves. The
 *   server's value, usually empty, is the result. `input` events are the
 *   user still typing; their answers keep the text.
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
 * @param {boolean} field.committing - an action from it or its form awaits this render
 * @param {boolean} field.serverChanged - the new render's value differs from the last one
 * @returns {boolean} true to keep the user's value
 */
export function keepsUserValue({ edited, focused, committing, serverChanged }) {
  if (!edited || committing) return false;
  return focused || !serverChanged;
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
