/**
 * Keeps its instance where the test can reach it. A hook outlives a dropped
 * connection and its pushEvent goes straight to the socket, so it is the path
 * by which messages were queued while disconnected (#97).
 */
window.wireview.hooks.Pinger = {
  mounted() {
    window.__pinger = this;
  },
};
