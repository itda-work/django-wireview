/**
 * When a `wire-viewport-top` / `wire-viewport-bottom` binding fires
 * (docs/tutorials/06-streams-api.md, "무한 스크롤").
 *
 * A binding fires when it comes into view while the user scrolls toward it,
 * or when it is already well inside what they see (they scrolled past it, or
 * the list is short). Both are measured against the binding's scroll box: the
 * nearest ancestor that scrolls, or the window when none does. A chat list
 * scrolls inside its own box while the window stands still; measured against
 * the window, scrolling up the list was no scroll at all and the top binding
 * never fired.
 *
 * A list drawn bottom up (`flex-direction: column-reverse`) starts at
 * scrollTop 0 and goes negative as the user scrolls up. Up is still "smaller",
 * so the same comparison holds.
 *
 * Pure functions, no DOM: tested with `node --test tests/js/`.
 */

/** `overflow-y` values that make an element its descendants' scroll box. */
const SCROLLING = new Set(["auto", "scroll", "overlay"]);

/**
 * Whether an element with this computed `overflow-y` scrolls its content.
 * @param {string} overflowY
 * @returns {boolean}
 */
export function scrolls(overflowY) {
  return SCROLLING.has(overflowY);
}

/**
 * Whether the binding sits past the middle of its scroll box, toward the far
 * side: the user went beyond it, or there is too little above (below) it to
 * scroll toward it at all.
 * @param {"top" | "bottom"} type
 * @param {{top: number, bottom: number}} rect - the binding's box, in viewport coordinates
 * @param {{top: number, bottom: number}} frame - its scroll box's visible area, likewise
 * @returns {boolean}
 */
export function overran(type, rect, frame) {
  const middle = frame.top + (frame.bottom - frame.top) * 0.5;
  return type === "top" ? rect.top > middle : rect.bottom < middle;
}

/**
 * Whether a binding that came into view fires.
 * @param {"top" | "bottom"} type
 * @param {number} before - its scroll box's position when last looked at
 * @param {number} after - the position now
 * @param {boolean} past - `overran` for it
 * @returns {boolean}
 */
export function fires(type, before, after, past) {
  if (past) return true;
  return type === "top" ? after < before : after > before;
}
