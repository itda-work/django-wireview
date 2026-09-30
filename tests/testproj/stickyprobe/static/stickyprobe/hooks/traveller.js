/**
 * Hooks for tests/test_sticky_e2e.py (#128). What they record goes on <html>,
 * which a boosted navigation keeps, so a test can wait on it.
 */

/** Counts a callback on <html> as data-<callback>-<data-who>. */
function countOnHtml(callback, who) {
  const html = document.documentElement;
  const name = `data-${callback}-${who}`;
  html.setAttribute(name, String(Number(html.getAttribute(name) || "0") + 1));
}

/**
 * A sticky component's hook with a page-wide effect: a class on <body>. The
 * next page's <body> does not have it, and `navigated()` is how the hook
 * learns it has to put it back.
 */
window.wireview.hooks.StickyTraveller = {
  mounted() {
    countOnHtml("mounted", this.el.dataset.who);
    document.body.classList.add(`with-${this.el.dataset.who}`);
  },
  navigated() {
    countOnHtml("navigated", this.el.dataset.who);
    document.body.classList.add(`with-${this.el.dataset.who}`);
  },
  destroyed() {
    countOnHtml("destroyed", this.el.dataset.who);
  },
};

// Page code hears the same moment
document.addEventListener("wireview:navigated", (event) => {
  countOnHtml("navigated", "document");
  const html = document.documentElement;
  html.dataset.navigatedUrl = new URL(event.detail.url).pathname;
  html.dataset.navigatedFrom = new URL(event.detail.previousUrl).pathname;
});
