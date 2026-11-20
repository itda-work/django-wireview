import assert from "node:assert/strict";
import { test } from "node:test";

import { chainIn, comesBack, relativeTarget, toggledAttribute } from "../../wireview/static/wireview/js-chains.mjs";

test("a relative target is read from its object, a selector string is not one", () => {
  assert.deepEqual(relativeTarget({ inner: ".a" }), { kind: "inner", selector: ".a" });
  assert.deepEqual(relativeTarget({ closest: ".card" }), { kind: "closest", selector: ".card" });
  assert.equal(relativeTarget("#a"), null);
  assert.equal(relativeTarget(null), null);
  assert.equal(relativeTarget({ outer: ".a" }), null);
});

test("toggle_attr with one value adds the attribute when absent and removes it when there", () => {
  assert.equal(toggledAttribute(null, { val: "" }), "");
  assert.equal(toggledAttribute("", { val: "" }), null);
  assert.equal(toggledAttribute("x", { val: "y" }), null, "there with another value is still there");
  assert.equal(toggledAttribute(null, {}), "");
});

test("toggle_attr with two values goes to the other one, and to the first from anything else", () => {
  const cmd = { vals: /** @type {[string, string]} */ (["true", "false"]) };
  assert.equal(toggledAttribute("true", cmd), "false");
  assert.equal(toggledAttribute("false", cmd), "true");
  assert.equal(toggledAttribute(null, cmd), "true");
  assert.equal(toggledAttribute("maybe", cmd), "true");
});

test("exec reads a JS() as it renders, or a binding's chain", () => {
  assert.deepEqual(chainIn('[{"cmd":"hide"}]'), { commands: [{ cmd: "hide" }] });
  assert.deepEqual(chainIn('{"js":[{"cmd":"show"}]}'), { commands: [{ cmd: "show" }] });
  assert.deepEqual(chainIn(null), { missing: true });
  assert.ok("unreadable" in chainIn("hide"));
  assert.ok("unreadable" in chainIn('{"h":"save"}'), "a binding that calls a handler holds no chain");
});

test("an exec that comes back to an attribute it is running is caught, the same attribute elsewhere is not", () => {
  const a = {};
  const b = {};
  const running = /** @type {Array<[object, string]>} */ ([[a, "data-x"]]);
  assert.ok(comesBack(running, a, "data-x"));
  assert.ok(!comesBack(running, b, "data-x"));
  assert.ok(!comesBack(running, a, "data-y"));
});
