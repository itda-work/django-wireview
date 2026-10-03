/**
 * @fileoverview Wireview boost module for client-side navigation.
 * Provides SPA-like navigation with morphing DOM updates.
 */

import { Idiomorph } from "idiomorph";
import { NavigationGate, crossesBoundary, readSessionName } from "./live-session.mjs";
import {
  NAVIGATION_FAILED_EVENT,
  arrivesOnRestore,
  fetchOutcome,
  formMethod,
  formRequest,
  isFragmentLink,
  isSameUrl,
  newPageId,
  onlyFragmentMoved,
  patchesPage,
  returnsToPatch,
  stamped,
} from "./navigation.mjs";
import { STREAM_ATTRIBUTE, isStreamContainer, pinContainerIds } from "./streams.mjs";
import { ValueGuard } from "./values.mjs";

/**
 * Callback function type for onBeforeElUpdated.
 * Called before an element is morphed, allowing attribute preservation.
 * @callback OnBeforeElUpdatedCallback
 * @param {Element} fromEl - The existing DOM element
 * @param {Element} toEl - The new element that will replace it
 * @returns {void}
 */

/**
 * The callbacks every morph runs before an element is updated, in the order
 * they were added. One slot that a second registration replaced let Alpine and
 * a project's own callback silently evict each other (#119).
 * @type {Set<OnBeforeElUpdatedCallback>}
 */
const beforeElUpdated = new Set();

/**
 * What the user typed survives a render (#91, #92). The rule and its
 * bookkeeping are in values.mjs; this is the page's one instance.
 */
const valueGuard = new ValueGuard();

// The fields an IME is composing in, which no render may write (#169). Capture,
// so a handler that stops the event cannot hide it. A composition lives in the
// focused field: one a script's write or a re-insertion dropped, without
// compositionend, is over once the field loses or regains focus.
document.addEventListener("compositionstart", (e) => valueGuard.compose(e.target, true), true);
document.addEventListener("compositionend", (e) => valueGuard.compose(e.target, false), true);
document.addEventListener("focusin", (e) => valueGuard.compose(e.target, false), true);
document.addEventListener("focusout", (e) => valueGuard.compose(e.target, false), true);

/** How many server changes are being applied now (a morph may run inside another). */
let applyingDepth = 0;

/**
 * Apply a change the server sent to the DOM -- a morph, a stream operation, a
 * removal. Events the browser dispatches meanwhile, like the blur of a focused
 * field it removes, are the change's and not the user's (events.mjs isRenderEcho).
 * @template T
 * @param {() => T} change
 * @returns {T}
 */
function applying(change) {
  applyingDepth += 1;
  try {
    return change();
  } finally {
    applyingDepth -= 1;
  }
}

/**
 * Whether a change from the server is being applied right now (see `applying`).
 * @returns {boolean}
 */
function inServerChange() {
  return applyingDepth > 0;
}

/**
 * The stream containers in `root` (itself included), in document order, with
 * the component each sits in.
 * @param {Element|DocumentFragment} root
 * @returns {{elements: Element[], spots: import("./streams.mjs").ContainerSpot[]}}
 */
function streamContainersIn(root) {
  const selector = `[${STREAM_ATTRIBUTE}]`;
  const elements = [...(root instanceof Element && root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];
  const spots = elements.map((element) => ({
    owner: element.closest("[wireview-component]")?.id ?? "",
    name: element.getAttribute(STREAM_ATTRIBUTE) ?? "",
    id: element.getAttribute("id") ?? "",
  }));
  return { elements, spots };
}

/**
 * The render to morph `oldNode` into, its stream containers carrying the ids
 * of the live ones they stand for, so the morph keeps those and moves them
 * rather than pairing them by position and removing them (streams.mjs
 * pinContainerIds). The HTML as it came when `oldNode` holds no stream.
 * @param {Element} oldNode
 * @param {Element|string} newNode
 * @returns {Element|DocumentFragment|string}
 */
function pinStreamContainers(oldNode, newNode) {
  if (typeof newNode !== "string") return newNode;
  const live = streamContainersIn(oldNode);
  if (!live.elements.length) return newNode;
  const template = document.createElement("template");
  template.innerHTML = newNode;
  const next = streamContainersIn(template.content);
  const pins = pinContainerIds(live.spots, next.spots);
  for (const [index, id] of pins.live) live.elements[index].setAttribute("id", id);
  for (const [index, id] of pins.next) next.elements[index].setAttribute("id", id);
  return template.content;
}

/**
 * Morphs an old DOM node into a new one using Idiomorph.
 * @param {Element} oldNode - The existing DOM element
 * @param {Element|string} newNode - The new content to morph into
 * @param {{permission?: Map<Element, string>, navigation?: boolean}} [options] - the fields
 *   this morph's render answers (ValueGuard.answer), which may take the server's value;
 *   `navigation` for a boosted page change, which keeps sticky components
 * @returns {Element[]} the sticky components a navigation kept as they were
 */
function morph(oldNode, newNode, { permission, navigation = false } = {}) {
  /** @type {WeakSet<Element>} */
  const kept = new WeakSet();
  /** @type {Element[]} */
  const stuck = [];
  const options = {
    callbacks: {
      beforeNodeMorphed(fromEl, toEl) {
        // A render carries the template's empty stream container. Morphing it
        // over the live one would delete every streamed item, so leave it alone.
        if (isStreamContainer(fromEl)) return false;

        // `wire-update="ignore"`: after its first render the element is the
        // page's -- a hook's, a widget's -- and no render touches it or what is
        // inside it, attributes included (Phoenix's phx-update="ignore", #102).
        if (fromEl.nodeType === Node.ELEMENT_NODE && fromEl.getAttribute("wire-update") === "ignore") return false;

        // A sticky component the next page has too (idiomorph pairs them by id) is
        // the same component: its element stays as it is, still live, so the join
        // that follows skips it and the server keeps the instance (#72). Only on a
        // navigation -- the component's own renders morph it as usual.
        if (navigation && fromEl.nodeType === Node.ELEMENT_NODE && fromEl.hasAttribute("wire-sticky")) {
          if (/** @type {Element} */ (toEl).id !== fromEl.id) return true;
          stuck.push(fromEl);
          return false;
        }

        if (fromEl.nodeType === Node.ELEMENT_NODE && valueGuard.keep(fromEl, toEl, permission)) kept.add(fromEl);

        // Only call for elements, not text nodes
        if (fromEl.nodeType === Node.ELEMENT_NODE) {
          for (const callback of beforeElUpdated) callback(fromEl, toEl);
        }
        return true; // Continue with morph
      },
      /**
       * @param {string} attributeName
       * @param {Element} element
       */
      beforeAttributeUpdated(attributeName, element) {
        // The value a field keeps is left alone; its attribute was synced above.
        return !(attributeName === "value" && kept.has(element));
      },
    },
  };

  // A component's render; a navigation's new page has new lists anyway
  const content = navigation ? newNode : pinStreamContainers(oldNode, newNode);
  applying(() => Idiomorph.morph(oldNode, content, options));
  return stuck;
}

/**
 * Add a callback that runs before each element is morphed.
 * @param {OnBeforeElUpdatedCallback} callback
 * @returns {() => void} removes this callback again
 */
function addBeforeElUpdated(callback) {
  beforeElUpdated.add(callback);
  return () => beforeElUpdated.delete(callback);
}

/** @type {boolean} */
const BOOST_PAGES = JSON.parse(
  /** @type {HTMLMetaElement|null} */ (document.querySelector("meta[name=wireview-boost]"))?.dataset.enabled || "false"
);

/**
 * Event target for navigation events.
 * Emits 'patched' when the URL moved inside the page and 'newContent' when a
 * navigation's page is on screen, which is when its URL reaches the server.
 */
class NavEvents extends EventTarget {
  /**
   * Dispatches a patched event: the URL moved inside the page on screen, and
   * nothing was fetched (navigation.mjs).
   */
  sendPatched() {
    this.dispatchEvent(new Event("patched"));
  }

  /**
   * Dispatches a newContent event: new components may be on the page.
   * @param {number} [token] - the navigation this content belongs to; none
   *   for a component's render that drew new ones
   * @param {boolean} [landed] - the navigation's own page, not a cached paint
   *   shown while it is fetched: what `wireview:navigated` announces, once
   * @param {Element[]} [stuck] - the sticky components the navigation's morph
   *   kept as they were (`morph`): what it carried across
   */
  sendNewContent(token, landed, stuck = []) {
    this.dispatchEvent(new CustomEvent("newContent", { detail: { token, landed, stuck } }));
  }
}

/** @type {NavEvents} */
let navEvent = new NavEvents();

/**
 * Which navigation the queued work belongs to. See `live-session.mjs`.
 * @type {NavigationGate}
 */
const navGate = new NavigationGate();

/**
 * The page on screen: its id, which the history entries it makes carry, and
 * the URL it was shown under. A popstate to one of those entries is a patch
 * (navigation.mjs). A load or a boosted navigation that lands gives a new one;
 * a navigation that fetches takes it away until then (`leavePage`).
 * @type {import("./navigation.mjs").PageOnScreen}
 */
const page = { id: newPageId(), url: document.location.href };
history.replaceState(stamped(history.state, page.id), document.title, document.location.href);

/**
 * The address bar as of the last move: one this module made, or a popstate.
 * A popstate that moved only the fragment from here is the browser's own
 * jump inside the document (navigation.mjs onlyFragmentMoved, #170).
 * @type {string}
 */
let here = document.location.href;

/**
 * `history.pushState`, keeping `here` up to date.
 * @param {any} state
 * @param {string} url
 */
function pushEntry(state, url) {
  history.pushState(state, document.title, url);
  here = document.location.href;
}

/**
 * `history.replaceState`, keeping `here` up to date.
 * @param {any} state
 * @param {string} url
 */
function replaceEntry(state, url) {
  history.replaceState(state, document.title, url);
  here = document.location.href;
}

/**
 * A navigation that fetches began. Until its page lands the screen holds the
 * page being left, or a cached copy of another one a popstate painted, and no
 * history entry is the page's own: a Forward to an entry the left page made is
 * fetched, and so is a push the left page's components send meanwhile.
 * @returns {string|null} the id of the page being left
 */
function leavePage() {
  const left = page.id;
  page.id = null;
  return left;
}

/**
 * The document is going away: a navigation the browser makes, for which it
 * stops the fetches in flight (Firefox rejects them with an `AbortError`). A
 * stopped boosted navigation then leaves history alone -- moving it would
 * take the browser's own navigation back (#170). A boosted one that begins
 * finds the document still here.
 * @type {boolean}
 */
let leavingDocument = false;
window.addEventListener("beforeunload", () => {
  leavingDocument = true;
});

/**
 * A stopped push going back to the entry it left (`HistoryCache.push`): the
 * popstate its `history.back()` brings is that, not a traversal to arrive
 * at -- unless another navigation began first.
 * @type {{token: number, entry: any, left: string|null} | null}
 */
let undoing = null;

/**
 * What a navigation that brought no page to show does instead (#170): the
 * entry is the navigation's already (a push made it, a popstate returned to
 * it), so the browser loads it there, and shows what went wrong under the URL
 * it went wrong for -- or follows the redirect to another origin a `cors`
 * fetch could not.
 * @param {string} url
 * @param {unknown} error - what the fetch rejected with, if it did
 */
function loadInstead(url, error) {
  if (error !== undefined) console.warn("wireview: could not fetch %s; loading it without boost", url, error);
  document.location.replace(url);
}

/**
 * A new page is on screen: entries the previous one made are another page's now.
 */
function landPage() {
  page.id = newPageId();
  page.url = document.location.href;
  replaceEntry(stamped(history.state, page.id), document.location.href);
}

// Set up click handler for boosted navigation
if (BOOST_PAGES) {
  document.addEventListener("click", (e) => {
    // A component handler ran first and called preventDefault: {% on "click.prevent" %}
    // on an <a href="#"> is a component event, not a navigation.
    if (e.defaultPrevented) return;

    const target = /** @type {HTMLElement} */ (e.target);
    /** @type {HTMLAnchorElement|null} */
    let link = /** @type {HTMLAnchorElement|null} */ (
      target?.tagName?.toLowerCase() !== "a" ? target?.closest("a") : target
    );
    if (
      link &&
      link.href &&
      (!link.target || link.target === "_self") &&
      link.origin == document.location.origin &&
      e.button === 0 && // left click only
      !e.metaKey && // open in new tab (mac)
      !e.ctrlKey && // open in new tab (win & linux)
      !e.altKey && // download
      !e.shiftKey
    ) {
      // `<a href="#section">` too: `load` hands a jump inside the document
      // back to the browser (#170)
      e.preventDefault();
      HistoryCache.load(link.href);
    }
  });

  // A form boosts only when it asks to (`wire-boost`). A link changes the page;
  // a form can change who is signed in, and a boosted navigation keeps the
  // socket -- a login or logout form that boosted would leave it speaking for
  // the old identity. So the form that knows it is safe opts in (#103).
  document.addEventListener("submit", (e) => {
    // A component's {% on "submit.prevent" %} handled it
    if (e.defaultPrevented) return;
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("wire-boost")) return;
    const submitter = /** @type {HTMLButtonElement|HTMLInputElement|null} */ (e.submitter);
    const target = submitter?.getAttribute("formtarget") ?? form.getAttribute("target");
    if (target && target !== "_self") return;
    const action = submitter?.getAttribute("formaction") ? submitter.formAction : form.action;
    // As the browser reads it: `method="put"` is a GET (#170)
    const method = formMethod(form.getAttribute("method"), submitter?.getAttribute("formmethod"));
    // A dialog's form closes its dialog and goes nowhere
    if (method === "dialog" || !hasSameOriginAsDocument(action)) return;
    e.preventDefault();
    HistoryCache.submit(action, method, new FormData(form, submitter ?? undefined));
  });
}

/**
 * The navigation in flight, or the last one. A hook records it when it mounts,
 * so the navigation that follows can tell it was already there (navigation.mjs).
 * @returns {number}
 */
function navigationToken() {
  return navGate.token;
}

/**
 * Replaces the document body content with morphing.
 * @param {Element|string} newBody - The new body content
 * @param {number} [scrollY] - Optional scroll position to restore
 * @param {boolean} [landed] - the navigation's own page; false for the cached
 *   page a popstate shows until the fetch answers
 */
function replaceBodyContent(newBody, scrollY = undefined, landed = true) {
  const token = navGate.token;
  window.requestAnimationFrame(() => {
    if (!navGate.accepts(token)) return;
    if (landed) landPage();
    const stuck = morph(document.body, newBody, { navigation: true });
    if (scrollY === undefined) {
      /** @type {HTMLElement|null} */ (document.querySelector("[autofocus]"))?.focus();
    } else {
      window.scrollTo(0, scrollY);
    }
    navEvent.sendNewContent(token, landed, stuck);
  });
}

/**
 * Checks if a URL has the same origin as the current document.
 * @param {string} url - The URL to check
 * @returns {boolean} True if same origin or relative URL
 */
function hasSameOriginAsDocument(url) {
  if (url.startsWith("http://") || url.startsWith("https://")) {
    return new URL(url).origin === document.location.origin;
  } else {
    return true;
  }
}

/**
 * Manages browser history with cached page content.
 * Enables fast back/forward navigation without server requests.
 */
class HistoryCache {
  /**
   * Loads a URL, using boost navigation if enabled.
   * @param {string} url - The URL to load
   * @param {{replace?: boolean, fetch?: boolean}} [options] - `replace`: take
   *   the current history entry's place instead of pushing a new one; `fetch`:
   *   fetch even a fragment of this document (`redirect_to` always fetches)
   * @returns {Promise<boolean>} False when the page is being replaced outright,
   *   which is also what leaving a live_session looks like.
   */
  static async load(url, { replace = false, fetch: always = false } = {}) {
    // A jump to a fragment of this document is the browser's, as a link's is
    // (#170): `wireview.visit("#top")` scrolls rather than fetching the page
    if (!always && isFragmentLink(document.location.href, url, document.baseURI)) {
      if (replace) {
        document.location.replace(url);
      } else {
        document.location.assign(url);
      }
      return true;
    }
    if (BOOST_PAGES && hasSameOriginAsDocument(url)) {
      return replace ? this.swap(url) : this.push(url);
    }
    if (replace) {
      document.location.replace(url);
    } else {
      document.location.assign(url);
    }
    return false;
  }

  /**
   * Navigates back in browser history.
   */
  static back() {
    window.history.back();
  }

  /**
   * Pushes a new URL to browser history and loads its content.
   * Saves current page state for back navigation.
   *
   * The cached entry records the live_session it was captured under, so a later
   * popstate can tell whether restoring it would carry a page from one boundary
   * into another.
   *
   * @param {string} path - The path to navigate to
   * @returns {Promise<boolean>} False when the navigation left the boundary and
   *   a full page load took over.
   */
  static async push(path) {
    // The browser makes no entry for a load of the URL it shows, and Back from
    // a second one would change nothing (#170): fetched in place instead
    if (isSameUrl(document.location.href, path, document.baseURI)) return this.swap(path);
    navGate.begin();
    leavingDocument = false;
    const left = leavePage();
    const entry = history.state;
    replaceEntry(
      {
        content: document.body.outerHTML,
        scrollY: window.scrollY,
        session: readSessionName(document),
      },
      document.location.href
    );
    pushEntry({}, path);
    return this.replaceContentFromUrl(path, undefined, "current", (outcome, error) => {
      if (outcome !== "aborted") return loadInstead(path, error);
      // Stopped, as the browser's stop button stops a load: the page that was
      // on screen is still there, so the address bar goes back to it, through
      // the entry it had (#170)
      if (leavingDocument) return;
      undoing = { token: navGate.token, entry, left };
      history.back();
    });
  }

  /**
   * Submits a form as a boosted navigation (`wire-boost`, #103).
   *
   * A GET goes where the browser would, with the fields as the query. A POST
   * is sent with fetch; the page it ends on -- a redirect's, as a form
   * should answer with (post/redirect/get) -- gets a history entry of its own.
   * An answer that did not redirect (a form re-rendered with its errors) stays
   * on the current URL: reloading it must not send the form again.
   *
   * If no page comes of it, the form is not sent again and the page stays as
   * it was, the address bar unmoved (#170). `wireview:navigation-failed` tells
   * the page which way: the network failed (`answered: false` -- the form may
   * have reached the server), or the server took it and redirected to another
   * origin (`answered: true`), whose address a boosted request cannot see
   * (`formRequest`). A form that redirects off the site is not one to boost.
   *
   * @param {string} action
   * @param {"get" | "post"} method - as the browser reads the form's (`formMethod`)
   * @param {FormData} data
   * @returns {Promise<boolean>} as `push`
   */
  static async submit(action, method, data) {
    if (method === "get") {
      const url = new URL(action, document.location.href);
      url.search = new URLSearchParams(/** @type {any} */ (data)).toString();
      return this.push(url.href);
    }
    navGate.begin();
    const left = leavePage();
    const entry = history.state;
    // What Back returns to, as `push` keeps it
    replaceEntry(
      {
        content: document.body.outerHTML,
        scrollY: window.scrollY,
        session: readSessionName(document),
      },
      document.location.href
    );
    const verb = method.toUpperCase();
    return this.replaceContentFromUrl(action, formRequest(verb, data), "push", (outcome, error) => {
      // Still the page that sent it, under its own entry
      page.id = left;
      replaceEntry(entry, document.location.href);
      if (outcome === "aborted") return;
      if (outcome === "unanswered") {
        console.warn(
          "wireview: could not submit to %s; the page stays as it was and the form is not sent again",
          action,
          error
        );
      }
      document.dispatchEvent(
        new CustomEvent(NAVIGATION_FAILED_EVENT, {
          detail: { url: action, method: verb, answered: outcome === "elsewhere" },
        })
      );
    });
  }

  /**
   * Loads a URL in place of the current history entry: `push` without the
   * entry it would leave behind, so there is no page to cache for back.
   * @param {string} path - The path to navigate to
   * @returns {Promise<boolean>} as `push`
   */
  static async swap(path) {
    navGate.begin();
    leavingDocument = false;
    const left = leavePage();
    const entry = history.state;
    const from = document.location.href;
    replaceEntry({}, path);
    return this.replaceContentFromUrl(path, undefined, "current", (outcome, error) => {
      if (outcome !== "aborted") return loadInstead(path, error);
      // Stopped: the page on screen is still the one it was leaving, and its
      // entry gets back its URL (#170)
      page.id = left;
      replaceEntry(entry, from);
    });
  }

  /**
   * Fetches content from a URL and replaces the body.
   *
   * This is where every boosted navigation meets: a link click, a popstate and a
   * server-sent redirect/push all end up here. So the boundary is checked here
   * and nowhere else -- a check on the click handler would miss the other two
   * (docs/design/live-session.md §3-3).
   *
   * The check runs on the *response*, not on the requested URL, so a redirect
   * chain that ends outside the boundary is caught by its final page. Nothing is
   * morphed and no `newContent` fires before it passes.
   *
   * @param {string} url - The URL to fetch content from
   * @param {RequestInit} [init] - a form's method and body (`submit`)
   * @param {"current"|"push"} [entry] - "current": the caller made the history
   *   entry, and a redirect only corrects its URL; "push": the entry is made
   *   here, for where the response ended, when it moved
   * @param {(outcome: "elsewhere" | "aborted" | "unanswered", error: unknown) => void} failed -
   *   what a request that brought no page to show does instead
   *   (navigation.mjs `fetchOutcome`, #170): `loadInstead`, or for a stopped
   *   one, what the browser's stop would leave. A stop abandons nothing the
   *   navigation queued: a cached page a popstate painted is the caller's
   * @returns {Promise<boolean>} False when the boundary was crossed or the
   *   request brought no page, and the browser is doing an ordinary page load
   *   instead -- or nothing, for a stopped one.
   */
  static async replaceContentFromUrl(url, init, entry, failed) {
    // The caller began the navigation; this reads the generation rather than
    // starting one, so the cached body a popstate queued belongs to the same
    // navigation as the fetch that validates it.
    const token = navGate.token;
    let response;
    let content = "";
    /** @type {unknown} */
    let error;
    try {
      response = await fetch(url, init);
      if (fetchOutcome({ response }) === "page") content = await response.text();
    } catch (e) {
      error = e;
    }
    const outcome = fetchOutcome({ response, error });
    if (outcome !== "page") {
      // An answer -- a 404, a 500 -- is a page, and shown under its URL like
      // any other. Without one the address bar names a page the screen does
      // not show. A later navigation has it now: the one superseded here
      // must not drag the page back to where it was going.
      if (!navGate.accepts(token)) return false;
      if (outcome === "elsewhere") {
        console.warn("wireview: %s redirected to another origin, which a boosted form cannot follow", url);
      }
      // Nothing queued for this navigation paints or joins any more -- but for
      // a stopped one, what it already painted is the caller's to keep
      if (outcome !== "aborted") navGate.abandon();
      failed(outcome, error);
      return false;
    }
    let doc = new DOMParser().parseFromString(content, "text/html");
    if (!navGate.accepts(token)) return false;
    if (crossesBoundary(readSessionName(document), readSessionName(doc))) {
      // Ends this navigation before handing over, so a cached body queued for
      // it neither paints nor joins its components while the browser is still
      // fetching the replacement document.
      navGate.abandon();
      // replace, not assign: the navigation already has its history entry (a push
      // made one, a popstate returned to one). assign added a second whenever a
      // redirect made the URL differ, and Back then led to the redirecting URL,
      // which redirected forward again (#110). A submitted form has no entry yet.
      if (entry === "push") {
        document.location.assign(response.url || url);
      } else {
        document.location.replace(response.url || url);
      }
      return false;
    }
    // The entry names the URL that was asked for. After a redirect the page is
    // another one, and a reload from the address bar would run the redirecting
    // view -- and whatever it does -- again (#104). Before `newLocation`, which
    // reads the params from the address bar. The state stays: after a popstate
    // it is the cached page Back returns to.
    if (entry === "push") {
      if (response.redirected && response.url) pushEntry({}, response.url);
    } else if (response.redirected && response.url) {
      replaceEntry(history.state, response.url);
    }
    // The server hears the new params once the page is on screen, between the
    // leaves of the old page's components and the joins of the new one's
    // (`newContent`). Telling it before the response was admitted had the old
    // page's components -- under the authentication the navigation was leaving
    // behind -- handle the destination's query (docs/design/live-session.md §3-3),
    // and before the morph the old page's components heard it at all (#170).
    document.title = doc.querySelector("title")?.text ?? "";
    replaceBodyContent(doc.body);
    return true;
  }

  /**
   * Whether the server's push or replace to `url` is a patch of the page on
   * screen (navigation.mjs patchesPage): resolved as `pushState` and `fetch`
   * resolve it, compared with the page shown rather than the address bar, and
   * never while a navigation is fetching.
   * @param {string} url
   * @returns {boolean}
   */
  static isPatch(url) {
    return patchesPage(page, url, document.baseURI);
  }

  /**
   * Moves to `url` inside the page on screen (#169): a new history entry, or
   * the current one rewritten, and nothing fetched. The components hear the
   * new params through `patched`. `url` must be a patch (`isPatch`); anything
   * else is `push` or `swap`.
   * @param {string} url
   * @param {{replace?: boolean}} [options] - rewrite the current entry instead of pushing one
   */
  static patch(url, { replace = false } = {}) {
    // A fetch still in flight lands no more: the address bar now names this page
    navGate.begin();
    if (replace) {
      replaceEntry(stamped({}, page.id), url);
    } else if (!isSameUrl(document.location.href, url, document.baseURI)) {
      replaceEntry(stamped(history.state, page.id), document.location.href);
      pushEntry(stamped({}, page.id), url);
    }
    // A push to the URL on screen makes no entry, as Phoenix's makes none,
    // and the components still hear their params, as handle_params runs (#170)
    navEvent.sendPatched();
  }

  /**
   * Replaces the current URL without navigation or a word to the server: the
   * server already knows the params it asked for (`set_query_string`).
   * @param {string} path - The new path
   */
  static replace(path) {
    replaceEntry(page.id === null ? {} : stamped({}, page.id), path);
  }
}

/**
 * The page arrives at the history entry the address bar names: a popstate, or
 * a document the back/forward cache restored (`pageshow`).
 * @param {any} state - the entry's
 * @param {{restored?: boolean}} [options] - a restored document: whatever it
 *   showed when it froze, it arrives, even under the same URL
 */
function arrive(state, { restored = false } = {}) {
  const from = here;
  here = document.location.href;
  // Only the fragment moved: a jump inside the document, the browser's own --
  // a fragment link's new entry, or Back from it. Nothing to fetch, no params
  // to tell, as Phoenix ignores such a popstate (#170). The entry needs no
  // stamp of its own: a patch away from it stamps it first.
  if (!restored && onlyFragmentMoved(from, here)) return;
  // An entry the page on screen made: it has what it needs, and only the
  // params changed. Before the boundary check, which a page cannot fail with
  // itself.
  if (returnsToPatch(state, document.location.href, page)) {
    navGate.begin();
    navEvent.sendPatched();
    return;
  }
  // The cached body is morphed in a requestAnimationFrame while the fetch below
  // is still in flight, so the boundary has to be settled before the morph is
  // even scheduled: by the time the fetch answers, the cached DOM is on screen.
  if (state?.content !== undefined && crossesBoundary(readSessionName(document), state.session)) {
    // Abandon before handing over: `reload()` does not stop the JavaScript that
    // is already running, so a fetch still in flight from an earlier navigation
    // would otherwise resolve and morph -- and join -- while the browser is
    // fetching the replacement document.
    navGate.abandon();
    document.location.reload();
    return;
  }
  const token = navGate.begin();
  leavingDocument = false;
  leavePage();
  const url = document.location.href;
  const cached = state?.content !== undefined;
  if (cached) {
    // The entry's own name matched, but that was true when it was captured; the
    // fetch below may still find the URL has moved. Showing the cache meanwhile
    // is the point of the cache, and the fetch bumps the generation, so a
    // refused destination drops this paint instead of flashing it.
    replaceBodyContent(state.content, state.scrollY, false);
  }
  HistoryCache.replaceContentFromUrl(url, undefined, "current", (outcome, error) => {
    if (outcome !== "aborted") return loadInstead(url, error);
    // Stopped. The traversal is history's already, and how far it went is not
    // the page's to know, so the address bar stays (#170). The cached copy it
    // painted is that entry's page: it lands, after the paint if that is still
    // to come. Without one the screen holds another page, and the browser
    // loads the entry -- unless it is leaving the document anyway.
    if (cached) {
      window.requestAnimationFrame(() => {
        if (!navGate.accepts(token)) return;
        landPage();
        navEvent.sendNewContent(token, true);
      });
    } else {
      navGate.abandon();
      if (!leavingDocument) document.location.replace(url);
    }
  });
}

window.addEventListener("popstate", (event) => {
  const undo = undoing;
  undoing = null;
  if (undo && navGate.accepts(undo.token)) {
    // Back on the entry the stopped push left, where the page on screen was
    page.id = undo.left;
    replaceEntry(undo.entry, document.location.href);
    return;
  }
  arrive(event.state);
});

// A document the back/forward cache restored froze with whatever it had: a
// navigation that handed over to the browser left it with no page id and an
// address bar it never reached (navigation.mjs arrivesOnRestore, #170). A
// popstate that follows the restore finds `here` already moved, and does
// nothing more.
window.addEventListener("pageshow", (event) => {
  if (arrivesOnRestore(event.persisted, page, here, document.location.href)) {
    arrive(history.state, { restored: true });
  }
});

/**
 * @typedef {Object} BoostExports
 * @property {typeof HistoryCache} HistoryCache - History management class
 * @property {typeof morph} morph - DOM morphing function
 * @property {typeof applying} applying - Apply a server change to the DOM
 * @property {typeof inServerChange} inServerChange - Whether a server change is being applied now
 * @property {NavEvents} navEvent - Navigation event emitter
 * @property {typeof addBeforeElUpdated} addBeforeElUpdated - Add a morph callback
 * @property {typeof navigationToken} navigationToken - The navigation in flight
 */

/** @type {BoostExports} */
export default {
  HistoryCache: HistoryCache,
  morph: morph,
  applying: applying,
  inServerChange: inServerChange,
  valueGuard: valueGuard,
  navEvent: navEvent,
  addBeforeElUpdated: addBeforeElUpdated,
  navigationToken: navigationToken,
};
