/**
 * What the JS() commands of #237 (GAP-041) decide: a relative target, an
 * attribute's next value, the chain an attribute holds, a chain that comes back
 * to itself.
 *
 * - `JS.inner(sel)` and `JS.closest(sel)` render as `to: {inner: sel}` and
 *   `to: {closest: sel}`: found from the element the binding is on (or, in a
 *   chain `exec` ran, the element that held it), not from the document.
 * - `toggle_attr` with `val` removes the attribute when there and sets it to
 *   `val` when not; with `vals: [a, b]` sets it to `b` when it is `a`, else `a`.
 * - `exec` runs the chain an attribute holds, from that attribute's element:
 *   a `JS()` as it renders (a list of commands) or a binding's own value
 *   (`{"js": [...]}`). A chain that comes back through `exec` to an attribute
 *   it is already running stops there.
 *
 * Pure functions, no DOM: tested with `node --test tests/js/`.
 */

/**
 * @typedef {{inner: string} | {closest: string}} RelativeTarget
 */

/**
 * The relative target a command's `to` names, or null when it names a selector over the document (or none).
 * @param {unknown} to
 * @returns {{kind: "inner" | "closest", selector: string} | null}
 */
export function relativeTarget(to) {
  if (!to || typeof to !== "object") return null;
  const target = /** @type {Record<string, unknown>} */ (to);
  if (typeof target.inner === "string") return { kind: "inner", selector: target.inner };
  if (typeof target.closest === "string") return { kind: "closest", selector: target.closest };
  return null;
}

/**
 * The value `toggle_attr` leaves an attribute with: null removes it.
 * @param {string | null} current - the attribute now, null when absent
 * @param {{val?: string, vals?: [string, string]}} cmd
 * @returns {string | null}
 */
export function toggledAttribute(current, cmd) {
  if (Array.isArray(cmd.vals) && cmd.vals.length === 2) {
    const [first, second] = cmd.vals;
    return current === first ? second : first;
  }
  return current === null ? cmd.val ?? "" : null;
}

/**
 * The commands an attribute holds for `exec`.
 * @param {string | null} value - the attribute, null when the element has none
 * @returns {{commands: Array<Object>} | {missing: true} | {unreadable: string}}
 */
export function chainIn(value) {
  if (value === null) return { missing: true };
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    return { unreadable: "it is not JSON" };
  }
  if (Array.isArray(parsed)) return { commands: parsed };
  if (parsed && typeof parsed === "object" && Array.isArray(/** @type {{js?: unknown}} */ (parsed).js)) {
    return { commands: /** @type {{js: Array<Object>}} */ (parsed).js };
  }
  return { unreadable: "it holds no JS() chain" };
}

/**
 * Whether an `exec` of `attribute` on `element` comes back to a chain already running.
 * @template E
 * @param {ReadonlyArray<readonly [E, string]>} running - the `exec`s the chain is inside, outermost first
 * @param {E} element
 * @param {string} attribute
 * @returns {boolean}
 */
export function comesBack(running, element, attribute) {
  return running.some(([el, name]) => el === element && name === attribute);
}
