import assert from "node:assert/strict";
import { test } from "node:test";

import { LoadingLedger } from "../../wireview/static/wireview/loading.mjs";

test("an answer releases the elements of its own event, not another's", () => {
  const ledger = new LoadingLedger();
  ledger.track(3, "a", ["save"], "click");
  ledger.track(2, "b", ["send"], "submit");

  assert.deepEqual(ledger.answer(2), ["send"]);
  assert.deepEqual(ledger.pending().map((mark) => mark.elements), [["save"]]);
});

test("the answer to a ref settles every earlier ref: the server answers in order", () => {
  const ledger = new LoadingLedger();
  ledger.track(1, "a", ["first"]);
  ledger.track(2, "a", ["second"]);
  ledger.track(3, "a", ["third"]);

  assert.deepEqual(ledger.answer(2), ["first", "second"]);
  assert.deepEqual(ledger.pending().map((mark) => mark.ref), [3]);
});

test("an element still waiting on a later event stays marked", () => {
  const ledger = new LoadingLedger();
  ledger.track(1, "a", ["button"]);
  ledger.track(2, "a", ["button"]);

  assert.deepEqual(ledger.answer(1), []);
  assert.deepEqual(ledger.answer(2), ["button"]);
});

test("a render that answers no ref releases nothing a paired event started", () => {
  const ledger = new LoadingLedger();
  ledger.track(1, "a", ["button"]);

  assert.deepEqual(ledger.answerUnpaired("a"), []);
  assert.equal(ledger.pending().length, 1);
});

test("without refs, any render of the component answers its own marks only", () => {
  const ledger = new LoadingLedger();
  ledger.track(null, "a", ["mine"]);
  ledger.track(null, "b", ["theirs"]);

  assert.deepEqual(ledger.answerUnpaired("a"), ["mine"]);
  assert.deepEqual(ledger.pending().map((mark) => mark.componentId), ["b"]);
});

test("a component that is discarded abandons every mark it started", () => {
  const ledger = new LoadingLedger();
  ledger.track(4, "a", ["x"]);
  ledger.track(null, "a", ["y"]);
  ledger.track(5, "b", ["z"]);

  assert.deepEqual(ledger.abandon("a"), ["x", "y"]);
  assert.deepEqual(ledger.pending().map((mark) => mark.componentId), ["b"]);
});

test("a closed connection releases everything", () => {
  const ledger = new LoadingLedger();
  ledger.track(1, "a", ["x"]);
  ledger.track(null, "b", ["y", "x"]);

  assert.deepEqual(ledger.clear().sort(), ["x", "y"]);
  assert.equal(ledger.pending().length, 0);
});

test("an event that marks nothing is not tracked", () => {
  const ledger = new LoadingLedger();
  ledger.track(1, "a", []);

  assert.equal(ledger.pending().length, 0);
});

test("a submit held for its uploads is ended by no answer until its event is sent (#264)", () => {
  const ledger = new LoadingLedger();
  const mark = ledger.hold("a", ["form", "button"], "submit");
  ledger.track(5, "a", ["other"]);

  // Answers to events sent meanwhile, paired or not, leave it marked
  assert.deepEqual(ledger.answer(5), ["other"]);
  assert.deepEqual(ledger.answerUnpaired("a"), []);
  assert.equal(ledger.pending().length, 1);

  assert.equal(ledger.send(mark, 6), true);
  assert.deepEqual(ledger.answer(6), ["form", "button"]);
});

test("a held submit that is not sent is dropped alone", () => {
  const ledger = new LoadingLedger();
  const mark = ledger.hold("a", ["form"], "submit");
  ledger.track(1, "a", ["form"]);

  // The form still waits for the other event's answer
  assert.deepEqual(ledger.drop(mark), []);
  assert.deepEqual(ledger.answer(1), ["form"]);
});

test("a held submit ends with its component or the connection, and cannot be sent after", () => {
  const ledger = new LoadingLedger();
  const mark = ledger.hold("a", ["form"], "submit");
  assert.deepEqual(ledger.abandon("a"), ["form"]);
  assert.equal(ledger.send(mark, 1), false);
  assert.deepEqual(ledger.drop(mark), []);

  const other = ledger.hold("b", ["form"], "submit");
  assert.deepEqual(ledger.clear(), ["form"]);
  assert.equal(ledger.send(other, 2), false);
});
