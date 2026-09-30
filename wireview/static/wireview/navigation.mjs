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
}
