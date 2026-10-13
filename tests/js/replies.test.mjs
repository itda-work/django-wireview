import assert from "node:assert/strict";
import { test } from "node:test";

import { Replies, WireviewPushError } from "../../wireview/static/wireview/replies.mjs";

/** How a promise settled, once the tasks queued so far ran: null while it has not. */
async function settled(promise) {
  let outcome = null;
  promise.then(
    (value) => (outcome = { value }),
    (error) => (outcome = { error })
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  return outcome;
}

test("refs are counted for the page, not per component", () => {
  const replies = new Replies();
  assert.equal(replies.ask({ owner: "a", hook: "h1", event: "x", callback: () => {} }).ref, "hook-1");
  assert.equal(replies.ask({ owner: "b", hook: "h2", event: "x", callback: () => {} }).ref, "hook-2");
});

test("a callback hears its response, and a ref answered once is answered no more", () => {
  const replies = new Replies();
  const heard = [];
  const { ref, promise } = replies.ask({ owner: "a", hook: "h", event: "x", callback: (r) => heard.push(r) });
  assert.equal(promise, undefined);
  replies.receive(ref, 1);
  replies.receive(ref, 2);
  replies.deliver(ref);
  replies.deliver(ref);
  assert.deepEqual(heard, [1]);
});

test("without a callback the response resolves a Promise", async () => {
  const replies = new Replies();
  const { ref, promise } = replies.ask({ owner: "a", hook: "h", event: "x" });
  assert.equal(await settled(promise), null);
  replies.receive(ref, { n: 1 });
  assert.equal(await settled(promise), null);
  replies.deliver(ref);
  assert.deepEqual(await promise, { n: 1 });
});

test("a failure rejects a Promise with its reason and the event", async () => {
  const replies = new Replies();
  const { promise } = replies.ask({ owner: "a", hook: "h", event: "save" });
  replies.fail(() => true, "disconnected");
  const { error } = await settled(promise);
  assert.ok(error instanceof WireviewPushError);
  assert.equal(error.reason, "disconnected");
  assert.equal(error.event, "save");
});

test("a failure calls no callback, and drops it", () => {
  const replies = new Replies();
  const heard = [];
  const { ref } = replies.ask({ owner: "a", hook: "h", event: "x", callback: (r) => heard.push(r) });
  replies.fail(() => true, "error");
  assert.equal(replies.receive(ref, 1), false);
  replies.deliver(ref);
  assert.deepEqual(heard, []);
});

test("fail gives up only on what it picks", async () => {
  const replies = new Replies();
  const mine = replies.ask({ owner: "a", hook: "h", event: "x" });
  const theirs = replies.ask({ owner: "b", hook: "k", event: "x" });
  replies.fail((waiting) => waiting.owner === "a", "error");
  assert.equal((await settled(mine.promise)).error.reason, "error");
  assert.equal(await settled(theirs.promise), null);
  replies.receive(theirs.ref, "ok");
  replies.deliver(theirs.ref);
  assert.equal(await theirs.promise, "ok");
});

test("giveUp gives up on one ref", async () => {
  const replies = new Replies();
  const one = replies.ask({ owner: "a", hook: "h", event: "x" });
  const other = replies.ask({ owner: "a", hook: "h", event: "x" });
  replies.giveUp(one.ref, "disconnected");
  assert.equal((await settled(one.promise)).error.reason, "disconnected");
  assert.equal(await settled(other.promise), null);
});

test("an entry says what sent it and whether a Promise waits", () => {
  const replies = new Replies();
  const by = {};
  const { ref } = replies.ask({ owner: "a", hook: "h", event: "x", by });
  const waiting = replies.waiting.get(ref);
  assert.equal(waiting.by, by);
  assert.equal(waiting.promised, true);
  assert.equal(waiting.sent, false);
  replies.sent(ref);
  assert.equal(waiting.sent, true);
});

test("a Promise nobody awaits does not report an unhandled rejection", async () => {
  const unhandled = [];
  const listen = (reason) => unhandled.push(reason);
  process.on("unhandledRejection", listen);
  try {
    const replies = new Replies();
    replies.ask({ owner: "a", hook: "h", event: "x" });
    replies.fail(() => true, "disconnected");
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", listen);
  }
});

test("a callback that throws is reported, and the ref is answered", () => {
  const replies = new Replies();
  const reported = [];
  const { ref } = replies.ask({
    owner: "a",
    hook: "h",
    event: "x",
    callback: () => {
      throw new Error("boom");
    },
  });
  replies.receive(ref, 1);
  replies.deliver(ref, (error) => reported.push(error.message));
  assert.deepEqual(reported, ["boom"]);
  assert.equal(replies.arrived.size, 0);
});

test("a reply that came is not undone by an error or a closed socket before it is handed over", async () => {
  const replies = new Replies();
  const heard = [];
  const called = replies.ask({ owner: "a", hook: "h", event: "save", callback: (r) => heard.push(r) });
  const awaited = replies.ask({ owner: "a", hook: "h", event: "save" });
  replies.receive(called.ref, "saved");
  replies.receive(awaited.ref, "saved");
  replies.fail(() => true, "error");
  replies.fail(() => true, "disconnected");
  replies.deliver(called.ref);
  replies.deliver(awaited.ref);
  assert.deepEqual(heard, ["saved"]);
  assert.deepEqual(await settled(awaited.promise), { value: "saved" });
});

test("a component that leaves drops a callback whose reply came, and a Promise still hears it", async () => {
  const replies = new Replies();
  const by = {};
  const heard = [];
  const called = replies.ask({ owner: "a", hook: "h", event: "x", by, callback: (r) => heard.push(r) });
  const awaited = replies.ask({ owner: "a", hook: "h", event: "x", by });
  replies.receive(called.ref, 1);
  replies.receive(awaited.ref, 2);
  replies.fail((waiting) => waiting.by === by, "destroyed");
  replies.deliver(called.ref);
  replies.deliver(awaited.ref);
  assert.deepEqual(heard, []);
  assert.deepEqual(await settled(awaited.promise), { value: 2 });
});

test("only what was written to the socket can be given up on for the instance's error", async () => {
  // The page's error rule: what a rejoin still holds goes to the next instance
  const replies = new Replies();
  const written = replies.ask({ owner: "a", hook: "h", event: "x" });
  const held = replies.ask({ owner: "a", hook: "h", event: "x" });
  replies.sent(written.ref);
  replies.fail((waiting) => waiting.sent && waiting.owner === "a", "error");
  assert.equal((await settled(written.promise)).error.reason, "error");
  assert.equal(await settled(held.promise), null);
});
