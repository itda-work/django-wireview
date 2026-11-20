import assert from "node:assert/strict";
import { test } from "node:test";

import { FocusStack, radioStops, tabOrder, wrapTarget } from "../../wireview/static/wireview/focus.mjs";

test("Tab visits a positive tabindex first, lowest first, then the rest in document order", () => {
  const tabs = { a: 0, b: 2, c: 0, d: 1, e: 2 };
  assert.deepEqual(tabOrder(["a", "b", "c", "d", "e"], (x) => tabs[x]), ["d", "b", "e", "a", "c"]);
  assert.deepEqual(tabOrder([], () => 0), []);
});

test("a wrap sends a Tab off the last element to the first and a Shift-Tab off the first to the last", () => {
  const order = ["a", "b", "c"];
  assert.equal(wrapTarget(order, "c", false), "a");
  assert.equal(wrapTarget(order, "a", true), "c");
  assert.equal(wrapTarget(order, "b", false), null, "inside, the browser moves");
  assert.equal(wrapTarget(order, "b", true), null);
  assert.equal(wrapTarget(order, "a", false), null);
});

test("from the container itself, or what Tab does not reach, a wrap goes to its first or last element", () => {
  assert.equal(wrapTarget(["a", "b"], "container", false), "a");
  assert.equal(wrapTarget(["a", "b"], "container", true), "b");
  assert.equal(wrapTarget(["a", "b"], null, false), "a");
  assert.equal(wrapTarget([], "container", false), null, "nothing to go to: the browser moves");
});

test("pop gives the newest element back and forgets it", () => {
  const stack = new FocusStack();
  stack.push("opener", 1);
  stack.push("inner", 1);
  assert.equal(stack.pop(() => true), "inner");
  assert.equal(stack.pop(() => true), "opener");
  assert.equal(stack.pop(() => true), null);
});

test("pop skips, and forgets, what is no longer valid", () => {
  const stack = new FocusStack();
  stack.push("opener", 1);
  stack.push("gone", 1);
  stack.push("other-page", 0);
  assert.equal(stack.pop(({ element, page }) => element !== "gone" && page === 1), "opener");
  assert.equal(stack.size, 0);
});

test("the stack keeps only the newest ones past its limit", () => {
  const stack = new FocusStack(2);
  for (const name of ["a", "b", "c"]) stack.push(name, 0);
  assert.deepEqual(stack.entries.map(({ element }) => element), ["b", "c"]);
});

test("Tab stops once in a radio group: at its checked radio, or its first", () => {
  const radios = { a: "size", b: "size", c: "size", x: "color", y: "color" };
  const checked = new Set(["b"]);
  const stops = radioStops(["button", "a", "b", "c", "x", "y", "link"], (e) => radios[e] ?? null, (e) => checked.has(e));
  assert.deepEqual(stops, ["button", "b", "x", "link"]);
});
