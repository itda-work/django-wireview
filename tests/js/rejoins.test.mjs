import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { RejoinBarrier } from "../../wireview/static/wireview/rejoins.mjs";

const counter = (start = 0) => {
  let ref = start;
  return () => ++ref;
};

test("a rejoin asks sync first, and holds what the page sends until its answer (#180)", () => {
  const barrier = new RejoinBarrier();
  assert.equal(barrier.holds("user_event"), false);

  assert.equal(barrier.asked(counter(4)), 5);
  assert.equal(barrier.holds("sync"), false);
  for (const command of ["user_event", "join", "leave", "hook_event", "params_changed", "navigated", "upload_register"]) {
    assert.equal(barrier.holds(command), true, command);
  }
  barrier.hold({ command: "user_event", payload: { id: "box", command: "bump" } });
  barrier.hold({ command: "leave", payload: { id: "gone" } });

  assert.equal(barrier.synced(5), true);
  assert.deepEqual(barrier.release(), [
    { command: "user_event", payload: { id: "box", command: "bump" } },
    { command: "leave", payload: { id: "gone" } },
  ]);
  assert.equal(barrier.holds("user_event"), false);
});

test("holding goes on while the joins are sent: what a DOM callback sends then waits for release", () => {
  // The review's case: a morph callback sent an event between synced and the
  // join, and the join put back what that event did
  const barrier = new RejoinBarrier();
  barrier.asked(counter());
  barrier.hold({ command: "user_event", payload: { id: "box", command: "first" } });
  assert.equal(barrier.synced(1), true);
  assert.equal(barrier.holds("user_event"), true);
  assert.equal(barrier.holds("hook_event"), true);
  barrier.hold({ command: "user_event", payload: { id: "box", command: "from-callback" } });
  // A rejoin cannot open another wait until this one is released
  assert.equal(barrier.asked(counter(9)), null);
  assert.deepEqual(
    barrier.release().map(({ payload }) => payload.command),
    ["first", "from-callback"]
  );
  assert.equal(barrier.holds("user_event"), false);
});

test("a second rejoin while waiting is the same one", () => {
  const barrier = new RejoinBarrier();
  const next = counter();
  assert.equal(barrier.asked(next), 1);
  assert.equal(barrier.asked(next), null);
  assert.equal(barrier.synced(1), true);
  assert.deepEqual(barrier.release(), []);
  // Once released, the next save asks again
  assert.equal(barrier.asked(next), 2);
});

test("an answer to no sync waited for changes nothing", () => {
  const barrier = new RejoinBarrier();
  assert.equal(barrier.synced(1), false);
  barrier.asked(counter(6));
  barrier.hold({ command: "user_event", payload: {} });
  assert.equal(barrier.synced(6), false);
  assert.equal(barrier.synced("7"), false);
  assert.equal(barrier.holds("user_event"), true);
  assert.equal(barrier.synced(7), true);
  assert.equal(barrier.synced(7), false);
  assert.equal(barrier.release().length, 1);
});

test("an answer that never comes gives the joins up and lets go of what was held", () => {
  const barrier = new RejoinBarrier();
  barrier.asked(counter(2));
  barrier.hold({ command: "user_event", payload: { id: "box" } });
  assert.equal(barrier.expired(2), null, "not the wait's ref");
  assert.deepEqual(barrier.expired(3), [{ command: "user_event", payload: { id: "box" } }]);
  assert.equal(barrier.holds("user_event"), false);
  // Its answer, late, opens nothing
  assert.equal(barrier.synced(3), false);
  // A timer from a wait already answered lets go of nothing
  barrier.asked(counter(10));
  barrier.synced(11);
  assert.equal(barrier.expired(11), null);
  assert.equal(barrier.holds("user_event"), true);
});

test("the joins a navigation sent while the rejoin waited are named, so the rejoin leaves them be", () => {
  const barrier = new RejoinBarrier();
  barrier.asked(counter());
  barrier.hold({ command: "leave", payload: { id: "old" } });
  barrier.hold({ command: "navigated", payload: { uri: "/b", params: {}, carried: [] } });
  barrier.hold({ command: "join", payload: { name: "Box", state: "s", children: {}, ref: 7 } });
  barrier.hold({ command: "join", payload: { name: "Box", state: "s", children: {} } });
  assert.deepEqual([...barrier.heldJoins()], [7]);
});

test("a closed socket ends the wait and drops what it held", () => {
  const barrier = new RejoinBarrier();
  barrier.asked(counter());
  barrier.hold({ command: "user_event", payload: {} });
  barrier.clear();
  assert.equal(barrier.holds("user_event"), false);
  assert.equal(barrier.synced(1), false);
  assert.equal(barrier.asked(counter(9)), 10);
  assert.equal(barrier.synced(10), true);
  assert.deepEqual(barrier.release(), []);
});

test("every frame the page sends goes through _send, where the barrier holds it", () => {
  // Another way onto the socket would get past a rejoin's barrier (#180)
  const source = readFileSync(new URL("../../wireview/static/wireview/wireview.js", import.meta.url), "utf8");
  assert.equal(source.split(".socket.send(").length - 1, 1);
  const write = source.indexOf(".socket.send(");
  const sender = source.lastIndexOf("  _send(command, payload, through = false) {", write);
  assert.ok(sender !== -1 && sender > source.lastIndexOf("\n  }\n", write), "the one write is inside _send");
});
