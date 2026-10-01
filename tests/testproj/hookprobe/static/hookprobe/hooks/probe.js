/**
 * Hooks for tests/test_hooks_e2e.py. What they record goes on <html>, which a
 * boost navigation keeps and no component's render overwrites, so a test can
 * wait on it with a Playwright assertion.
 */

window.__askers = window.__askers || {};

/** Counts a callback on <html> as data-<callback>-<data-who>. */
function count(callback, who) {
  const html = document.documentElement;
  const name = `data-${callback}-${who}`;
  html.setAttribute(name, String(Number(html.getAttribute(name) || "0") + 1));
}

window.wireview.hooks.ProbeLog = {
  mounted() {
    count("mounted", this.el.dataset.who);
    this.handleEvent("pinged", () => count("pinged", this.el.dataset.who));
  },
  destroyed() {
    count("destroyed", this.el.dataset.who);
  },
};

/** Lets a test call pushEvent on the hook of a named component. */
window.wireview.hooks.ProbeAsker = {
  mounted() {
    window.__askers[this.el.dataset.who] = this;
  },
};

/** Counts the `lit` event the sprout's update() pushes to the hook its parent's patch draws. */
window.wireview.hooks.ProbeLit = {
  mounted() {
    count("mounted", this.el.dataset.who);
    this.handleEvent("lit", () => count("lit", this.el.dataset.who));
  },
};
