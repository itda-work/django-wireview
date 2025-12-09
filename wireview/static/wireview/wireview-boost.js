/**
 * @fileoverview Wireview boost module for client-side navigation.
 * Provides SPA-like navigation with morphing DOM updates.
 */

import { Idiomorph } from "idiomorph";

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
 * Morphs an old DOM node into a new one using Idiomorph.
 * @param {Element} oldNode - The existing DOM element
 * @param {Element|string} newNode - The new content to morph into
 */
function morph(oldNode, newNode) {
  const options = {};

  // Add beforeNodeMorphed callback if configured
  if (morphConfig.onBeforeElUpdated) {
    const callback = morphConfig.onBeforeElUpdated;
    options.callbacks = {
      beforeNodeMorphed(fromEl, toEl) {
        // Only call for elements, not text nodes
        if (fromEl.nodeType === Node.ELEMENT_NODE) {
          callback(fromEl, toEl);
        }
        return true; // Continue with morph
      },
    };
  }

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

console.log("BOOST_PAGES", BOOST_PAGES);

/**
 * Event target for navigation events.
 * Emits 'newLocation' when URL changes and 'newContent' when DOM updates.
 */
class NavEvents extends EventTarget {
  /**
   * Dispatches a newLocation event.
   */
  sendNewLocation() {
    console.log("LOAD", document.location.href);
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

// Set up click handler for boosted navigation
if (BOOST_PAGES) {
  document.addEventListener("click", (e) => {
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
  window.requestAnimationFrame(() => {
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
   */
  static async load(url) {
    if (BOOST_PAGES) {
      // this._saveCurrentPage();
      if (hasSameOriginAsDocument(url)) {
        this.push(url);
      } else {
        document.location.assign(url);
      }
    } else {
      document.location.assign(url);
    }
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
   * @param {string} path - The path to navigate to
   */
  static async push(path) {
    if (document.body == null) debugger;
    history.replaceState(
      {
        content: document.body.outerHTML,
        scrollY: window.scrollY,
      },
      document.title,
      document.location.href
    );
    history.pushState({}, document.title, path);
    this.replaceContentFromUrl(path);
  }

  /**
   * Fetches content from a URL and replaces the body.
   * @param {string} url - The URL to fetch content from
   */
  static async replaceContentFromUrl(url) {
    navEvent.sendNewLocation();
    let response = await fetch(url);
    let content = await response.text();
    let doc = new DOMParser().parseFromString(content, "text/html");
    document.title = doc.querySelector("title")?.text ?? "";
    replaceBodyContent(doc.body);
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
  navEvent.sendNewLocation();
  if (event.state?.content !== undefined) {
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
  navEvent: navEvent,
  setOnBeforeElUpdated: setOnBeforeElUpdated,
};
