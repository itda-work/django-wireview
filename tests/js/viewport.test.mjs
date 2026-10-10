import assert from "node:assert/strict";
import { test } from "node:test";

import { fires, overran, scrolls } from "../../wireview/static/wireview/viewport.mjs";

test("an element scrolls its content when its overflow-y lets it", () => {
  assert.equal(scrolls("auto"), true);
  assert.equal(scrolls("scroll"), true);
  assert.equal(scrolls("overlay"), true);
  assert.equal(scrolls("visible"), false);
  assert.equal(scrolls("hidden"), false);
  assert.equal(scrolls("clip"), false);
});

test("the top binding fires scrolling up and the bottom one scrolling down", () => {
  assert.equal(fires("top", 500, 200, false), true);
  assert.equal(fires("top", 200, 500, false), false);
  assert.equal(fires("bottom", 200, 500, false), true);
  assert.equal(fires("bottom", 500, 200, false), false);
});

test("no movement fires nothing unless the binding is already passed", () => {
  // The window stood still: a list scrolling in its own box is measured in that box, not here
  assert.equal(fires("top", 0, 0, false), false);
  assert.equal(fires("bottom", 0, 0, false), false);
  assert.equal(fires("top", 0, 0, true), true);
  assert.equal(fires("bottom", 0, 0, true), true);
});

test("a list drawn bottom up scrolls up into negative positions, and up is still smaller", () => {
  // flex-direction: column-reverse starts at 0 and goes negative as the user reads back
  assert.equal(fires("top", 0, -1600, false), true);
  assert.equal(fires("top", -1600, -800, false), false);
});

test("a binding past the middle of its scroll box is overran, measured in that box", () => {
  const box = { top: 200, bottom: 600 }; // a 400px list lower on the page
  assert.equal(overran("top", { top: 230, bottom: 250 }, box), false);
  assert.equal(overran("top", { top: 450, bottom: 470 }, box), true);
  assert.equal(overran("bottom", { top: 550, bottom: 570 }, box), false);
  assert.equal(overran("bottom", { top: 300, bottom: 320 }, box), true);
  // The window is the box from 0 to its height
  assert.equal(overran("top", { top: 450, bottom: 470 }, { top: 0, bottom: 800 }), true);
  assert.equal(overran("top", { top: 350, bottom: 370 }, { top: 0, bottom: 800 }), false);
});
