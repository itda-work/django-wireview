/**
 * @fileoverview Wireview boost module for client-side navigation.
 * Provides SPA-like navigation with morphing DOM updates.
 */

import { Idiomorph } from "idiomorph";
import { NavigationGate, crossesBoundary, readSessionName } from "./live-session.mjs";
import { isStreamContainer } from "./streams.mjs";
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

/**
 * Morphs an old DOM node into a new one using Idiomorph.
 * @param {Element} oldNode - The existing DOM element
 * @param {Element|string} newNode - The new content to morph into
 * @param {{permission?: Map<Element, string>, navigation?: boolean}} [options] - the fields
 *   this morph's render answers (ValueGuard.answer), which may take the server's value;
 *   `navigation` for a boosted page change, which keeps sticky components
 */
function morph(oldNode, newNode, { permission, navigation = false } = {}) {
  /** @type {WeakSet<Element>} */
  const kept = new WeakSet();
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
          return /** @type {Element} */ (toEl).id !== fromEl.id;
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

  Idiomorph.morph(oldNode, newNode, options);
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
 * Emits 'newLocation' when URL changes and 'newContent' when DOM updates.
 */
class NavEvents extends EventTarget {
  /**
   * Dispatches a newLocation event.
   */
  sendNewLocation() {
    this.dispatchEvent(new Event("newLocation"));
  }

  /**
   * Dispatches a newContent event.
   * @param {number} token - the navigation this content belongs to
   * @param {boolean} landed - the navigation's own page, not a cached paint
   *   shown while it is fetched: what `wireview:navigated` announces, once
   */
  sendNewContent(token, landed) {
    this.dispatchEvent(new CustomEvent("newContent", { detail: { token, landed } }));
  }
}

/** @type {NavEvents} */
let navEvent = new NavEvents();

/**
 * Which navigation the queued work belongs to. See `live-session.mjs`.
 * @type {NavigationGate}
 */
const navGate = new NavigationGate();

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
    const method = (submitter?.getAttribute("formmethod") || form.getAttribute("method") || "get").toLowerCase();
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
    morph(document.body, newBody, { navigation: true });
    if (scrollY === undefined) {
      /** @type {HTMLElement|null} */ (document.querySelector("[autofocus]"))?.focus();
    } else {
      window.scrollTo(0, scrollY);
    }
    navEvent.sendNewContent(token, landed);
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
   * @param {{replace?: boolean}} [options] - take the current history entry's
   *   place instead of pushing a new one
   * @returns {Promise<boolean>} False when the page is being replaced outright,
   *   which is also what leaving a live_session looks like.
   */
  static async load(url, { replace = false } = {}) {
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
    if (document.body == null) debugger;
    navGate.begin();
    history.replaceState(
      {
        content: document.body.outerHTML,
        scrollY: window.scrollY,
        session: readSessionName(document),
      },
      document.title,
      document.location.href
    );
    history.pushState({}, document.title, path);
    return this.replaceContentFromUrl(path);
  }

  /**
   * Submits a form as a boosted navigation (`wire-boost`, #103).
   *
   * A GET goes where the browser would, with the fields as the query. Anything
   * else is sent with fetch; the page it ends on -- a redirect's, as a form
   * should answer with (post/redirect/get) -- gets a history entry of its own.
   * An answer that did not redirect (a form re-rendered with its errors) stays
   * on the current URL: reloading it must not send the form again.
   *
   * @param {string} action
   * @param {string} method - lower case
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
    // What Back returns to, as `push` keeps it
    history.replaceState(
      {
        content: document.body.outerHTML,
        scrollY: window.scrollY,
        session: readSessionName(document),
      },
      document.title,
      document.location.href
    );
    return this.replaceContentFromUrl(action, { method: method.toUpperCase(), body: data }, "push");
  }

  /**
   * Loads a URL in place of the current history entry: `push` without the
   * entry it would leave behind, so there is no page to cache for back.
   * @param {string} path - The path to navigate to
   * @returns {Promise<boolean>} as `push`
   */
  static async swap(path) {
    navGate.begin();
    history.replaceState({}, document.title, path);
    return this.replaceContentFromUrl(path);
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
   * @returns {Promise<boolean>} False when the boundary was crossed and the
   *   browser is doing an ordinary page load instead.
   */
  static async replaceContentFromUrl(url, init = undefined, entry = "current") {
    // The caller began the navigation; this reads the generation rather than
    // starting one, so the cached body a popstate queued belongs to the same
    // navigation as the fetch that validates it.
    const token = navGate.token;
    let response = await fetch(url, init);
    let content = await response.text();
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
      if (response.redirected && response.url) history.pushState({}, document.title, response.url);
    } else if (response.redirected && response.url) {
      history.replaceState(history.state, document.title, response.url);
    }
    // Only now. `newLocation` is what makes the client tell the server its new
    // params, and announcing it before the response was admitted had the old
    // page's components -- under the authentication the navigation was leaving
    // behind -- handle the destination's query (docs/design/live-session.md §3-3).
    navEvent.sendNewLocation();
    document.title = doc.querySelector("title")?.text ?? "";
    replaceBodyContent(doc.body);
    return true;
  }

  /**
   * Replaces the current URL without navigation.
   * @param {string} path - The new path
   */
  static replace(path) {
    history.replaceState({}, document.title, path);
  }
}

window.addEventListener("popstate", (event) => {
  // The cached body is morphed in a requestAnimationFrame while the fetch below
  // is still in flight, so the boundary has to be settled before the morph is
  // even scheduled: by the time the fetch answers, the cached DOM is on screen.
  if (event.state?.content !== undefined && crossesBoundary(readSessionName(document), event.state.session)) {
    // Abandon before handing over: `reload()` does not stop the JavaScript that
    // is already running, so a fetch still in flight from an earlier navigation
    // would otherwise resolve and morph -- and join -- while the browser is
    // fetching the replacement document.
    navGate.abandon();
    document.location.reload();
    return;
  }
  navGate.begin();
  if (event.state?.content !== undefined) {
    // The entry's own name matched, but that was true when it was captured; the
    // fetch below may still find the URL has moved. Showing the cache meanwhile
    // is the point of the cache, and the fetch bumps the generation, so a
    // refused destination drops this paint instead of flashing it.
    replaceBodyContent(event.state.content, event.state.scrollY, false);
  }
  HistoryCache.replaceContentFromUrl(document.location.href);
});

/**
 * @typedef {Object} BoostExports
 * @property {typeof HistoryCache} HistoryCache - History management class
 * @property {typeof morph} morph - DOM morphing function
 * @property {NavEvents} navEvent - Navigation event emitter
 * @property {typeof addBeforeElUpdated} addBeforeElUpdated - Add a morph callback
 * @property {typeof navigationToken} navigationToken - The navigation in flight
 */

/** @type {BoostExports} */
export default {
  HistoryCache: HistoryCache,
  morph: morph,
  valueGuard: valueGuard,
  navEvent: navEvent,
  addBeforeElUpdated: addBeforeElUpdated,
  navigationToken: navigationToken,
};
