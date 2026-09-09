/**
 * @fileoverview Boundary decisions for boosted navigation (#58).
 *
 * A live_session is a page boundary. Navigation inside one may morph the body
 * and keep the WebSocket; navigation across one must be an ordinary page load,
 * because that is what tears the socket down and stands a new one up under the
 * cookies the browser holds *now*. Without it, logging out and boosting back to
 * a protected page reuses the connection that was authenticated before.
 *
 * Pure functions with no DOM of their own: the three navigation entry points
 * (a boosted link, popstate, a server redirect/push) all ask the same question,
 * and it is the kind of question worth testing without a browser
 * (`tests/js/live-session.test.mjs`).
 */

/** Name of the meta tag `{% wireview_header %}` renders. */
export const META_NAME = "wireview-live-session";

/**
 * Reads the live_session a document declares.
 *
 * A document with no meta tag reads as `""`, the same as a page that declared
 * no boundary. That is deliberate: an old cached page, a page served before the
 * upgrade and a plain non-wireview page all answer "no boundary", so navigating
 * to one from inside a session crosses out of it.
 *
 * @param {Document|{querySelector: (s: string) => ({getAttribute: (a: string) => string|null}|null)}} doc
 * @returns {string} The session name, or "" when there is none.
 */
export function readSessionName(doc) {
  const meta = doc?.querySelector?.(`meta[name="${META_NAME}"]`);
  return meta?.getAttribute("content") ?? "";
}

/**
 * Whether moving from `current` to `next` leaves the boundary.
 *
 * @param {string|null|undefined} current - Session the live page is in.
 * @param {string|null|undefined} next - Session of the page being navigated to.
 * @returns {boolean} True when the move needs a full page load.
 */
export function crossesBoundary(current, next) {
  return (current ?? "") !== (next ?? "");
}
