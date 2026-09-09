/**
 * The full-page-reload decision (see docs/implementation/wire-protocol.md).
 *
 * The server sends `reload` when a component's signed state cannot be used:
 * it expired, it predates the envelope format, or it does not verify. A full
 * page load is the recovery, because the server re-renders with the current
 * auth context and issues fresh tokens.
 *
 * The risk is a loop: if the freshly rendered page is rejected again (a server
 * misconfiguration, mismatched SECRET_KEY between processes), the page would
 * reload forever. The cooldown below turns that into one warning per half
 * minute instead. The decision is a pure function so `tests/js/` can check it
 * without a DOM.
 */

/** Storage key holding the epoch milliseconds of the last reload we triggered. */
export const RELOAD_STORAGE_KEY = "wireview:last-reload";

/** How long after a reload another one is refused, in milliseconds. */
export const RELOAD_COOLDOWN_MS = 30000;

/**
 * Should the page reload now?
 *
 * @param {string|number|null|undefined} lastReloadAt - What storage holds for
 *   the previous reload, as read (a string), or null when there is none.
 * @param {number} now - Current epoch milliseconds.
 * @param {number} [cooldown] - Milliseconds to wait after a reload.
 * @returns {boolean}
 */
export function shouldReload(lastReloadAt, now, cooldown = RELOAD_COOLDOWN_MS) {
  const previous = Number(lastReloadAt);
  // No record, an unreadable one, or a clock that moved backwards: reload.
  if (!Number.isFinite(previous) || previous <= 0) return true;
  const elapsed = now - previous;
  if (elapsed < 0) return true;
  return elapsed >= cooldown;
}
