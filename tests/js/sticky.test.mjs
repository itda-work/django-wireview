import assert from "node:assert/strict";
import { test } from "node:test";

import { StickyOps } from "../../wireview/static/wireview/sticky.mjs";

/** An element as much as StickyOps touches, parsed from `class="..."` and attributes. */
function element({ classes = "", attrs = {}, hidden = false, display = "" } = {}) {
  const names = new Set(classes.split(" ").filter(Boolean));
  const attributes = new Map(Object.entries(attrs));
  return {
    classList: {
      add: (...add) => add.forEach((name) => names.add(name)),
      remove: (...remove) => remove.forEach((name) => names.delete(name)),
      contains: (name) => names.has(name),
    },
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
    hidden,
    style: { display },
    get classes() {
      return [...names].sort().join(" ");
    },
    attr: (name) => (attributes.has(name) ? attributes.get(name) : null),
  };
}

test("a class a command added survives a render that does not have it", () => {
  const ops = new StickyOps();
  const live = element({ classes: "box" });
  ops.addClasses(live, ["hot"]);
  const render = element({ classes: "box" });
  ops.apply(live, render);
  assert.equal(render.classes, "box hot");
});

test("a class a command removed stays removed through a render that has it", () => {
  const ops = new StickyOps();
  const live = element({ classes: "box open" });
  ops.removeClasses(live, ["open"]);
  const render = element({ classes: "box open" });
  ops.apply(live, render);
  assert.equal(render.classes, "box");
});

test("adding a class the element already had records nothing, so the server can take it away", () => {
  const ops = new StickyOps();
  const live = element({ classes: "active" });
  ops.addClasses(live, ["active"]);
  assert.equal(ops.has(live), false);
  const render = element();
  ops.apply(live, render);
  assert.equal(render.classes, "");
});

test("undoing a command's change leaves the element to the server again", () => {
  const ops = new StickyOps();
  const live = element({ classes: "open" });
  ops.removeClasses(live, ["open"]);
  ops.addClasses(live, ["open"]);
  assert.equal(ops.has(live), false);
  const render = element();
  ops.apply(live, render);
  assert.equal(render.classes, "", "the server's later word stands");

  ops.addClasses(live, ["hot"]);
  ops.removeClasses(live, ["hot"]);
  assert.equal(ops.has(live), false);
});

test("toggle records whichever way it went", () => {
  const ops = new StickyOps();
  const live = element({ classes: "a" });
  ops.toggleClasses(live, ["a", "b"]);
  assert.equal(live.classes, "b");
  const render = element({ classes: "a" });
  ops.apply(live, render);
  assert.equal(render.classes, "b");
});

test("attributes keep the last value a command set or removed", () => {
  const ops = new StickyOps();
  const live = element({ attrs: { "data-step": "1", title: "x" } });
  ops.setAttribute(live, "data-step", "2");
  ops.setAttribute(live, "title", null);
  ops.setAttribute(live, "aria-expanded", "true");
  ops.setAttribute(live, "aria-expanded", "false");
  const render = element({ attrs: { "data-step": "1", title: "x" } });
  ops.apply(live, render);
  assert.equal(render.attr("data-step"), "2");
  assert.equal(render.attr("title"), null);
  assert.equal(render.attr("aria-expanded"), "false");
});

test("setting the whole class attribute supersedes the class changes before it", () => {
  const ops = new StickyOps();
  const live = element({ classes: "a" });
  ops.addClasses(live, ["b"]);
  ops.setAttribute(live, "class", "c");
  const render = element({ classes: "a" });
  ops.apply(live, render);
  assert.equal(render.attr("class"), "c");
  assert.equal(render.classes, "a", "no class change of its own is replayed after it");
});

test("a class change after the class attribute is replayed on top of it", () => {
  const ops = new StickyOps();
  const live = element();
  ops.setAttribute(live, "class", "c");
  ops.addClasses(live, ["d"]);
  const render = element({ classes: "c" });
  ops.apply(live, render);
  assert.equal(render.classes, "c d");
});

test("hide and show keep the last visibility", () => {
  const ops = new StickyOps();
  const live = element();
  ops.hide(live);
  let render = element();
  ops.apply(live, render);
  assert.equal(render.hidden, true);

  ops.show(live, "flex");
  render = element({ hidden: true, display: "none" });
  ops.apply(live, render);
  assert.equal(render.hidden, false);
  assert.equal(render.style.display, "flex");
});

test("a render without a command's record is left as the server sent it", () => {
  const ops = new StickyOps();
  const render = element({ classes: "x", hidden: true });
  ops.apply(element(), render);
  assert.equal(render.classes, "x");
  assert.equal(render.hidden, true);
});

test("a running transition's classes survive a render and go when it ends", () => {
  const ops = new StickyOps();
  const live = element();
  const release = ops.transient(live, ["fade"]);
  const render = element();
  ops.apply(live, render);
  assert.equal(render.classes, "fade");
  live.classList.add("fade"); // the morph copied it over
  release();
  assert.equal(live.classes, "");
  assert.equal(ops.has(live), false);
  release();
});

test("overlapping transitions of a class end with the last", () => {
  const ops = new StickyOps();
  const live = element();
  const first = ops.transient(live, ["pulse"]);
  const second = ops.transient(live, ["pulse"]);
  first();
  assert.equal(live.classes, "pulse");
  second();
  assert.equal(live.classes, "");
});

test("a transition's end does not take a class a command keeps", () => {
  const ops = new StickyOps();
  const live = element();
  const release = ops.transient(live, ["on"]);
  ops.addClasses(live, ["on"]);
  release();
  assert.equal(live.classes, "", "already on when added: nothing recorded, the transition's class goes");

  const other = element();
  ops.addClasses(other, ["on"]);
  const again = ops.transient(other, ["on"]);
  again();
  assert.equal(other.classes, "on");
});

test("an element that replaces another keeps its record, and later changes act on it", () => {
  const ops = new StickyOps();
  const old = element();
  ops.addClasses(old, ["read"]);
  const release = ops.transient(old, ["slide"]);
  const next = element();
  ops.move(old, next);
  assert.equal(next.classes, "read slide");
  assert.equal(ops.has(old), false);
  release();
  assert.equal(next.classes, "read");
  const render = element();
  ops.apply(next, render);
  assert.equal(render.classes, "read");
});

test("a forgotten element is the server's again", () => {
  const ops = new StickyOps();
  const live = element();
  ops.addClasses(live, ["hot"]);
  ops.forget(live);
  const render = element();
  ops.apply(live, render);
  assert.equal(render.classes, "");
});

test("a handle follows its element to the one that replaces it", () => {
  const ops = new StickyOps();
  const old = element();
  const handle = ops.handle(old);
  const next = element();
  ops.move(old, next);
  assert.equal(handle.current(), next);
});

test("a handle lets go once the record is forgotten, and so does a running transition", () => {
  const ops = new StickyOps();
  const live = element();
  const handle = ops.handle(live);
  const release = ops.transient(live, ["fade"]);
  ops.forget(live);
  assert.equal(handle.current(), null, "a navigation made it the next page's");
  live.classList.add("fade"); // the next page draws it with this class
  release();
  assert.equal(live.classes, "fade", "the transition's end is not the next page's");
  assert.equal(ops.has(live), false);
});

test("an attribute keeps the spelling it was given", () => {
  const ops = new StickyOps();
  const svg = element();
  ops.setAttribute(svg, "viewBox", "0 0 10 10");
  assert.equal(svg.attr("viewBox"), "0 0 10 10");
  const render = element({ attrs: { viewBox: "0 0 1 1" } });
  ops.apply(svg, render);
  assert.equal(render.attr("viewBox"), "0 0 10 10");
  ops.setAttribute(svg, "preserveAspectRatio", null);
  assert.equal(svg.attr("preserveAspectRatio"), null);
  assert.equal(svg.attr("preserveaspectratio"), null);
});

test("hidden and style spelled in capitals still supersede the visibility", () => {
  const ops = new StickyOps();
  const live = element();
  ops.hide(live);
  ops.setAttribute(live, "HIDDEN", null);
  const render = element();
  ops.apply(live, render);
  assert.equal(render.hidden, false);
});

test("an HTML attribute is one record whatever the case it is named in", () => {
  const ops = new StickyOps();
  const live = { ...element(), namespaceURI: "http://www.w3.org/1999/xhtml" };
  ops.setAttribute(live, "TITLE", "first");
  ops.setAttribute(live, "title", "second");
  ops.setAttribute(live, "Title", "last");
  const render = element({ attrs: { title: "server" } });
  ops.apply(live, render);
  assert.equal(render.attr("title"), "last");
  assert.equal(render.attr("TITLE"), null);
});

test("the attribute set last is replayed last", () => {
  const ops = new StickyOps();
  const live = element();
  /** @type {string[]} */
  const order = [];
  ops.setAttribute(live, "a", "1");
  ops.setAttribute(live, "b", "1");
  ops.setAttribute(live, "a", "2");
  const render = { ...element(), setAttribute: (name) => order.push(name) };
  ops.apply(live, render);
  assert.deepEqual(order, ["b", "a"]);
});
