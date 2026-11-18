/**
 * A form's own properties and methods, read past its fields (#246).
 *
 * A form element exposes its named controls as properties: `<input
 * name="action">` makes `form.action` that input, `name="elements"` hides the
 * control collection, `name="getAttribute"` the method. A field so named is an
 * ordinary one -- a login form's "action", an editor's "method" -- so what the
 * library reads off a form it reads from the prototypes, as the browser's own
 * submission does.
 *
 * The globals are looked up when called: node imports this module too.
 */

/**
 * @param {string} name
 * @returns {(this: HTMLFormElement) => any}
 */
function getter(name) {
  return /** @type {(this: HTMLFormElement) => any} */ (
    /** @type {PropertyDescriptor} */ (Object.getOwnPropertyDescriptor(HTMLFormElement.prototype, name)).get
  );
}

/**
 * The URL the form submits to, as its submission reads it: its `action`
 * attribute resolved against the document, or the document's URL without one.
 * @param {HTMLFormElement} form
 * @returns {string}
 */
export function formActionOf(form) {
  return getter("action").call(form);
}

/**
 * The controls the form owns, a `form="id"` one outside it included.
 * @param {HTMLFormElement} form
 * @returns {HTMLFormControlsCollection}
 */
export function controlsOf(form) {
  return getter("elements").call(form);
}

/**
 * @param {Element} el
 * @param {string} name
 * @returns {string|null}
 */
export function attributeOf(el, name) {
  return Element.prototype.getAttribute.call(el, name);
}

/**
 * @param {Element} el
 * @param {string} name
 * @param {string|null} value - null removes it
 */
export function setAttributeOf(el, name, value) {
  if (value === null) Element.prototype.removeAttribute.call(el, name);
  else Element.prototype.setAttribute.call(el, name, value);
}

/**
 * Submit `form` as `HTMLFormElement.submit` does: no `submit` event, no
 * constraint validation.
 * @param {HTMLFormElement} form
 */
export function submitForm(form) {
  HTMLFormElement.prototype.submit.call(form);
}
