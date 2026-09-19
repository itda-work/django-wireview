/**
 * @fileoverview Wireview boost module for client-side navigation.
 * Provides SPA-like navigation with morphing DOM updates.
 */

import { Idiomorph } from "idiomorph";
import { NavigationGate, crossesBoundary, readSessionName } from "./live-session.mjs";
import { isStreamContainer } from "./streams.mjs";
import { isEditableField, keepsUserValue } from "./values.mjs";

/**
 * Callback function type for onBeforeElUpdated.
 * Called before an element is morphed, allowing attribute preservation.
 * @callback OnBeforeElUpdatedCallback
 * @param {Element} fromEl - The existing DOM element
 * @param {Element} toEl - The new element that will replace it
 * @returns {void}
 */

/**
 * Global configuration for morph callbacks.
 * @type {{onBeforeElUpdated: OnBeforeElUpdatedCallback|null}}
 */
const morphConfig = {
  onBeforeElUpdated: null,
};

/**
 * What the user typed survives a render (#91, #92; values.mjs has the rule,
 * docs/design/input-values.md the reasoning).
 *
 * A committing action records its fields with the values it sent, under the
 * event's `ref`. Only the render that carries that `ref` opens them to the
 * server's value, and then only the fields the user has not typed in since.
 * The marks close when that component's morph is done, whichever fields it
 * visited, so no mark is left for an unrelated render to use later.
 *
 * A server too old to echo refs gets the #91 behaviour: the fields are marked
 * without a ref and the first morph that touches them takes the server value.
 */
const valueGuard = {
  /** @type {Map<number, Map<Element, string>>} fields and the values sent, per ref */
  pending: new Map(),
  /** @type {Map<Element, string>} fields a render just answered, until their component's morph */
  answered: new Map(),
  /** @type {Set<Element>} fields marked without a ref (a server that does not echo refs) */
  unpaired: new Set(),

  /**
   * The editable fields an action from `element` commits: the element, or
   * inside a form the form's fields, with their values as sent.
   * @param {Element} element
   * @returns {Map<Element, string>}
   */
  fieldsOf(element) {
    const form = element instanceof HTMLFormElement ? element : element.closest("form");
    const fields = form ? Array.from(form.elements) : [element];
    const result = new Map();
    for (const field of fields) {
      if (isEditableField(field.tagName, /** @type {HTMLInputElement} */ (field).type)) {
        result.set(field, /** @type {HTMLInputElement} */ (field).value);
      }
    }
    return result;
  },

  /**
   * @param {number | null} ref - the event's ref, or null when the server does not echo refs
   * @param {Map<Element, string>} fields
   */
  commit(ref, fields) {
    if (ref === null) {
      for (const field of fields.keys()) this.unpaired.add(field);
    } else {
      this.pending.set(ref, fields);
    }
  },

  /**
   * The render answering `ref` arrived. When it will morph its component, its
   * fields may take the server's value in that morph; when it will not (no
   * change, or only children changed), the server's value for them is what
   * the page already shows, and the marks simply go.
   * @param {number} ref
   * @param {boolean} morphing
   */
  answer(ref, morphing) {
    const fields = this.pending.get(ref);
    this.pending.delete(ref);
    if (!fields || !morphing) return;
    for (const [field, sent] of fields) this.answered.set(field, sent);
  },

  /**
   * A component's morph is done, or its answer changed nothing: close every
   * mark under it, visited or not (a stream container is never visited).
   * @param {Element} root
   */
  settle(root) {
    for (const field of this.answered.keys()) {
      if (!field.isConnected || root.contains(field)) this.answered.delete(field);
    }
    for (const field of this.unpaired) {
      if (!field.isConnected || root.contains(field)) this.unpaired.delete(field);
    }
  },

  /**
   * Decide for one field about to be morphed. When it keeps the user's value,
   * the server's value still goes to the attribute (`defaultValue`): a field
   * the user edited does not change what it shows when its attribute changes,
   * and the next render compares against what the server actually sent.
   * @param {Element} fromEl
   * @param {Element} toEl
   * @returns {boolean} true when the value must not be touched
   */
  keep(fromEl, toEl) {
    const field = /** @type {HTMLInputElement|HTMLTextAreaElement} */ (fromEl);
    const next = /** @type {HTMLInputElement|HTMLTextAreaElement} */ (toEl);
    if (!isEditableField(field.tagName, field.type) || field.tagName !== next.tagName) return false;
    const sent = this.answered.get(field);
    // The answer covers what was sent; keystrokes after that are the user's.
    const committing = (sent !== undefined && sent === field.value) || this.unpaired.delete(field);
    const keep = keepsUserValue({
      edited: field.value !== field.defaultValue,
      focused: field === document.activeElement,
      committing,
      serverChanged: next.defaultValue !== field.defaultValue,
    });
    if (keep) field.defaultValue = next.defaultValue;
    return keep;
  },
};

/**
 * Morphs an old DOM node into a new one using Idiomorph.
 * @param {Element} oldNode - The existing DOM element
 * @param {Element|string} newNode - The new content to morph into
 */
function morph(oldNode, newNode) {
  const callback = morphConfig.onBeforeElUpdated;
  /** @type {WeakSet<Element>} */
  const kept = new WeakSet();
  const options = {
    callbacks: {
      beforeNodeMorphed(fromEl, toEl) {
        // A render carries the template's empty stream container. Morphing it
        // over the live one would delete every streamed item, so leave it alone.
        if (isStreamContainer(fromEl)) return false;

        if (fromEl.nodeType === Node.ELEMENT_NODE && valueGuard.keep(fromEl, toEl)) kept.add(fromEl);

        // Only call for elements, not text nodes
        if (callback && fromEl.nodeType === Node.ELEMENT_NODE) {
          callback(fromEl, toEl);
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
 * Set the onBeforeElUpdated callback.
 * This callback is called before each element is morphed, allowing you to
 * preserve attributes or state from the old element to the new one.
 *
 * @param {OnBeforeElUpdatedCallback|null} callback - The callback function
 *
 * @example
 * // Preserve data-js-* attributes
 * setOnBeforeElUpdated((fromEl, toEl) => {
 *   for (const attr of fromEl.attributes) {
 *     if (attr.name.startsWith('data-js-')) {
 *       toEl.setAttribute(attr.name, attr.value);
 *     }
 *   }
 * });
 */
function setOnBeforeElUpdated(callback) {
  morphConfig.onBeforeElUpdated = callback;
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
   */
  sendNewContent() {
    this.dispatchEvent(new Event("newContent"));
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
}

/**
 * Replaces the document body content with morphing.
 * @param {Element|string} newBody - The new body content
 * @param {number} [scrollY] - Optional scroll position to restore
 */
function replaceBodyContent(newBody, scrollY = undefined) {
  const token = navGate.token;
  window.requestAnimationFrame(() => {
    if (!navGate.accepts(token)) return;
    morph(document.body, newBody);
    if (scrollY === undefined) {
      /** @type {HTMLElement|null} */ (document.querySelector("[autofocus]"))?.focus();
    } else {
      window.scrollTo(0, scrollY);
    }
    navEvent.sendNewContent();
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
   * @returns {Promise<boolean>} False when the page is being replaced outright,
   *   which is also what leaving a live_session looks like.
   */
  static async load(url) {
    if (BOOST_PAGES && hasSameOriginAsDocument(url)) {
      return this.push(url);
    }
    document.location.assign(url);
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
   * @returns {Promise<boolean>} False when the boundary was crossed and the
   *   browser is doing an ordinary page load instead.
   */
  static async replaceContentFromUrl(url) {
    // The caller began the navigation; this reads the generation rather than
    // starting one, so the cached body a popstate queued belongs to the same
    // navigation as the fetch that validates it.
    const token = navGate.token;
    let response = await fetch(url);
    let content = await response.text();
    let doc = new DOMParser().parseFromString(content, "text/html");
    if (!navGate.accepts(token)) return false;
    if (crossesBoundary(readSessionName(document), readSessionName(doc))) {
      // Ends this navigation before handing over, so a cached body queued for
      // it neither paints nor joins its components while the browser is still
      // fetching the replacement document.
      navGate.abandon();
      document.location.assign(response.url || url);
      return false;
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
    replaceBodyContent(event.state.content, event.state.scrollY);
  }
  HistoryCache.replaceContentFromUrl(document.location.href);
});

/**
 * @typedef {Object} BoostExports
 * @property {typeof HistoryCache} HistoryCache - History management class
 * @property {typeof morph} morph - DOM morphing function
 * @property {NavEvents} navEvent - Navigation event emitter
 * @property {typeof setOnBeforeElUpdated} setOnBeforeElUpdated - Configure morph callback
 */

/** @type {BoostExports} */
export default {
  HistoryCache: HistoryCache,
  morph: morph,
  valueGuard: valueGuard,
  navEvent: navEvent,
  setOnBeforeElUpdated: setOnBeforeElUpdated,
};
