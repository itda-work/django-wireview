import assert from "node:assert/strict";
import { test } from "node:test";

import { TargetQueue } from "../../wireview/static/wireview/targets.mjs";

/**
 * A queue over a page whose elements a test puts in place, with frames it runs
 * by hand. `send(owner, name)` pushes a command aimed at the owner's element.
 */
function queueOn(page) {
  const applied = [];
  const dropped = [];
  const reported = [];
  const frames = [];
  const queue = new TargetQueue({
    schedule: (callback) => frames.push(callback),
    report: (error) => reported.push(error.message),
  });
  const send = (owner, name, apply = (target) => applied.push(`${target}:${name}`)) =>
    queue.push(owner, {
      find: () => page[owner] ?? null,
      apply,
      drop: () => dropped.push(name),
    });
  const frame = () => frames.splice(0).forEach((callback) => callback());
  return { queue, send, applied, dropped, reported, frames, frame };
}

test("a command whose target is on the page applies at once", () => {
  const { send, applied, frames } = queueOn({ a: "A" });
  send("a", "insert");
  assert.deepEqual(applied, ["A:insert"]);
  assert.equal(frames.length, 0);
});

test("a command whose target is not there yet applies after the next frame's morphs", () => {
  const page = {};
  const { send, applied, dropped, frame } = queueOn(page);
  send("child", "reset");
  assert.deepEqual(applied, []);
  page.child = "C"; // the morph scheduled before it puts the element in
  frame();
  assert.deepEqual(applied, ["C:reset"]);
  assert.deepEqual(dropped, []);
});

test("an owner's commands behind a held one wait with it, so they keep the server's order", () => {
  const page = {};
  const { send, applied, frames, frame } = queueOn(page);
  send("child", "reset");
  page.child = "C"; // there now, but an earlier command of its owner waits
  send("child", "insert");
  send("child", "exec");
  assert.deepEqual(applied, []);
  assert.equal(frames.length, 1, "one frame for the lot");
  frame();
  assert.deepEqual(applied, ["C:reset", "C:insert", "C:exec"]);
});

test("another owner's commands do not wait behind a held one", () => {
  // Nothing ties two components' lists together. Holding them back kept
  // every component's stream in memory for as long as a background tab ran no
  // frame, though only one was waiting for its element.
  const page = { a: "A" };
  const { queue, send, applied, frame } = queueOn(page);
  send("child", "reset");
  for (let n = 0; n < 100; n += 1) send("a", `insert ${n}`);
  assert.equal(applied.length, 100);
  assert.deepEqual(applied.at(-1), "A:insert 99");
  assert.deepEqual([...queue.held.keys()], ["child"]);
  assert.equal(queue.held.get("child").length, 1, "only the owner that waits holds anything");
  page.child = "C";
  frame();
  assert.deepEqual(applied.at(-1), "C:reset");
});

test("commands without an owner keep their order among themselves", () => {
  // An older server names no owner: its stream ops all wait on one key
  const page = {};
  const { send, applied, frame } = queueOn(page);
  send(undefined, "reset");
  page[undefined] = "P";
  send(undefined, "insert");
  assert.deepEqual(applied, []);
  frame();
  assert.deepEqual(applied, ["P:reset", "P:insert"]);
});

test("a command that still finds nothing a frame later is dropped, and nothing stays held", () => {
  const { queue, send, applied, dropped, frame } = queueOn({ a: "A" });
  send("gone", "insert");
  frame();
  assert.deepEqual(dropped, ["insert"]);
  assert.equal(queue.held.size, 0);
  send("gone", "delete");
  assert.equal(queue.held.size, 1, "after the drop, the owner may wait again");
  send("a", "delete");
  assert.deepEqual(applied, ["A:delete"]);
});

test("a held command that throws is reported, and the ones behind it still apply", () => {
  const page = {};
  const { send, applied, reported, frame } = queueOn(page);
  send("child", "reset");
  send("child", "broken", () => {
    throw new Error("bad html");
  });
  send("child", "insert");
  page.child = "C";
  frame();
  assert.deepEqual(applied, ["C:reset", "C:insert"]);
  assert.deepEqual(reported, ["bad html"]);
});

test("each owner that waits gets its own frame", () => {
  const page = {};
  const { send, applied, frames, frame } = queueOn(page);
  send("one", "reset");
  send("two", "reset");
  assert.equal(frames.length, 2);
  page.one = "1";
  page.two = "2";
  frame();
  assert.deepEqual(applied, ["1:reset", "2:reset"]);
});
