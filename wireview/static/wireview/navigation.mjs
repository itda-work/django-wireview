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
 * The event dispatched on `document` when a boosted form submission put no
 * page on screen (#170). The form is not sent again, and the page stays.
 * `detail` is `{url, method, answered}`: `answered` false -- the network
 * failed, not the server, and the form may or may not have reached it; true --
 * the server took it and redirected to another origin, which a boosted form
 * cannot follow (`fetchOutcome`).
 */
export const NAVIGATION_FAILED_EVENT = "wireview:navigation-failed";

/**
 * The cancelable event dispatched on `document` before a move boost makes
 * (#154): `preventDefault()` keeps the page where it is -- a form with unsaved
 * input, say. `beforeunload` covers a full page load and never a boosted one,
 * whose `pushState` the browser does not see as leaving.
 */
export const BEFORE_NAVIGATE_EVENT = "wireview:before-navigate";

/**
 * What started a move (#154), in `wireview:before-navigate` and
 * `wireview:navigated`:
 * - "link": a boosted link
 * - "form": a boosted form (`wire-boost`), GET or POST
 * - "visit": page code -- `wireview.visit()`, the `JS().navigate()` command
 * - "push", "replace": the server's `push_to`, `replace_to`
 * - "redirect": the server's `redirect_to`
 * - "popstate": Back or Forward
 * @typedef {"link" | "form" | "visit" | "push" | "replace" | "redirect" | "popstate"} NavigationKind
 */

/**
 * @typedef {Object} BeforeNavigateDetail
 * @property {string} url - where the move goes, resolved; for a popstate, the
 *   entry the address bar already names
 * @property {NavigationKind} kind
 * @property {boolean} patch - the page stays and only its params change
 *   (`push_to` on the same path, Back between such entries): nothing is
 *   fetched, and the components hear `params_changed`
 * @property {HTMLFormElement} [form] - the form being sent, for "form"
 */

/**
 * The detail of `wireview:before-navigate`.
 * @param {string} url
 * @param {string} base - what a relative `url` resolves against
 * @param {NavigationKind} kind
 * @param {{patch?: boolean, form?: HTMLFormElement | null}} [options]
 * @returns {BeforeNavigateDetail}
 */
export function beforeNavigateDetail(url, base, kind, { patch = false, form = null } = {}) {
  const detail = { url: new URL(url, base).href, kind, patch };
  return form ? { ...detail, form } : detail;
}

/**
 * @typedef {Object} NavigatedDetail
 * @property {string} url - where the navigation ended, after any redirect
 * @property {string} previousUrl - the page it left
 * @property {NavigationKind} kind - what started it (#154)
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
   * @param {NavigationKind} kind - what started it
   * @returns {NavigatedDetail}
   */
  landed(url, kind) {
    const detail = { url, previousUrl: this.current, kind };
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
 * Undoing a cancelled Back or Forward (#154). The address bar has moved by the
 * time `popstate` runs, and the History API tells no page which entry it left
 * or how far it went: a guess written into `history.state` sent a later undo
 * the wrong way once an entry boost never stamped was in between. The
 * Navigation API knows: `currententrychange` names the entry a traversal
 * left, and `navigation.traverseTo(key)` returns there whatever else is queued
 * -- a relative `history.go()` issued from a popstate lands somewhere else
 * when another Back runs first. Without that API a traversal is announced and
 * not cancelable.
 *
 * While the undo is on its way, a popstate is either its arrival -- the entry
 * it left -- or another traversal that ran first, which the undo then
 * overrides. Neither asks the page or arrives anywhere.
 */
export class TraversalUndo {
  constructor() {
    /** @type {{key: string} | null} the undo on its way, and the entry it returns to */
    this.current = null;
  }

  /**
   * An undo to the entry `key` began.
   * @param {string} key
   * @returns {{key: string}} the undo, for `failed`
   */
  start(key) {
    this.current = { key };
    return this.current;
  }

  /**
   * A popstate arrived at the entry `key`.
   * - "none": no undo is on its way; an ordinary traversal
   * - "returned": the undo arrived, and is over
   * - "passing": another traversal ran before the undo, which will override it
   * @param {string | null} key
   * @returns {"none" | "returned" | "passing"}
   */
  arrived(key) {
    if (this.current === null) return "none";
    if (key !== this.current.key) return "passing";
    this.current = null;
    return "returned";
  }

  /**
   * `undo` will not arrive: the browser refused or dropped its traversal.
   * @param {{key: string}} undo
   * @returns {boolean} whether it was still on its way -- the page then has to
   *   arrive at wherever the address bar is
   */
  failed(undo) {
    if (this.current !== undo) return false;
    this.current = null;
    return true;
  }
}

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
 * @param {string} from - the URL of the page on screen
 * @param {string} to - where the server asked to go
 * @param {string} [base] - what a relative `to` resolves against: the
 *   document's base URL, as `pushState` and `fetch` resolve it; `from` by default
 * @returns {boolean}
 */
export function isPatch(from, to, base = from) {
  const here = new URL(from);
  const there = new URL(to, base);
  return here.origin === there.origin && here.pathname === there.pathname;
}

/**
 * @typedef {Object} PageOnScreen
 * @property {string|null} id - null from the moment a navigation that fetches
 *   begins until its page lands: meanwhile the screen holds the page being
 *   left or a cached copy of another one, and no entry is that page's
 * @property {string} url - the URL it was shown under
 */

/**
 * Whether the server's push or replace to `to` is a patch of the page on
 * screen. Judged against that page, not the address bar: a push that fetches
 * has already moved the address bar to a page not shown yet. While a fetch is
 * in flight nothing is a patch -- the page it would patch is on its way out.
 * @param {PageOnScreen} page
 * @param {string} to
 * @param {string} base - the document's base URL
 * @returns {boolean}
 */
export function patchesPage(page, to, base) {
  return page.id !== null && isPatch(page.url, to, base);
}

/**
 * Whether a popstate that landed on `url` with `state` is a patch: the entry
 * was made or landed on by the page on screen, and it is the same path.
 * @param {any} state - the entry's `history.state`
 * @param {string} url - the location after the popstate
 * @param {PageOnScreen} page - the page on screen and the URL it was shown under
 * @returns {boolean}
 */
export function returnsToPatch(state, url, page) {
  return page.id !== null && state?.[PAGE_KEY] === page.id && isPatch(page.url, url);
}

/**
 * Whether `to` names the very URL `from` is (#170): a push there makes no new
 * history entry, as the browser makes none for a link to the page it shows
 * and Phoenix's pushState skips one to `location.href`.
 * @param {string} from - the address bar
 * @param {string} to
 * @param {string} [base] - what a relative `to` resolves against; `from` by default
 * @returns {boolean}
 */
export function isSameUrl(from, to, base = from) {
  return new URL(to, base).href === new URL(from).href;
}

/**
 * Whether `from` and `to` are one document's URL apart from the fragment: the
 * same origin, path and query (#170). A move between them -- a click on
 * `<a href="#section">`, Back from it -- is the browser's own: it scrolls and
 * makes or walks a history entry, and the page neither fetches nor tells its
 * components anything, as Phoenix ignores a popstate that only moved the hash.
 * @param {string} from
 * @param {string} to - resolved already
 * @returns {boolean}
 */
export function onlyFragmentMoved(from, to) {
  const here = new URL(from);
  const there = new URL(to);
  return here.origin === there.origin && here.pathname === there.pathname && here.search === there.search;
}

/**
 * Whether a link to `to` is a jump inside the document at `from`: a fragment
 * navigation, which boost leaves to the browser (#170). It has a fragment --
 * `href="#"` included -- and nothing else differs. A link to the very URL with
 * no fragment is a new page load to the browser, and so to boost.
 * @param {string} from - the address bar
 * @param {string} to
 * @param {string} [base] - what a relative `to` resolves against; `from` by default
 * @returns {boolean}
 */
export function isFragmentLink(from, to, base = from) {
  const there = new URL(to, base).href;
  return there.includes("#") && onlyFragmentMoved(from, there);
}

/**
 * The method a form submits with, as the browser reads it (#170): the
 * submitter's `formmethod` if it has one, else the form's `method`, each "get",
 * "post" or "dialog" whatever its case. Anything else -- `put`, `delete`, an
 * empty value -- is GET, as the browser sends it; a missing `method` is too.
 * What `HTMLFormElement.method` and `formMethod` reflect.
 * @param {string|null} formAttribute - the form's `method`
 * @param {string|null} [submitterAttribute] - the submitter's `formmethod`
 * @returns {"get" | "post" | "dialog"}
 */
export function formMethod(formAttribute, submitterAttribute = null) {
  const method = (submitterAttribute ?? formAttribute ?? "").toLowerCase();
  return method === "post" || method === "dialog" ? method : "get";
}

/**
 * The request a boosted form submission sends (#170). A GET goes as a link
 * does; anything else is sent once and never again, so it goes in `no-cors`
 * mode: a redirect inside this origin is followed and read as with any fetch
 * -- post/redirect/get lands as before -- while one to another origin comes
 * back as an opaque response instead of the network error a `cors` fetch
 * makes of it. That is what tells "the server answered" from "nothing did".
 * (`redirect: "manual"` would hide where a same-origin redirect went too.)
 * What it sends is a POST (`formMethod`), allowed in `no-cors`, and boost adds
 * no header to it.
 * @param {string} method - upper case: POST
 * @param {FormData} body
 * @returns {RequestInit}
 */
export function formRequest(method, body) {
  return { method, body, mode: "no-cors" };
}

/**
 * What a boosted navigation's fetch came to (#170).
 * - "page": an answer to show -- a 404 or a 500 included, under its URL
 * - "elsewhere": the server answered with a redirect to another origin, which
 *   only a `no-cors` request (`formRequest`) sees as such; its target is
 *   hidden from the page
 * - "aborted": the request was stopped (an `AbortError`), not failed: the page
 *   is going somewhere else, or the user stopped it. Nothing to do.
 * - "unanswered": the network, not the server -- no answer at all. A
 *   Chromium `window.stop()` lands here too: it rejects the fetch with the
 *   same `TypeError` a dropped connection does, and the two cannot be told
 *   apart.
 * @param {{response?: {type: string}, error?: unknown}} result - what the
 *   fetch resolved to, or what it rejected with
 * @returns {"page" | "elsewhere" | "aborted" | "unanswered"}
 */
export function fetchOutcome({ response, error }) {
  if (error !== undefined || response === undefined) {
    return /** @type {{name?: string} | null} */ (error)?.name === "AbortError" ? "aborted" : "unanswered";
  }
  return response.type === "opaque" || response.type === "opaqueredirect" ? "elsewhere" : "page";
}

/**
 * Whether a document the back/forward cache restored has to arrive at the
 * address bar again, as a popstate there would (#170). It froze as it was:
 * mid-navigation -- a fetch that handed over to the browser, a link away while
 * one was in flight -- the page on screen is not the one the address bar names
 * and `page.id` is null, and the navigation it waited for is gone. Or the
 * traversal moved past the entry it last showed. Only when nothing but the
 * fragment moved and the page is its own is there nothing to do. Chromium
 * never restores a live page (an open WebSocket keeps it out of the cache);
 * WebKit closes the socket and may.
 * @param {boolean} persisted - `PageTransitionEvent.persisted`
 * @param {PageOnScreen} page
 * @param {string} here - the address bar as of the last move the page saw
 * @param {string} at - the address bar now
 * @returns {boolean}
 */
export function arrivesOnRestore(persisted, page, here, at) {
  return persisted && (page.id === null || !onlyFragmentMoved(here, at));
}
