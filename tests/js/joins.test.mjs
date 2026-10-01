import assert from "node:assert/strict";
import { test } from "node:test";

import { Joins, settledEvent } from "../../wireview/static/wireview/joins.mjs";

test("the first join under an id replaces nothing; the next one does", () => {
  const joins = new Joins();
  assert.equal(joins.sent("box", 1), false);
  assert.equal(joins.sent("box", 2), true);
});

test("a join after the id left or failed replaces nothing", () => {
  const joins = new Joins();
  joins.sent("box", 1);
  joins.forget("box");
  assert.equal(joins.sent("box", 2), false);
});

test("a connection that closed held no joins for the next one", () => {
  const joins = new Joins();
  joins.sent("box", 1);
  joins.clear();
  assert.equal(joins.sent("box", undefined), false);
});

test("while a named join waits, only its answer is applied", () => {
  // #139, second symptom: the replaced instance's answer named it as current,
  // and a file chosen then went to its config
  const joins = new Joins();
  joins.sent("box", 1);
  joins.sent("box", 2);

  assert.equal(joins.render("box", 1, true), false, "the answer to the join it replaced");
  assert.equal(joins.render("box", undefined, true), false, "a render of the replaced instance");
  assert.equal(joins.render("box", 5, true), false, "the replaced instance answering an event");
  assert.equal(joins.render("box", 2, true), true);
  assert.equal(joins.render("box", undefined, true), true, "after the answer, the id is the new instance's");
  assert.equal(joins.render("box", 9, true), true);
});

test("a render for a component the page let go is not applied", () => {
  // #140: a render on its way when the element left registered its children
  // again. #137: an answer to its join brought its instance back.
  const joins = new Joins();
  joins.sent("box", 1);
  joins.forget("box");
  assert.equal(joins.render("box", 1, false), false);
  assert.equal(joins.render("child", undefined, false), false);
});

test("a component that never joined -- a LiveComponent -- takes its renders", () => {
  const joins = new Joins();
  assert.equal(joins.render("child", undefined, true), true);
  assert.equal(joins.render("child", 4, true), true);
});

test("a join sent without a ref pairs nothing", () => {
  // The first join of a connection, or one to a server older than the ref
  const joins = new Joins();
  joins.sent("box", undefined);
  assert.equal(joins.render("box", undefined, true), true);
  assert.equal(joins.error("box", undefined, "join"), true);
  joins.sent("box", undefined);
  assert.equal(joins.render("box", undefined, true), true);
  assert.equal(joins.error("box", undefined, "event"), true);
});

test("an error for the join the page replaced leaves the new one alone", () => {
  // #139, first symptom: it marked the new element and dropped its component
  const joins = new Joins();
  joins.sent("box", undefined);
  joins.sent("box", 2);
  assert.equal(joins.error("box", undefined, "join"), false);

  joins.sent("box", 3);
  assert.equal(joins.error("box", 2, "join"), false);
  assert.equal(joins.error("box", 3, "join"), true);
});

test("a join that failed after its render is still answered by its error", () => {
  // The render answers first; the error params_changed raised follows it
  const joins = new Joins();
  joins.sent("box", 1);
  assert.equal(joins.render("box", 1, true), true);
  assert.equal(joins.error("box", 1, "join"), true);
});

test("an event's error is the new instance's only once its join is answered", () => {
  const joins = new Joins();
  joins.sent("box", 1);
  joins.render("box", 1, true);
  joins.sent("box", 2);
  assert.equal(joins.error("box", 6, "event"), false, "the replaced instance raised");
  joins.render("box", 2, true);
  assert.equal(joins.error("box", 7, "event"), true);
  assert.equal(joins.error("box", undefined, "event"), true);
});

test("a join's answer settles no event, though it carries a ref", () => {
  // Joins and events share one counter. Read as an event's, a join answer's
  // ref settled every event up to it, and another component's committing
  // event lost the permission to reset its fields.
  assert.equal(settledEvent("render", { id: "box", ref: 6, vsn: 6 }), undefined);
  assert.equal(settledEvent("error", { id: "box", ref: 6, during: "join" }), undefined);
});

test("an event's answer settles that event", () => {
  assert.equal(settledEvent("render", { id: "box", ref: 5 }), 5);
  assert.equal(settledEvent("error", { id: "box", ref: 5, during: "event" }), 5);
});

test("an answer without a ref settles nothing by ref", () => {
  assert.equal(settledEvent("render", { id: "box" }), undefined);
  assert.equal(settledEvent("render", { id: "box", vsn: 6 }), undefined);
  assert.equal(settledEvent("error", { id: "box", during: "event" }), undefined);
});

test("while a named join waits, a remove, reload or joined is its own only with its ref", () => {
  // #146: a halted join's remove took the element the next join was for, and a
  // replaced join's joined started the new element's infinite scroll
  const joins = new Joins();
  joins.sent("box", 1);
  joins.sent("box", 2);

  assert.equal(joins.about("box", 1), false, "the replaced join's");
  assert.equal(joins.about("box", undefined), false, "the replaced instance's own");
  assert.equal(joins.about("box", 2), true, "the waiting join's: a halt, a refusal");
  assert.equal(joins.render("box", 2, true), true);
  assert.equal(joins.about("box", undefined), true, "after the answer, the id is the new instance's");
});

test("asking about an answer answers no join", () => {
  // A remove or reload with the join's ref ends it without a render: the page
  // lets the element go (forget) or loads again, and nothing else is expected
  const joins = new Joins();
  joins.sent("box", 1);
  joins.sent("box", 2);
  assert.equal(joins.about("box", 2), true);
  assert.equal(joins.render("box", undefined, true), false);
});

test("a remove, reload or joined for an id with no named join is taken", () => {
  const joins = new Joins();
  assert.equal(joins.about("box", undefined), true, "never joined on this connection");
  assert.equal(joins.about(null, 4), true, "a reload for a state that did not verify names no id");
  joins.sent("box", undefined);
  joins.sent("box", undefined);
  assert.equal(joins.about("box", undefined), true, "a join to a server that takes no ref");
});

test("a LiveComponent's own render waits with its root's join", () => {
  // #146: the page asks about the root (rootIdOf in wireview.js); the render
  // carries an event's ref or none, never the root's join ref
  const joins = new Joins();
  joins.sent("root", 1);
  joins.render("root", 1, true);
  joins.sent("root", 2);
  assert.equal(joins.render("root", 7, true), false, "the replaced child answering an event");
  assert.equal(joins.render("root", undefined, true), false);
  assert.equal(joins.render("root", 2, true), true);
  assert.equal(joins.render("root", 8, true), true, "the new child's");
});

test("a join the page sent stands over a failure an older render reports", () => {
  // A render made before the page joined the id again (a boosted page's
  // components join behind their root) still marks it wire-join-failed
  const joins = new Joins();
  assert.equal(joins.holds("held"), false);
  joins.sent("held", 4);
  assert.equal(joins.holds("held"), true, "pending");
  joins.render("held", 4, true);
  assert.equal(joins.holds("held"), true, "answered");
  // It failed: the mark is about the page's last join
  joins.forget("held");
  assert.equal(joins.holds("held"), false);
  joins.sent("held");
  joins.clear();
  assert.equal(joins.holds("held"), false);
});
