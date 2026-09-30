import assert from "node:assert/strict";
import { test } from "node:test";

import { UploadManagers } from "../../wireview/static/wireview/uploads.mjs";

/** A manager that remembers whether it was disposed and what it holds. */
function fake(id) {
  return { id, files: [], disposed: 0, dispose() { this.disposed += 1; } };
}

test("a component's manager is made once and kept", () => {
  const managers = new UploadManagers(fake);
  const first = managers.get("probe");
  assert.equal(managers.get("probe"), first);
  assert.equal(managers.find("probe"), first);
  assert.equal(managers.find("other"), undefined);
});

test("ending a component disposes its manager and the next one starts empty", () => {
  const managers = new UploadManagers(fake);
  const old = managers.get("probe");
  old.files.push("chosen before the config");

  managers.dispose("probe");

  assert.equal(old.disposed, 1);
  assert.equal(managers.find("probe"), undefined);
  const next = managers.get("probe");
  assert.notEqual(next, old);
  assert.deepEqual(next.files, []);
});

test("a config that arrives after its component left does not bring the manager back", () => {
  const managers = new UploadManagers(fake);
  managers.get("probe");
  managers.dispose("probe");

  assert.equal(managers.forConfig("probe"), null);
  assert.equal(managers.find("probe"), undefined);
});

test("a config for an id replaced by a new join goes nowhere until the new instance renders", () => {
  const managers = new UploadManagers(fake);
  managers.get("probe");
  managers.dispose("probe");
  // A file chosen on the new DOM before its join is answered
  const fresh = managers.get("probe");
  fresh.files.push("new");

  assert.equal(managers.forConfig("probe"), null, "the old instance's config");
  managers.rendered("probe");
  assert.equal(managers.forConfig("probe"), fresh, "the new instance's config");
});

test("a config for a component that never ended makes its manager", () => {
  const managers = new UploadManagers(fake);
  const manager = managers.forConfig("child");
  assert.equal(manager, managers.find("child"));
});

test("a closed connection ends every manager, and configs on the next are accepted", () => {
  const managers = new UploadManagers(fake);
  const a = managers.get("a");
  const b = managers.get("b");
  managers.dispose("gone");

  managers.disposeAll();

  assert.deepEqual([a.disposed, b.disposed], [1, 1]);
  assert.equal(managers.find("a"), undefined);
  // Nothing from the old socket arrives on the new one
  assert.notEqual(managers.forConfig("gone"), null);
  assert.notEqual(managers.forConfig("a"), null);
});

test("disposing an id with no manager is harmless and still drops its late config", () => {
  const managers = new UploadManagers(fake);
  managers.dispose("never");
  assert.equal(managers.forConfig("never"), null);
});
