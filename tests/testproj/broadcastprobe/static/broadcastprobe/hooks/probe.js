/** For tests/test_broadcast_e2e.py: what a Broadcast pushed, written where a test can wait on it. */
window.wireview.hooks.BroadcastHeard = {
  mounted() {
    this.heard = 0;
    this.handleEvent("pinged", ({ posts }) => {
      this.heard += 1;
      this.el.textContent = `${this.heard}:${posts}`;
    });
  },
};
