/**
 * Hook definitions for the `hooks` example.
 *
 * This file runs after wireview's own bundle, which is what `defer` on both
 * script tags buys: deferred scripts run in document order. `window.wireview`
 * therefore exists by the time this line does.
 *
 * Registering a hook is one assignment. Nothing else knows the name: the
 * template's `wire-hook="Timeago"` and the key here are the whole contract,
 * which is also why forgetting this file produces a console warning and no
 * other symptom (GAP-032, #71).
 */

/**
 * Counters live outside the component on purpose.
 *
 * Anything a hook writes inside the component is overwritten the next time the
 * server renders: the server does not know these numbers, so its HTML wins the
 * morph. State the server has never heard of belongs outside the component --
 * or in the hook instance, which is what `this` is for.
 *
 * @param {string} name
 */
function bump(name) {
  const el = document.querySelector(`[data-testid="count-${name}"]`);
  if (el) el.textContent = String(Number(el.textContent || "0") + 1);
}

/** @param {string} testid @param {string} text */
function write(testid, text) {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (el) el.textContent = text;
}

window.wireview.hooks.Timeago = {
  mounted() {
    bump("mounted");
    this.show();
    // A timer is the usual reason a hook needs `destroyed()`: nothing else will
    // stop it when the element goes away.
    this.timer = setInterval(() => this.show(), 1000);

    // What the server pushes with `push_event` arrives here.
    this.handleEvent("highlight", ({ text }) => write("pushed", text));
  },

  beforeUpdate() {
    // Runs *before* the morph, synchronously: the place to read anything the
    // morph is about to destroy (scroll offset, selection, an open dropdown).
    bump("beforeUpdate");
  },

  updated() {
    // The server has just rewritten this element's content, including the text
    // `show()` had put there. Re-applying it is the hook's job.
    bump("updated");
    this.show();
  },

  destroyed() {
    bump("destroyed");
    clearInterval(this.timer);
  },

  /** Render the server's instant the way this browser's clock sees it. */
  show() {
    const at = Date.parse(this.el.dataset.at);
    if (Number.isNaN(at)) return;
    const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
    this.el.textContent = `${seconds}초 전`;
  },
};

window.wireview.hooks.Noter = {
  mounted() {
    this.el.addEventListener("click", () => {
      // The third argument is a callback. Without it the server's return value
      // is dropped -- `pushEvent` is one-way unless something is waiting.
      this.pushEvent("noted", { from: "Noter" }, (reply) => {
        write("reply", String(reply.count));
      });
    });
  },
};
