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


/**
 * Which navigation queued work belongs to.
 *
 * Boosted navigation queues work that outlives the step that queued it: the
 * body morph runs on a requestAnimationFrame, and morphing the body is what
 * makes the client join the components inside it. So between queueing and
 * running, two things can make that work wrong -- a newer navigation started,
 * or this one turned out to leave the live_session and handed the page to the
 * browser.
 *
 * Both are the same question ("is the navigation I was queued for still the one
 * in flight?"), which is why they are one counter rather than a flag per case.
 */
export class NavigationGate {
  constructor() {
    /** @type {number} */
    this._token = 0;
  }

  /**
   * The navigation in flight. Work captures this when it is queued.
   * @returns {number}
   */
  get token() {
    return this._token;
  }

  /**
   * Start a navigation, abandoning whatever the previous one still had queued.
   * @returns {number} The new token.
   */
  begin() {
    return ++this._token;
  }

  /**
   * End the navigation in flight without starting another.
   *
   * Used when the destination turned out to be across the boundary: the browser
   * is loading a whole new document, and nothing queued for the navigation that
   * discovered this should paint or join in the meantime.
   */
  abandon() {
    this._token += 1;
  }

  /**
   * Whether work queued under `token` may still run.
   * @param {number} token
   * @returns {boolean}
   */
  accepts(token) {
    return token === this._token;
  }
}
