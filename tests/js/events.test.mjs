import assert from "node:assert/strict";
import { test } from "node:test";

import { bindingsFor, parseBinding, runSteps } from "../../wireview/static/wireview/events.mjs";

/** Ops that record what a run did; debounce keeps its continuation to call later. */
function recorder({ throttleAllows = true } = {}) {
  const log = [];
  /** @type {(() => void) | null} */
  let pending = null;
  return {
    log,
    flush: () => pending?.(),
    ops: {
      prevent: () => log.push("prevent"),
      stop: () => log.push("stop"),
      debounce: (delay, rest) => {
        log.push(`debounce ${delay}`);
        pending = rest;
      },
      throttle: (delay) => {
        log.push(`throttle ${delay}`);
        return throttleAllows;
      },
      fire: () => log.push("fire"),
    },
  };
}

test("parseBinding reads the event and its modifiers, arguments included", () => {
  assert.deepEqual(parseBinding("wire-on-click"), { type: "click", steps: [] });
  assert.deepEqual(parseBinding("wire-on-keyup.debounce.300.enter"), {
    type: "keyup",
    steps: [
      { name: "debounce", arg: "300" },
      { name: "key", arg: "enter" },
    ],
  });
  assert.deepEqual(parseBinding("wire-on-keydown.key.a.ctrl"), {
    type: "keydown",
    steps: [{ name: "key", arg: "a" }, { name: "ctrl" }],
  });
  assert.deepEqual(parseBinding("wire-on-keyup.esc").steps, [{ name: "key", arg: "escape" }]);
  assert.deepEqual(parseBinding("wire-on-keyup.space").steps, [{ name: "key", arg: " " }]);
  assert.equal(parseBinding("wire-upload"), null);
  assert.equal(parseBinding("wire-on-"), null);
});

test("bindingsFor matches the event exactly or with modifiers, not a longer event name", () => {
  const names = ["class", "wire-on-keyup.enter", "wire-on-keyup.esc", "wire-on-keyupx", "wire-on-click"];
  assert.deepEqual(bindingsFor(names, "keyup"), ["wire-on-keyup.enter", "wire-on-keyup.esc"]);
  assert.deepEqual(bindingsFor(names, "click"), ["wire-on-click"]);
  assert.deepEqual(bindingsFor(names, "input"), []);
});

test("a binding with no modifiers fires", () => {
  const r = recorder();
  runSteps([], {}, r.ops);
  assert.deepEqual(r.log, ["fire"]);
});

test("modifiers run left to right, and a condition that fails stops the run", () => {
  const steps = parseBinding("wire-on-keyup.prevent.enter.stop").steps;

  const hit = recorder();
  runSteps(steps, { key: "Enter" }, hit.ops);
  assert.deepEqual(hit.log, ["prevent", "stop", "fire"]);

  const miss = recorder();
  runSteps(steps, { key: "a" }, miss.ops);
  assert.deepEqual(miss.log, ["prevent"], "prevent came before the key check, so it ran");
});

test("modifier keys must be held", () => {
  const steps = parseBinding("wire-on-click.ctrl.shift").steps;
  const held = recorder();
  runSteps(steps, { ctrlKey: true, shiftKey: true }, held.ops);
  assert.deepEqual(held.log, ["fire"]);
  const not = recorder();
  runSteps(steps, { ctrlKey: true }, not.ops);
  assert.deepEqual(not.log, []);
});

test("key matching ignores case, and key_code compares the code", () => {
  const upper = recorder();
  runSteps(parseBinding("wire-on-keydown.key.A").steps, { key: "a" }, upper.ops);
  assert.deepEqual(upper.log, ["fire"]);
  const code = recorder();
  runSteps(parseBinding("wire-on-keydown.key_code.13").steps, { keyCode: 13 }, code.ops);
  assert.deepEqual(code.log, ["fire"]);
});

test("debounce hands the rest of the run to its timer", () => {
  const r = recorder();
  runSteps(parseBinding("wire-on-keyup.debounce.250.enter").steps, { key: "Enter" }, r.ops);
  assert.deepEqual(r.log, ["debounce 250"]);
  r.flush();
  assert.deepEqual(r.log, ["debounce 250", "fire"]);
});

test("a prevent after debounce runs late, as it always did", () => {
  const r = recorder();
  runSteps(parseBinding("wire-on-submit.debounce.100.prevent").steps, {}, r.ops);
  assert.deepEqual(r.log, ["debounce 100"]);
  r.flush();
  assert.deepEqual(r.log, ["debounce 100", "prevent", "fire"]);
});

test("throttle lets a call through or drops it", () => {
  const allowed = recorder();
  runSteps(parseBinding("wire-on-scroll.throttle.100").steps, {}, allowed.ops);
  assert.deepEqual(allowed.log, ["throttle 100", "fire"]);
  const dropped = recorder({ throttleAllows: false });
  runSteps(parseBinding("wire-on-scroll.throttle.100").steps, {}, dropped.ops);
  assert.deepEqual(dropped.log, ["throttle 100"]);
});

test("an unknown modifier is ignored", () => {
  const r = recorder();
  runSteps(parseBinding("wire-on-click.once").steps, {}, r.ops);
  assert.deepEqual(r.log, ["fire"]);
});
