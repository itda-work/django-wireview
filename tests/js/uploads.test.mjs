import assert from "node:assert/strict";
import { test } from "node:test";

import { UploadManagers, answersJoin } from "../../wireview/static/wireview/uploads.mjs";

/** A manager that remembers whether it was disposed and what it holds. */
function fake(id) {
  return { id, files: [], disposed: 0, dispose() { this.disposed += 1; } };
}

/** What wireview.js does with a render: it answers a join only when it says so. */
function render(managers, id, { vsn, serverVsn = 5 } = {}) {
  if (answersJoin(vsn, serverVsn)) managers.answered(id);
}

test("a component's manager is made once and kept", () => {
  const managers = new UploadManagers(fake);
  const first = managers.get("probe");
  assert.equal(managers.get("probe"), first);
  assert.equal(managers.find("probe"), first);
  assert.equal(managers.find("other"), undefined);
});

test("a config after the join's answer goes to the manager", () => {
  const managers = new UploadManagers(fake);
  const early = managers.get("probe");
  early.files.push("chosen before the first join was answered");
  managers.joining("probe");

  assert.equal(managers.forConfig("probe"), null, "nothing answered yet");
  render(managers, "probe", { vsn: 5 });
  assert.equal(managers.forConfig("probe"), early);
});

test("a component that left takes no config, whatever renders arrive after", () => {
  // #137: a render the old instance sent before the server handled the leave
  // used to open the gate again, and the config behind it brought the manager back
  const managers = new UploadManagers(fake);
  managers.joining("left");
  render(managers, "left", { vsn: 5 });
  const old = managers.forConfig("left");
  old.files.push("gone");

  managers.dispose("left");
  render(managers, "left");

  assert.equal(old.disposed, 1);
  assert.equal(managers.forConfig("left"), null);
  assert.equal(managers.find("left"), undefined);
});

test("a join that replaces an instance takes no config until it is answered", () => {
  const managers = new UploadManagers(fake);
  managers.joining("probe", ["child"]);
  render(managers, "probe", { vsn: 5 });
  const old = managers.forConfig("probe");
  const oldChild = managers.forConfig("child");

  managers.joining("probe", ["child"], true);

  assert.deepEqual([old.disposed, oldChild.disposed], [1, 1]);
  // A file chosen on the new DOM before the answer
  const fresh = managers.get("probe");
  fresh.files.push("new");
  // The old instance still renders, and its configs follow
  render(managers, "probe");
  assert.equal(managers.forConfig("probe"), null, "the old instance's config");
  assert.equal(managers.forConfig("child"), null, "its LiveComponent's");

  render(managers, "probe", { vsn: 5 });
  assert.equal(managers.forConfig("probe"), fresh, "the new instance's config");
  assert.notEqual(managers.forConfig("child"), null);
});

test("two joins in a row open the gate only at the second answer", () => {
  // The first join's answer arrives after the page already sent the second;
  // what follows it is the instance the second join retires.
  const managers = new UploadManagers(fake);
  managers.joining("probe");
  managers.joining("probe", [], true);

  render(managers, "probe", { vsn: 5 });
  assert.equal(managers.forConfig("probe"), null);
  render(managers, "probe", { vsn: 5 });
  assert.notEqual(managers.forConfig("probe"), null);
});

test("a component that leaves while its join is unanswered stays closed after the answer", () => {
  const managers = new UploadManagers(fake);
  managers.joining("probe");
  managers.dispose("probe");
  render(managers, "probe", { vsn: 5 });
  assert.equal(managers.forConfig("probe"), null);

  // Until the page joins it again
  managers.joining("probe");
  render(managers, "probe", { vsn: 5 });
  assert.notEqual(managers.forConfig("probe"), null);
});

test("a failed join is its own answer and ends the id", () => {
  const managers = new UploadManagers(fake);
  managers.get("probe").files.push("chosen");
  managers.joining("probe");

  managers.answered("probe");
  managers.dispose("probe");

  assert.equal(managers.forConfig("probe"), null);
  assert.equal(managers.unanswered.size, 0);
});

test("an answer to no join is ignored", () => {
  const managers = new UploadManagers(fake);
  managers.answered("probe");
  managers.joining("probe");
  managers.answered("other");
  assert.equal(managers.forConfig("probe"), null);
});

test("a server that never sends vsn answers a join with any render", () => {
  assert.equal(answersJoin(undefined, 0), true);
  assert.equal(answersJoin(undefined, 5), false);
  assert.equal(answersJoin(5, 0), true);
  assert.equal(answersJoin(5, 5), true);
});

test("a closed connection ends the managers of its instances, once", () => {
  const managers = new UploadManagers(fake);
  managers.joining("a");
  render(managers, "a", { vsn: 5 });
  const a = managers.forConfig("a");

  managers.connectionClosed();
  assert.equal(a.disposed, 1);
  assert.equal(managers.find("a"), undefined);

  // Chosen while offline: no instance's yet
  const offline = managers.get("a");
  offline.files.push("offline");
  // The next attempt fails too
  managers.connectionClosed();
  assert.equal(offline.disposed, 0);
  assert.equal(managers.find("a"), offline);

  // Connected at last: the new instance's config takes the file
  managers.joining("a");
  render(managers, "a", { vsn: 5 });
  assert.equal(managers.forConfig("a"), offline);
  assert.deepEqual(offline.files, ["offline"]);
});

test("a file chosen before the first connection survives a failed attempt", () => {
  const managers = new UploadManagers(fake);
  const early = managers.get("probe");
  early.files.push("early");

  managers.connectionClosed();

  managers.joining("probe");
  render(managers, "probe", { vsn: 5 });
  assert.equal(managers.forConfig("probe"), early);
  assert.equal(early.disposed, 0);
});

test("a closed connection forgets what the old socket owed", () => {
  const managers = new UploadManagers(fake);
  managers.joining("gone");
  managers.dispose("left");

  managers.connectionClosed();

  assert.notEqual(managers.forConfig("gone"), null);
  assert.notEqual(managers.forConfig("left"), null);
});
