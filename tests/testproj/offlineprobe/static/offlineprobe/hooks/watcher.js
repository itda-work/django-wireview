/**
 * Counts its lifecycle callbacks on <html>, outside every component, so no
 * render can overwrite the count. A hook has to keep getting its callbacks
 * across a reconnect -- disconnected, reconnected, and then updated and
 * handleEvent as before (#110).
 */
window.wireview.hooks.Watcher = {
  mounted() {
    window.__watcher = this;
    this.handleEvent("nudge", (payload) => this.count(`nudge-${payload.n}`));
  },
  disconnected() {
    this.count("disconnected");
  },
  reconnected() {
    this.count("reconnected");
  },
  updated() {
    this.count("updated");
  },
  count(what) {
    const data = document.documentElement.dataset;
    const key = what.replace(/-(\w)/g, (_, c) => c.toUpperCase());
    data[key] = String(Number(data[key] || 0) + 1);
  },
};
