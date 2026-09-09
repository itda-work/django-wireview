import assert from "node:assert/strict";
import { test } from "node:test";

import { applyPartial, buildHtml, isBlock, isComprehension } from "../../wireview/static/wireview/rendered.mjs";

const list = () => ({ s: ["<li>", " x ", "</li>"], d: [["a", "1"], ["b", "2"]] });

test("buildHtml interleaves statics and string dynamics", () => {
  assert.equal(buildHtml(["<p>", "</p>"], ["hi"]), "<p>hi</p>");
  assert.equal(buildHtml(["<p>", " ", "</p>"], ["a", ""]), "<p>a </p>");
});

test("buildHtml expands a comprehension per item", () => {
  const html = buildHtml(["<ul>", "</ul>"], [list()]);
  assert.equal(html, "<ul><li>a x 1</li><li>b x 2</li></ul>");
});

test("buildHtml handles nested comprehensions", () => {
  const inner = { s: ["<i>", "</i>"], d: [["q"]] };
  const outer = { s: ["<li>", "", "</li>"], d: [["a", inner]] };
  assert.equal(buildHtml(["<ul>", "</ul>"], [outer]), "<ul><li>a<i>q</i></li></ul>");
});

test("applyPartial replaces string dynamics", () => {
  const dynamics = ["a", "b"];
  applyPartial(dynamics, { 1: "c" });
  assert.deepEqual(dynamics, ["a", "c"]);
});

test("applyPartial updates only the changed items and length", () => {
  const dynamics = ["T", list()];
  applyPartial(dynamics, { 1: { u: { 1: ["b", "3"], 2: ["c", "4"] }, n: 3 } });
  assert.deepEqual(dynamics[1].d, [["a", "1"], ["b", "3"], ["c", "4"]]);
  applyPartial(dynamics, { 1: { u: {}, n: 1 } });
  assert.deepEqual(dynamics[1].d, [["a", "1"]]);
});

test("applyPartial replaces a comprehension when its statics are sent", () => {
  const dynamics = ["T", "<li>none</li>"];
  applyPartial(dynamics, { 1: list() });
  assert.ok(isComprehension(dynamics[1]));
  assert.equal(buildHtml(["<ul>", "</ul>"], [dynamics[1]]), "<ul><li>a x 1</li><li>b x 2</li></ul>");
});

test("applyPartial turns a plain slot into a comprehension on update", () => {
  const dynamics = ["T", ""];
  applyPartial(dynamics, { 1: { u: { 0: ["z", "9"] }, n: 1 } });
  assert.ok(isComprehension(dynamics[1]));
  assert.deepEqual(dynamics[1].d, [["z", "9"]]);
});

test("applyPartial ignores out-of-range indexes", () => {
  const dynamics = ["a"];
  applyPartial(dynamics, { 5: "x", "-1": "y", foo: "z" });
  assert.deepEqual(dynamics, ["a"]);
});

test("buildHtml renders a block as a nested render", () => {
  const block = { r: ["<b>", "</b>"], d: ["Y"] };
  assert.equal(buildHtml(["<p>", "</p>"], [block]), "<p><b>Y</b></p>");
  assert.equal(buildHtml(["<p>", "</p>"], [{ r: [""], d: [] }]), "<p></p>");
});

test("applyPartial replaces a block when a branch switches", () => {
  const dynamics = [{ r: ["<b>", "</b>"], d: ["Y"] }];
  applyPartial(dynamics, { 0: { r: ["no"], d: [] } });
  assert.ok(isBlock(dynamics[0]));
  assert.equal(buildHtml(["<p>", "</p>"], dynamics), "<p>no</p>");
});

test("applyPartial applies a nested block partial in place", () => {
  const dynamics = [{ r: ["<b>", "</b>"], d: ["Y"] }];
  applyPartial(dynamics, { 0: { p: { 0: "Z" } } });
  assert.equal(buildHtml(["<p>", "</p>"], dynamics), "<p><b>Z</b></p>");
});

test("comprehension items may carry blocks", () => {
  const comp = { s: ['<li class="', '">', "</li>"], d: [[{ r: ["done"], d: [] }, "a"], [{ r: [""], d: [] }, "b"]] };
  assert.equal(buildHtml(["<ul>", "</ul>"], [comp]), '<ul><li class="done">a</li><li class="">b</li></ul>');
  applyPartial([comp], { 0: { u: { 1: [{ r: ["done"], d: [] }, "b"] }, n: 2 } });
  assert.equal(buildHtml(["<ul>", "</ul>"], [comp]), '<ul><li class="done">a</li><li class="done">b</li></ul>');
});

// --- component references: a nested LiveComponent's slot names it, its HTML comes from elsewhere ---

import { isComponentRef } from "../../wireview/static/wireview/rendered.mjs";

const childHtml = { c1: "<div id=\"c1\">child one</div>", c2: "<div id=\"c2\">child two</div>" };
const resolve = (id) => childHtml[id] ?? "";

test("a component ref is an object with a string c", () => {
  assert.ok(isComponentRef({ c: "c1" }));
  assert.ok(!isComponentRef({ s: [], d: [] }));
  assert.ok(!isComponentRef({ r: [], d: [] }));
  assert.ok(!isComponentRef("c1"));
  assert.ok(!isComponentRef(null));
});

test("buildHtml substitutes a ref with the resolver's HTML", () => {
  assert.equal(buildHtml(["<main>", "</main>"], [{ c: "c1" }], resolve), '<main><div id="c1">child one</div></main>');
});

test("a ref renders as nothing without a resolver or for an unknown id", () => {
  assert.equal(buildHtml(["<main>", "</main>"], [{ c: "c1" }]), "<main></main>");
  assert.equal(buildHtml(["<main>", "</main>"], [{ c: "ghost" }], resolve), "<main></main>");
});

test("refs inside comprehension items and blocks resolve too", () => {
  const grid = { s: ["<li>", "</li>"], d: [[{ c: "c1" }], [{ c: "c2" }]] };
  assert.equal(
    buildHtml(["<ul>", "</ul>"], [grid], resolve),
    '<ul><li><div id="c1">child one</div></li><li><div id="c2">child two</div></li></ul>'
  );
  const block = { r: ["<b>", "</b>"], d: [{ c: "c2" }] };
  assert.equal(buildHtml(["<p>", "</p>"], [block], resolve), '<p><b><div id="c2">child two</div></b></p>');
});

test("applyPartial puts a ref into a slot and can replace it", () => {
  const dynamics = ["T", ""];
  applyPartial(dynamics, { 1: { c: "c1" } });
  assert.deepEqual(dynamics, ["T", { c: "c1" }]);
  applyPartial(dynamics, { 1: { c: "c2" } });
  assert.deepEqual(dynamics, ["T", { c: "c2" }]);
  applyPartial(dynamics, { 1: "" });
  assert.deepEqual(dynamics, ["T", ""]);
});
