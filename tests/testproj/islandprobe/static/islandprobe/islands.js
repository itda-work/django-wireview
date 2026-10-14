/**
 * The probe island: draws its props, keeps a node the test can tell apart, and
 * logs what it is asked. `window.__islandLog` lists [what, who, props].
 */
window.__islandLog = window.__islandLog || [];
window.__islands = window.__islands || {};

window.wireview.islands.Probe = {
  mount(el, props, ctx) {
    const who = ctx.el.dataset.who;
    const log = (what, value) => window.__islandLog.push([what, who, value ?? null]);
    log("mount", props);
    // As a library does to the element it is given: an id the render's markup lacks
    el.id = `drawn-into-${who}`;
    const drawn = document.createElement("span");
    drawn.className = "drawn";
    drawn.textContent = JSON.stringify(props);
    el.appendChild(drawn);
    window.__islands[who] = { ctx, drawn };
    ctx.onStatus((status) => log("status", status));
    return {
      update(next) {
        log("update", next);
        drawn.textContent = JSON.stringify(next);
      },
      unmount() {
        log("unmount");
      },
    };
  },
};
