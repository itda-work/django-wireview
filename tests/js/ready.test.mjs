import { strict as assert } from "node:assert";
import test from "node:test";

import { createDocumentReady } from "../../wireview/static/wireview/ready.mjs";

/** A document and window that record what was subscribed and can fire it. */
function fakeDom(readyState) {
  const listeners = { document: {}, window: {} };
  const target = (bag) => ({
    addEventListener(name, fn) {
      bag[name] = fn;
    },
  });
  return {
    doc: { readyState, ...target(listeners.document) },
    win: target(listeners.window),
    fire(where, name) {
      const fn = listeners[where][name];
      assert.ok(fn, `nothing subscribed to ${where}.${name}`);
      fn();
    },
    subscribed(where, name) {
      return Boolean(listeners[where][name]);
    },
  };
}

test("a complete document runs the callback at once", () => {
  const dom = fakeDom("complete");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  let ran = 0;
  whenReady(() => ran++);

  assert.equal(ran, 1);
  assert.equal(dom.subscribed("document", "DOMContentLoaded"), false);
});

test("while parsing, the callback waits for DOMContentLoaded", () => {
  const dom = fakeDom("loading");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  let ran = 0;
  whenReady(() => ran++);
  assert.equal(ran, 0, "a join must not go out before the deferred scripts run");

  dom.fire("document", "DOMContentLoaded");
  assert.equal(ran, 1);
});

test("during the deferred phase it still waits", () => {
  // readyState is already "interactive" while deferred scripts run, which is
  // exactly the window a plain readyState check gets wrong.
  const dom = fakeDom("interactive");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  let ran = 0;
  whenReady(() => ran++);
  assert.equal(ran, 0);

  dom.fire("document", "DOMContentLoaded");
  assert.equal(ran, 1);
});

test("load is the way out when DOMContentLoaded has already been and gone", () => {
  // A bundle injected after DOMContentLoaded sees "interactive" too, and its
  // DOMContentLoaded listener will never fire. Without the second subscription
  // nothing would ever join.
  const dom = fakeDom("interactive");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  let ran = 0;
  whenReady(() => ran++);

  dom.fire("window", "load");
  assert.equal(ran, 1);
});

test("once ready, later callers do not queue behind anything", () => {
  const dom = fakeDom("loading");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  dom.fire("document", "DOMContentLoaded");

  let ran = 0;
  whenReady(() => ran++);
  assert.equal(ran, 1, "a reconnect must not wait for an event that is over");
});

test("the queue runs in the order it was filled, once", () => {
  const dom = fakeDom("loading");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  const order = [];
  whenReady(() => order.push("first"));
  whenReady(() => order.push("second"));

  dom.fire("document", "DOMContentLoaded");
  dom.fire("window", "load");

  assert.deepEqual(order, ["first", "second"]);
});

test("a callback queued from inside a callback still runs", () => {
  const dom = fakeDom("loading");
  const whenReady = createDocumentReady(dom.doc, dom.win);

  const order = [];
  whenReady(() => {
    order.push("outer");
    whenReady(() => order.push("inner"));
  });

  dom.fire("document", "DOMContentLoaded");
  assert.deepEqual(order, ["outer", "inner"]);
});
