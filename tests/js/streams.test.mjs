import assert from "node:assert/strict";
import { test } from "node:test";

import { isStreamContainer, pinContainerIds, planInsert, planTrim } from "../../wireview/static/wireview/streams.mjs";

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

const spot = (owner, name, id = "") => ({ owner, name, id });

test("a container without an id gets one on the page and in the render alike", () => {
  const pins = pinContainerIds([spot("p", "rows")], [spot("p", "late"), spot("p", "rows")]);
  assert.deepEqual([...pins.live], [[0, "wire-stream-p-rows"]]);
  assert.deepEqual([...pins.next], [[1, "wire-stream-p-rows"]], "the new list behind the {% if %} gets none");
});

test("a container that was pinned before keeps its id", () => {
  const pins = pinContainerIds([spot("p", "rows", "wire-stream-p-rows")], [spot("p", "rows")]);
  assert.deepEqual([...pins.live], []);
  assert.deepEqual([...pins.next], [[0, "wire-stream-p-rows"]]);
});

test("a container the template names keeps the template's id", () => {
  const pins = pinContainerIds([spot("p", "rows", "my-rows")], [spot("p", "rows", "my-rows")]);
  assert.deepEqual([...pins.live], []);
  assert.deepEqual([...pins.next], []);
});

test("containers pair within their component", () => {
  const pins = pinContainerIds(
    [spot("child", "ticks"), spot("p", "ticks")],
    [spot("child", "ticks"), spot("p", "ticks")]
  );
  assert.deepEqual([...pins.next], [
    [0, "wire-stream-child-ticks"],
    [1, "wire-stream-p-ticks"],
  ]);
  assert.deepEqual([...pins.live], [
    [0, "wire-stream-child-ticks"],
    [1, "wire-stream-p-ticks"],
  ]);
});

test("only the first container of a name in a component pairs", () => {
  // The one stream ops go to (`_streamContainer`); a second of the name stays as before
  const pins = pinContainerIds([spot("p", "rows"), spot("p", "rows")], [spot("p", "rows"), spot("p", "rows")]);
  assert.deepEqual([...pins.next], [[0, "wire-stream-p-rows"]]);
  assert.deepEqual([...pins.live], [[0, "wire-stream-p-rows"]]);
});
