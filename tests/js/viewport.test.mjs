import assert from "node:assert/strict";
import { test } from "node:test";

import { fires, overran, scrollBox, scrolls, visibleFrame } from "../../wireview/static/wireview/viewport.mjs";

test("an element scrolls its content when its overflow-y lets it", () => {
  assert.equal(scrolls("auto"), true);
  assert.equal(scrolls("scroll"), true);
  assert.equal(scrolls("overlay"), true);
  assert.equal(scrolls("visible"), false);
  assert.equal(scrolls("hidden"), false);
  assert.equal(scrolls("clip"), false);
});

test("the scroll box is the nearest ancestor that scrolls and overflows", () => {
  const list = { overflowY: "auto", scrollHeight: 3000, clientHeight: 400 };
  const plain = { overflowY: "visible", scrollHeight: 400, clientHeight: 400 };
  assert.equal(scrollBox([plain, list, plain]), 1);
  assert.equal(scrollBox([list, { ...list }]), 0);
  assert.equal(scrollBox([]), -1);
  assert.equal(scrollBox([plain]), -1);
});

test("a wrapper that only computes overflow-y: auto is not the scroll box: the window is", () => {
  // `overflow-x: hidden` alone computes overflow-y to auto; with no height of
  // its own the wrapper holds all its content and the window scrolls it
  const wrapper = { overflowY: "auto", scrollHeight: 1341, clientHeight: 1341 };
  assert.equal(scrollBox([wrapper]), -1);
  // A list inside it that does overflow is still the box
  assert.equal(scrollBox([{ overflowY: "auto", scrollHeight: 2000, clientHeight: 300 }, wrapper]), 0);
  // An ancestor that overflows but hides its overflow does not scroll
  assert.equal(scrollBox([{ overflowY: "hidden", scrollHeight: 2000, clientHeight: 300 }]), -1);
});

test("the top binding fires scrolling up and the bottom one scrolling down", () => {
  assert.equal(fires("top", [[500, 200]], false), true);
  assert.equal(fires("top", [[200, 500]], false), false);
  assert.equal(fires("bottom", [[200, 500]], false), true);
  assert.equal(fires("bottom", [[500, 200]], false), false);
});

test("any scroller that moved toward the binding fires it", () => {
  // The list scrolled up in its own box while the window stood still
  assert.equal(fires("top", [[0, -1600], [0, 0]], false), true);
  // The window scrolled down past a wrapper that never moves
  assert.equal(fires("bottom", [[0, 0], [0, 900]], false), true);
  // Nothing moved toward it
  assert.equal(fires("bottom", [[0, 0], [900, 0]], false), false);
});

test("no movement fires nothing unless the binding is already passed", () => {
  assert.equal(fires("top", [[0, 0]], false), false);
  assert.equal(fires("bottom", [[0, 0]], false), false);
  assert.equal(fires("top", [], false), false);
  assert.equal(fires("top", [[0, 0]], true), true);
  assert.equal(fires("bottom", [], true), true);
});

test("a list drawn bottom up scrolls up into negative positions, and up is still smaller", () => {
  // flex-direction: column-reverse starts at 0 and goes negative as the user reads back
  assert.equal(fires("top", [[0, -1600]], false), true);
  assert.equal(fires("top", [[-1600, -800]], false), false);
});

// Tested here, not in a browser: the clip is arithmetic on the box's
// getBoundingClientRect() and innerHeight, which wireview.js passes in
// unchanged. A page that showed it would need a box taller than the window with
// a binding past the middle of the part in view, and would check these numbers again.
test("what the user sees of a scroll box is the box clipped to the window", () => {
  assert.deepEqual(visibleFrame(null, 800), { top: 0, bottom: 800 });
  assert.deepEqual(visibleFrame({ top: 200, bottom: 600 }, 800), { top: 200, bottom: 600 });
  // A box taller than the window, scrolled half out of it
  assert.deepEqual(visibleFrame({ top: -300, bottom: 1500 }, 800), { top: 0, bottom: 800 });
});

test("a binding past the middle of its scroll box is overran, measured in that box", () => {
  const box = { top: 200, bottom: 600 }; // a 400px list lower on the page
  assert.equal(overran("top", { top: 230, bottom: 250 }, box), false);
  assert.equal(overran("top", { top: 450, bottom: 470 }, box), true);
  assert.equal(overran("bottom", { top: 550, bottom: 570 }, box), false);
  assert.equal(overran("bottom", { top: 300, bottom: 320 }, box), true);
  // The window is the box from 0 to its height
  assert.equal(overran("top", { top: 450, bottom: 470 }, visibleFrame(null, 800)), true);
  assert.equal(overran("top", { top: 350, bottom: 370 }, visibleFrame(null, 800)), false);
});
