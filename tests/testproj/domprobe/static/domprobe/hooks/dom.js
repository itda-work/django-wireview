/**
 * The hook tests/test_dom_events_e2e.py presses: a click pushes the event its
 * element names with the x it carries, and the reply goes on <html>, which no
 * component's render overwrites.
 */

window.wireview.hooks.DomPush = {
  mounted() {
    this.el.addEventListener("click", async () => {
      const reply = await this.pushEvent(this.el.dataset.event, { x: Number(this.el.dataset.x) });
      document.documentElement.setAttribute("data-dom-reply", JSON.stringify(reply));
    });
  },
};

window.wireview.hooks.DomOther = {};
