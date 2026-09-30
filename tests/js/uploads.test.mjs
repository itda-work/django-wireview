import assert from "node:assert/strict";
import { test } from "node:test";

import { Joins } from "../../wireview/static/wireview/joins.mjs";
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

test("a config from the instance a render named goes to the manager", () => {
  const managers = new UploadManagers(fake);
  const early = managers.get("probe");
  early.files.push("chosen before the first join was answered");

  assert.equal(managers.forConfig("probe", 1), null, "no render named an instance yet");
  managers.started("probe", 1);
  assert.equal(managers.forConfig("probe", 1), early);
});

test("a component that left takes no config, whatever arrives after", () => {
  // #137: a render the old instance sent before the server handled the leave
  // used to open the gate again, and the config behind it brought the manager back
  const managers = new UploadManagers(fake);
  managers.started("left", 1);
  const old = managers.forConfig("left", 1);
  old.files.push("gone");

  managers.dispose("left");

  assert.equal(old.disposed, 1);
  assert.equal(managers.forConfig("left", 1), null);
  assert.equal(managers.find("left"), undefined);
});

test("a join that replaces an instance takes only the new instance's configs", () => {
  const managers = new UploadManagers(fake);
  managers.started("probe", 1);
  managers.started("child", 2);
  const old = managers.forConfig("probe", 1);
  const oldChild = managers.forConfig("child", 2);

  // The page sends the join: the instance and its LiveComponent's are over
  managers.dispose("probe");
  managers.dispose("child");
  assert.deepEqual([old.disposed, oldChild.disposed], [1, 1]);
  // A file chosen on the new DOM before the answer
  const fresh = managers.get("probe");
  fresh.files.push("new");
  assert.equal(managers.forConfig("probe", 1), null, "the old instance's config, before the answer");

  managers.started("probe", 3);
  managers.started("child", 4);
  assert.equal(managers.forConfig("probe", 1), null, "the old instance's config, after it");
  assert.equal(managers.forConfig("child", 2), null, "its LiveComponent's");
  assert.equal(managers.forConfig("probe", 3), fresh, "the new instance's config");
  assert.notEqual(managers.forConfig("child", 4), null);
});

test("a LiveComponent shown again under its id takes its new instance's config", () => {
  // It never sends a join: the parent's render names the new instance. Closing
  // the id until a join came kept it closed for good.
  const managers = new UploadManagers(fake);
  managers.started("child", 2);
  managers.forConfig("child", 2);
  managers.dispose("child");

  managers.started("child", 5);
  const again = managers.forConfig("child", 5);
  assert.notEqual(again, null);
  assert.equal(again.disposed, 0);
});

test("a join answered twice does not shift what the next join takes", () => {
  // A render, then an error: the params handler raised after the first render.
  // Counting answers, the error took the answer the next join was owed.
  const managers = new UploadManagers(fake);
  managers.started("probe", 5);
  managers.dispose("probe");

  managers.started("probe", 6);
  assert.equal(managers.forConfig("probe", 5), null);
  assert.notEqual(managers.forConfig("probe", 6), null);
});

test("an instance a later render replaces loses the manager it configured", () => {
  // The first join's answer arrived after the page sent the second: for a while
  // the old instance looked current, and its config was taken
  const managers = new UploadManagers(fake);
  managers.started("probe", 1);
  const old = managers.forConfig("probe", 1);

  managers.started("probe", 2);
  assert.equal(old.disposed, 1);
  const fresh = managers.forConfig("probe", 2);
  assert.notEqual(fresh, old);
  assert.equal(managers.forConfig("probe", 1), null);
});

test("a manager nobody configured stays when a render names the instance", () => {
  const managers = new UploadManagers(fake);
  managers.started("probe", 1);
  const waiting = managers.get("probe");
  managers.started("probe", 2);
  assert.equal(waiting.disposed, 0);
  assert.equal(managers.forConfig("probe", 2), waiting);
});

test("a config from a server that does not number instances is taken", () => {
  const managers = new UploadManagers(fake);
  const manager = managers.forConfig("probe", undefined);
  assert.notEqual(manager, null);

  managers.connectionClosed();
  assert.equal(manager.disposed, 1);
});

test("a closed connection ends the managers of its instances, once", () => {
  const managers = new UploadManagers(fake);
  managers.started("a", 1);
  const a = managers.forConfig("a", 1);

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
  managers.started("a", 7);
  assert.equal(managers.forConfig("a", 7), offline);
  assert.deepEqual(offline.files, ["offline"]);
});

test("a file chosen before the first connection survives a failed attempt", () => {
  const managers = new UploadManagers(fake);
  const early = managers.get("probe");
  early.files.push("early");

  managers.connectionClosed();

  managers.started("probe", 1);
  assert.equal(managers.forConfig("probe", 1), early);
  assert.equal(early.disposed, 0);
});

test("a closed connection forgets the instances it held", () => {
  const managers = new UploadManagers(fake);
  managers.started("gone", 1);

  managers.connectionClosed();

  assert.equal(managers.forConfig("gone", 1), null);
});

// The page's guards (#142). wireview.js calls these in the order below; each
// test states what the page used to lose when the guard was not there.

test("the first join replaces nothing: files chosen before it wait for its config", () => {
  const managers = new UploadManagers(fake);
  const joins = new Joins();
  const early = managers.get("probe");
  early.files.push("chosen before the join");

  managers.joining("probe", ["child"], joins.sent("probe", undefined));

  assert.equal(early.disposed, 0);
  managers.named({ probe: 1 });
  assert.equal(managers.forConfig("probe", 1), early);
});

test("a join that replaces an instance ends its uploads and its LiveComponents'", () => {
  // Kept, a file chosen on the new DOM before the new answer was registered
  // with the old config, and dropped when the answer arrived: the user's file
  // silently gone
  const managers = new UploadManagers(fake);
  const joins = new Joins();
  managers.joining("probe", ["child"], joins.sent("probe", 1));
  joins.render("probe", 1, true);
  managers.named({ probe: 1, child: 2 });
  const old = managers.forConfig("probe", 1);
  const oldChild = managers.forConfig("child", 2);

  managers.joining("probe", ["child"], joins.sent("probe", 3));

  assert.deepEqual([old.disposed, oldChild.disposed], [1, 1]);
  const fresh = managers.get("probe");
  fresh.files.push("chosen on the new DOM");
  assert.equal(managers.forConfig("probe", 1), null, "the old config, on its way");
  assert.equal(managers.forConfig("child", 2), null);
  assert.equal(joins.render("probe", 3, true), true);
  managers.named({ probe: 4 });
  assert.equal(managers.forConfig("probe", 4), fresh);
  assert.deepEqual(fresh.files, ["chosen on the new DOM"]);
});

test("a failed join ends the uploads of its LiveComponents too", () => {
  // Their configs may already be on their way: kept, one made a manager for
  // an id with no instance, a zombie nothing would end
  const managers = new UploadManagers(fake);
  managers.named({ probe: 1, child: 2 });
  const own = managers.forConfig("probe", 1);
  const child = managers.forConfig("child", 2);

  managers.joinFailed("probe", ["child"]);

  assert.deepEqual([own.disposed, child.disposed], [1, 1]);
  assert.equal(managers.forConfig("child", 2), null);
  assert.equal(managers.find("child"), undefined);
});

test("a late answer for a component the page let go does not name its instance", () => {
  // Answered after the element left: the answer used to bring the instance
  // back, and its config after it
  const managers = new UploadManagers(fake);
  const joins = new Joins();
  managers.joining("probe", [], joins.sent("probe", 1));
  joins.forget("probe");
  managers.dispose("probe");

  if (joins.render("probe", 1, false)) managers.named({ probe: 5 });

  assert.equal(managers.forConfig("probe", 5), null);
  assert.equal(managers.find("probe"), undefined);
});

test("the answer to a join the page replaced does not name its instance", () => {
  // #139: a file chosen then went to that instance's config, and the answer
  // that followed ended it with the file
  const managers = new UploadManagers(fake);
  const joins = new Joins();
  managers.joining("probe", [], joins.sent("probe", 1));
  managers.joining("probe", [], joins.sent("probe", 2));

  if (joins.render("probe", 1, true)) managers.named({ probe: 5 });
  assert.equal(managers.forConfig("probe", 5), null);
  const waiting = managers.get("probe");
  waiting.files.push("chosen in between");

  if (joins.render("probe", 2, true)) managers.named({ probe: 6 });
  assert.equal(managers.forConfig("probe", 6), waiting);
  assert.equal(waiting.disposed, 0);
});
