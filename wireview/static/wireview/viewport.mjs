/**
 * When a `wire-viewport-top` / `wire-viewport-bottom` binding fires
 * (docs/tutorials/06-streams-api.md, "무한 스크롤").
 *
 * A binding fires when it comes into view while the user scrolls toward it,
 * or when it is already well inside what they see (they scrolled past it, or
 * the list is short).
 *
 * "Scrolls toward it" is asked of every scroller that can move the binding:
 * each ancestor whose `overflow-y` scrolls, and the window. A chat list
 * scrolls inside its own box while the window stands still; measured against
 * the window alone, scrolling up the list was no scroll at all and the top
 * binding never fired. Measured against the nearest such ancestor alone, a
 * wrapper that only computes `overflow-y: auto` (`overflow-x: hidden` alone
 * does that) and never scrolls stood still while the window moved, and a
 * window-scrolled list stopped loading. An ancestor that does not overflow
 * never moves and a window that does not scroll never moves, so any of them
 * moving toward the binding is the user scrolling toward it.
 *
 * "Well inside" is measured in its scroll box: the nearest ancestor that
 * scrolls and actually overflows (`scrollHeight > clientHeight`), or the
 * window when none does, and only the part of that box the window shows.
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
 * Which of a binding's ancestors is its scroll box: the nearest one that
 * scrolls and has more content than it shows. -1 is the window.
 * @param {Array<{overflowY: string, scrollHeight: number, clientHeight: number}>} ancestors - nearest first,
 *   up to but not including `body` (the page itself scrolls as the window)
 * @returns {number}
 */
export function scrollBox(ancestors) {
  return ancestors.findIndex((el) => scrolls(el.overflowY) && el.scrollHeight > el.clientHeight);
}

/**
 * The part of a scroll box the user sees: the box clipped to the window.
 * @param {{top: number, bottom: number} | null} box - its rect, in viewport coordinates; null for the window
 * @param {number} windowHeight
 * @returns {{top: number, bottom: number}}
 */
export function visibleFrame(box, windowHeight) {
  if (box === null) return { top: 0, bottom: windowHeight };
  return { top: Math.max(box.top, 0), bottom: Math.min(box.bottom, windowHeight) };
}

/**
 * Whether the binding sits past the middle of its scroll box, toward the far
 * side: the user went beyond it, or there is too little above (below) it to
 * scroll toward it at all.
 * @param {"top" | "bottom"} type
 * @param {{top: number, bottom: number}} rect - the binding's box, in viewport coordinates
 * @param {{top: number, bottom: number}} frame - its scroll box's visible area, likewise (`visibleFrame`)
 * @returns {boolean}
 */
export function overran(type, rect, frame) {
  const middle = frame.top + (frame.bottom - frame.top) * 0.5;
  return type === "top" ? rect.top > middle : rect.bottom < middle;
}

/**
 * Whether a binding that came into view fires.
 * @param {"top" | "bottom"} type
 * @param {Array<[number, number]>} moves - `[before, after]` for each scroller
 *   that can move it (its scrolling ancestors and the window): the position
 *   when last looked at, and now
 * @param {boolean} past - `overran` for it
 * @returns {boolean}
 */
export function fires(type, moves, past) {
  if (past) return true;
  return moves.some(([before, after]) => (type === "top" ? after < before : after > before));
}
