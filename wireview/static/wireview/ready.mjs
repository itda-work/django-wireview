/**
 * "Every deferred script has run" — the gate the first join waits behind.
 *
 * Deferred scripts run in document order, before `DOMContentLoaded`. This
 * bundle is one of them and it opens the socket the moment it executes, while
 * the page's *own* deferred scripts have not run yet. A page that registers a
 * JavaScript hook from such a script would be racing the handshake, and losing
 * that race is silent: the hook is simply "not registered" when the first
 * render arrives, and the only trace is a console warning.
 *
 * Two things make this harder than one `readyState` check, and both were found
 * by breaking it:
 *
 * 1. **`readyState` cannot answer the question.** It turns `"interactive"` when
 *    parsing finishes, which is *before* the deferred scripts run, and stays
 *    that way until `load`. So it reads the same during the window that must be
 *    waited out and during the window that must not be. Reading it is only
 *    conclusive at the two ends: `"loading"` is certainly before, `"complete"`
 *    is certainly after.
 * 2. **The answer has to be captured when the bundle runs, not when it is
 *    needed.** The socket usually opens *after* `DOMContentLoaded` has already
 *    fired; asking then would find `"interactive"`, subscribe to an event that
 *    is never coming again, and hold every join until `load` — which waits for
 *    images. So the listener is registered at module scope, while this bundle
 *    is still inside the deferred phase, and later callers read a flag.
 *
 * `load` stays as the way out for a bundle that really was injected after
 * `DOMContentLoaded`, where the flag would otherwise never flip.
 *
 * Pure of globals so `tests/js/` can check it without a DOM.
 */

/**
 * @typedef {{ readyState: string, addEventListener: Function }} ReadyDocument
 * @typedef {{ addEventListener: Function }} ReadyWindow
 */

/**
 * Build the gate. Call once, as early as the bundle runs.
 *
 * @param {ReadyDocument} doc
 * @param {ReadyWindow} win
 * @returns {(fn: () => void) => void} Runs `fn` now if the document is past its
 *   deferred scripts, and otherwise queues it in call order.
 */
export function createDocumentReady(doc, win) {
  let done = doc.readyState === "complete";
  /** @type {Array<() => void>} */
  const waiting = [];

  const flush = () => {
    if (done) return;
    done = true;
    while (waiting.length) {
      const fn = waiting.shift();
      if (fn) fn();
    }
  };

  if (!done) {
    doc.addEventListener("DOMContentLoaded", flush, { once: true });
    win.addEventListener("load", flush, { once: true });
  }

  return (fn) => {
    if (done) fn();
    else waiting.push(fn);
  };
}
