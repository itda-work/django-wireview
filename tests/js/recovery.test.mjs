import assert from "node:assert/strict";
import { test } from "node:test";

import { RECOVERY_OFF, Recoveries, recoveryOf, stillRecovers } from "../../wireview/static/wireview/recovery.mjs";

test("a bare attribute replays the change, a name is a handler, false turns it off", () => {
  assert.deepEqual(recoveryOf(""), { kind: "change" });
  assert.deepEqual(recoveryOf("true"), { kind: "change" });
  assert.deepEqual(recoveryOf("restore"), { kind: "handler", handler: "restore" });
  assert.deepEqual(recoveryOf(RECOVERY_OFF), { kind: "off" });
  assert.equal(recoveryOf(null), null);
});

test("ignore is still a handler's name, as it always was", () => {
  assert.deepEqual(recoveryOf("ignore"), { kind: "handler", handler: "ignore" });
});

const form = { name: "draft" };
const armed = { form, id: "draft", owner: "box" };
const found = (over = {}) => ({ connected: true, id: "draft", owner: "box", value: "restore", ...over });

test("the same form, still asking, is recovered through what it asks for now", () => {
  assert.deepEqual(stillRecovers(armed, found()), { kind: "handler", handler: "restore" });
  assert.deepEqual(stillRecovers(armed, found({ value: "" })), { kind: "change" });
});

test("a form the render dropped, renamed, moved or turned off is not recovered", () => {
  assert.equal(stillRecovers(armed, null), null);
  assert.equal(stillRecovers(armed, found({ connected: false })), null);
  assert.equal(stillRecovers(armed, found({ id: "draft-2" })), null);
  assert.equal(stillRecovers(armed, found({ owner: "other" })), null);
  assert.equal(stillRecovers(armed, found({ value: "false" })), null);
  assert.equal(stillRecovers(armed, found({ value: null })), null);
});

test("a form without an id is the same form only while it has none", () => {
  const anonymous = { form, id: null, owner: "box" };
  assert.deepEqual(stillRecovers(anonymous, found({ id: null })), { kind: "handler", handler: "restore" });
  assert.equal(stillRecovers(anonymous, found()), null);
});

test("a landed join gives its forms back once", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", 4, [armed]);
  assert.deepEqual(recoveries.landed("box", 4), { forms: [armed], afterError: false });
  assert.deepEqual(recoveries.landed("box", 4).forms, []);
});

test("the answer to another join under the id gives nothing back", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", 4, [armed]);
  assert.deepEqual(recoveries.landed("box", 3).forms, []);
  assert.deepEqual(recoveries.landed("box", 4).forms, [armed]);
});

test("a join without a ref -- a reconnect's first round -- lands on any joined", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", undefined, [armed]);
  assert.deepEqual(recoveries.landed("box", undefined).forms, [armed]);
});

test("a later join under the id that recovers nothing drops what the earlier one held", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", 4, [armed]);
  recoveries.arm("box", 5, []);
  assert.deepEqual(recoveries.landed("box", 4).forms, []);
  assert.deepEqual(recoveries.landed("box", 5).forms, []);
});

test("a join that will not land, or a closed connection, gives nothing back", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", 4, [armed]);
  recoveries.drop("box");
  assert.deepEqual(recoveries.landed("box", 4).forms, []);
  recoveries.arm("box", 6, [armed]);
  recoveries.clear();
  assert.deepEqual(recoveries.landed("box", 6).forms, []);
});

test("a rollback whose recovery raises again is not recovered a second time", () => {
  const recoveries = new Recoveries();
  assert.equal(recoveries.recoversAfterError("box"), true);
  recoveries.arm("box", 7, [armed], { afterError: true });
  assert.equal(recoveries.landed("box", 7).afterError, true);
  recoveries.recovered("box");
  // The recovery raised: no answer came between the two errors
  assert.equal(recoveries.recoversAfterError("box"), false);
  // The error after that one recovers again
  assert.equal(recoveries.recoversAfterError("box"), true);
});

test("something the user did between two errors lets the second recover", () => {
  const recoveries = new Recoveries();
  recoveries.recovered("box");
  recoveries.recovered("other");
  recoveries.userActed();
  assert.equal(recoveries.recoversAfterError("box"), true);
  assert.equal(recoveries.recoversAfterError("other"), true);
});

test("the guard is per component", () => {
  const recoveries = new Recoveries();
  recoveries.recovered("box");
  assert.equal(recoveries.recoversAfterError("other"), true);
  assert.equal(recoveries.recoversAfterError("box"), false);
});

test("a clear starts another generation: what was taken before it goes nowhere", () => {
  const recoveries = new Recoveries();
  recoveries.arm("box", 4, [armed]);
  const before = recoveries.generation;
  assert.deepEqual(recoveries.landed("box", 4).forms, [armed]);
  recoveries.clear();
  assert.notEqual(recoveries.generation, before);
});
