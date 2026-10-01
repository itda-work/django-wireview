import assert from "node:assert/strict";
import { test } from "node:test";

import { StreamOpQueue, isStreamContainer, planInsert, planTrim } from "../../wireview/static/wireview/streams.mjs";

const element = (attributes = {}) => ({
  nodeType: 1,
  hasAttribute: (name) => name in attributes,
});

test("a stream container is an element carrying wire-stream", () => {
  assert.equal(isStreamContainer(element({ "wire-stream": "items" })), true);
  assert.equal(isStreamContainer(element({ class: "items" })), false);
});

test("nodes that cannot carry attributes are not stream containers", () => {
  assert.equal(isStreamContainer(null), false);
  assert.equal(isStreamContainer({ nodeType: 3 }), false); // text node
  assert.equal(isStreamContainer({ nodeType: 1 }), false); // no hasAttribute
});

test("an id already on screen is replaced where it stands", () => {
  assert.deepEqual(planInsert({ exists: true, at: 0, childCount: 3 }), { mode: "replace" });
  assert.deepEqual(planInsert({ exists: true, at: -1, childCount: 3 }), { mode: "replace" });
});

test("a new item lands where `at` says", () => {
  assert.deepEqual(planInsert({ exists: false, at: 0, childCount: 3 }), { mode: "prepend" });
  assert.deepEqual(planInsert({ exists: false, at: -1, childCount: 3 }), { mode: "append" });
  assert.deepEqual(planInsert({ exists: false, at: 1, childCount: 3 }), { mode: "before", index: 1 });
});

test("an index past the end appends instead of throwing", () => {
  assert.deepEqual(planInsert({ exists: false, at: 5, childCount: 3 }), { mode: "append" });
  assert.deepEqual(planInsert({ exists: false, at: 3, childCount: 3 }), { mode: "append" });
});

test("no limit means no trimming", () => {
  assert.deepEqual(planTrim({ childCount: 100, limit: 0, at: -1 }), { count: 0, fromEnd: false });
  assert.deepEqual(planTrim({ childCount: 3, limit: 5, at: -1 }), { count: 0, fromEnd: false });
});

test("trimming drops from the end the new items did not arrive at", () => {
  assert.deepEqual(planTrim({ childCount: 7, limit: 5, at: 0 }), { count: 2, fromEnd: true });
  assert.deepEqual(planTrim({ childCount: 7, limit: 5, at: -1 }), { count: 2, fromEnd: false });
});

/** A queue over a page whose containers a test puts in place, with frames it runs by hand. */
function queueOn(page) {
  const applied = [];
  const dropped = [];
  const frames = [];
  const queue = new StreamOpQueue({
    find: (op) => page[op.owner] ?? null,
    apply: (container, op) => applied.push(`${container}:${op.name}`),
    schedule: (callback) => frames.push(callback),
    drop: (op) => dropped.push(op.name),
  });
  const frame = () => frames.splice(0).forEach((callback) => callback());
  return { queue, applied, dropped, frames, frame };
}

test("an op whose container is on the page applies at once", () => {
  const { queue, applied, frames } = queueOn({ a: "A" });
  queue.push({ owner: "a", name: "insert" });
  assert.deepEqual(applied, ["A:insert"]);
  assert.equal(frames.length, 0);
});

test("an op whose container is not there yet applies after the next frame's morphs", () => {
  const page = {};
  const { queue, applied, dropped, frame } = queueOn(page);
  queue.push({ owner: "child", name: "reset" });
  assert.deepEqual(applied, []);
  page.child = "C"; // the morph scheduled before it puts the element in
  frame();
  assert.deepEqual(applied, ["C:reset"]);
  assert.deepEqual(dropped, []);
});

test("ops behind a held one wait with it, so they keep the server's order", () => {
  const page = { a: "A" };
  const { queue, applied, frames, frame } = queueOn(page);
  queue.push({ owner: "child", name: "reset" });
  queue.push({ owner: "a", name: "insert" });
  queue.push({ owner: "child", name: "insert" });
  assert.deepEqual(applied, []);
  assert.equal(frames.length, 1, "one frame for the lot");
  page.child = "C";
  frame();
  assert.deepEqual(applied, ["C:reset", "A:insert", "C:insert"]);
});

test("an op that still finds nothing a frame later is dropped, and nothing stays held", () => {
  const { queue, applied, dropped, frame } = queueOn({ a: "A" });
  queue.push({ owner: "gone", name: "insert" });
  frame();
  assert.deepEqual(dropped, ["insert"]);
  assert.equal(queue.held.length, 0);
  queue.push({ owner: "a", name: "delete" });
  assert.deepEqual(applied, ["A:delete"], "after the drop, ops apply at once again");
});

test("an op that arrives after the flush was scheduled waits for that same flush", () => {
  const page = {};
  const { queue, applied, frames, frame } = queueOn(page);
  queue.push({ owner: "child", name: "reset" });
  page.child = "C";
  queue.push({ owner: "child", name: "insert" }); // its container is there, but an earlier op waits
  assert.deepEqual(applied, []);
  assert.equal(frames.length, 1);
  frame();
  assert.deepEqual(applied, ["C:reset", "C:insert"]);
});
