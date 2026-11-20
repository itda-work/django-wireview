/**
 * Where the focus goes (#238, GAP-042): the elements that can take it from the
 * keyboard, their Tab order, where a `wire-focus-wrap` container sends a Tab off
 * its edge, and the stack `push_focus` and `pop_focus` keep.
 *
 * Pure functions and a class over whatever elements the caller passes: the DOM
 * checks (disabled, inert, shown) are the caller's, through `canFocus`.
 * Tested with `node --test tests/js/`.
 */

/** What can take the focus at all, before the checks the caller makes */
export const FOCUSABLE = [
  "a[href]",
  "area[href]",
  "button",
  "input:not([type=hidden])",
  "select",
  "textarea",
  "iframe",
  "audio[controls]",
  "video[controls]",
  "summary",
  "[contenteditable]:not([contenteditable=false])",
  "[tabindex]",
].join(", ");

/** What `focus_first(input_only=True)` looks for */
export const INPUTS = "input:not([type=hidden]), textarea";

/** The attribute that keeps Tab inside its element */
export const WRAP_ATTRIBUTE = "wire-focus-wrap";

/**
 * `elements` in the order Tab visits them: a positive tabindex first, lowest
 * first, then the rest in document order (the order given).
 * @template E
 * @param {E[]} elements - in document order, each one Tab reaches
 * @param {(element: E) => number} tabIndexOf
 * @returns {E[]}
 */
export function tabOrder(elements, tabIndexOf) {
  const positive = elements
    .map((element, index) => ({ element, index, tab: tabIndexOf(element) }))
    .filter(({ tab }) => tab > 0)
    .sort((a, b) => a.tab - b.tab || a.index - b.index)
    .map(({ element }) => element);
  return [...positive, ...elements.filter((element) => !(tabIndexOf(element) > 0))];
}

/**
 * The stops Tab makes among `elements`: one per radio group -- its checked
 * radio, or its first when none is checked -- and every other element.
 * @template E
 * @param {E[]} elements
 * @param {(element: E) => string | null} groupOf - a radio's group key, null for anything else
 * @param {(element: E) => boolean} checkedOf
 * @returns {E[]}
 */
export function radioStops(elements, groupOf, checkedOf) {
  /** @type {Map<string, E>} */
  const stop = new Map();
  for (const element of elements) {
    const group = groupOf(element);
    if (group === null) continue;
    if (!stop.has(group) || (checkedOf(element) && !checkedOf(/** @type {E} */ (stop.get(group))))) stop.set(group, element);
  }
  return elements.filter((element) => {
    const group = groupOf(element);
    return group === null || stop.get(group) === element;
  });
}

/**
 * Where a Tab (or Shift-Tab with `backwards`) inside a wrap container sends the
 * focus, or null when the browser's own move stays inside: from the last
 * element to the first, from the first back to the last, and from the
 * container itself (or anything Tab does not reach) to the first or the last.
 * @template E
 * @param {E[]} order - the container's elements in Tab order
 * @param {E | null} active - the focused element, inside the container
 * @param {boolean} backwards
 * @returns {E | null}
 */
export function wrapTarget(order, active, backwards) {
  if (order.length === 0) return null;
  const first = order[0];
  const last = order[order.length - 1];
  const at = active === null ? -1 : order.indexOf(active);
  if (at === -1) return backwards ? last : first;
  if (backwards) return at === 0 ? last : null;
  return at === order.length - 1 ? first : null;
}

/**
 * @template E
 * @typedef {{element: E, page: number}} FocusEntry
 */

/**
 * The elements `push_focus` remembered, newest last. `pop` skips (and forgets)
 * the ones `valid` turns down: gone, unable to take the focus, another page's.
 * @template E
 */
export class FocusStack {
  /** @param {number} [limit] - the oldest go past this many: a page that never pops does not grow it */
  constructor(limit = 32) {
    this.limit = limit;
    /** @type {FocusEntry<E>[]} */
    this.entries = [];
  }

  /**
   * @param {E} element
   * @param {number} page - the page it was remembered on (boost's pageGeneration)
   */
  push(element, page) {
    this.entries.push({ element, page });
    if (this.entries.length > this.limit) this.entries.splice(0, this.entries.length - this.limit);
  }

  /**
   * The newest entry `valid` accepts, with it and every newer one forgotten.
   * @param {(entry: FocusEntry<E>) => boolean} valid
   * @returns {E | null}
   */
  pop(valid) {
    while (this.entries.length > 0) {
      const entry = /** @type {FocusEntry<E>} */ (this.entries.pop());
      if (valid(entry)) return entry.element;
    }
    return null;
  }

  get size() {
    return this.entries.length;
  }
}
