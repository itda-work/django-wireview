/**
 * @fileoverview What a boosted navigation tells the page once it has landed (#128).
 *
 * A boosted move leaves sticky components alone and turns everything else --
 * `<body>`'s class included -- into the next page. A hook inside a sticky
 * component gets neither `mounted`, `updated` nor `destroyed` from it, so an
 * effect it put on the whole page (a body class, a scroll lock) was undone
 * without its knowing. Two signals close that gap, fired once per navigation,
 * after the new page is in place and its components have joined:
 *
 * - `navigated()` on every hook that was on the page before the move and still
 *   is (the ones the new page brought get `mounted()` instead);
 * - a `wireview:navigated` event on `document`, for page code.
 *
 * Pure functions, no DOM, so node can test them (tests/js/navigation.test.mjs).
 */

/** The event dispatched on `document` after each boosted navigation. */
export const NAVIGATED_EVENT = "wireview:navigated";

/**
 * @typedef {Object} NavigatedDetail
 * @property {string} url - where the navigation ended, after any redirect
 * @property {string} previousUrl - the page it left
 */

/**
 * @template T
 * @typedef {Object} HookEntry
 * @property {number} navigation - the navigation token current when the hook mounted
 * @property {boolean} connected - whether its element is still in the document
 * @property {T} hook
 */

/**
 * The hooks that hear `navigated()` for the navigation `token`.
 *
 * Those mounted before it began and still on the page. A hook mounted under
 * `token` itself arrived with this navigation -- a Back's cached paint mounts
 * the cached page's hooks before the fetched page lands -- and `mounted()` was
 * its news. A hook whose element the morph removed is on its way to
 * `destroyed()`: the observer that says so runs after this.
 *
 * @template T
 * @param {HookEntry<T>[]} entries
 * @param {number} token
 * @returns {T[]}
 */
export function carriedAcross(entries, token) {
  return entries.filter((entry) => entry.navigation < token && entry.connected).map((entry) => entry.hook);
}

/**
 * Remembers the page the last navigation landed on, which is where the next
 * one comes from. A Back has already changed the address bar by the time
 * anything runs, so the previous URL cannot be read then.
 */
export class NavigationLog {
  /** @param {string} url - the page as first loaded */
  constructor(url) {
    /** @type {string} */
    this.current = url;
  }

  /**
   * A navigation landed on `url`.
   * @param {string} url
   * @returns {NavigatedDetail}
   */
  landed(url) {
    const detail = { url, previousUrl: this.current };
    this.current = url;
    return detail;
  }

  /**
   * A patch moved the page to `url` without a navigation to announce: the next
   * navigation leaves from there.
   * @param {string} url
   */
  patched(url) {
    this.current = url;
  }
}

/**
 * A patch (#169): a move inside the page the browser already shows -- same
 * path, another query or fragment. Phoenix's `push_patch`: the address bar and
 * the history change, nothing is fetched, and the components on the page hear
 * `params_changed` with the state they built up. Another path is Phoenix's
 * `push_navigate`: the destination is fetched and its components join.
 *
 * A Back or Forward between such entries is a patch too, as long as the page
 * that made them is still the one on screen. Each page the browser shows --
 * loaded, or fetched by a boosted navigation -- gets an id, and every history
 * entry it makes or lands on carries it in `history.state`. An entry whose id
 * is not the page's own (another page, a page since reloaded) is fetched again.
 */

/** The `history.state` key holding the page id. */
export const PAGE_KEY = "wireviewPage";

/**
 * A new page id. Unique enough for one tab's history: a reload starts a new
 * document whose entries must not match the ones the old document made.
 * @returns {string}
 */
export function newPageId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * `state` with the page id put in, keeping whatever else it holds.
 * @param {any} state - `history.state`, which may be null
 * @param {string} page
 * @returns {Object}
 */
export function stamped(state, page) {
  const base = state !== null && typeof state === "object" ? state : {};
  return { ...base, [PAGE_KEY]: page };
}

/**
 * Whether going from `from` to `to` is a patch: the same origin and path.
 * @param {string} from - the current location
 * @param {string} to - where the server asked to go; relative to `from`
 * @returns {boolean}
 */
export function isPatch(from, to) {
  const here = new URL(from);
  const there = new URL(to, here);
  return here.origin === there.origin && here.pathname === there.pathname;
}

/**
 * Whether a popstate that landed on `url` with `state` is a patch: the entry
 * was made or landed on by the page on screen, and it is the same path.
 * @param {any} state - the entry's `history.state`
 * @param {string} url - the location after the popstate
 * @param {{id: string, url: string}} page - the page on screen and the URL it was shown under
 * @returns {boolean}
 */
export function returnsToPatch(state, url, page) {
  return state?.[PAGE_KEY] === page.id && isPatch(page.url, url);
}
