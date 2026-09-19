/**
 * The value guard's bookkeeping, without a browser (#92, second round).
 *
 * The implementation review of #92 (docs/design/input-values-review-codex-2026-09-19.md)
 * reproduced its findings against the guard with the DOM and the frame queue
 * mocked. These tests pin the same orderings against the guard's own API, with
 * plain objects standing in for fields: a field is anything with `tagName`,
 * `type`, `value`, `defaultValue` and `isConnected`.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { ValueGuard, commitScope } from "../../wireview/static/wireview/values.mjs";

/** A text input as the guard sees it. */
function input(value, defaultValue = "") {
  return { tagName: "INPUT", type: "text", value, defaultValue, isConnected: true };
}

/** The element a render would morph the field into: only its server value matters. */
function next(serverValue) {
  return { tagName: "INPUT", type: "text", defaultValue: serverValue };
}

/** A guard whose focused element the test sets. */
function guard() {
  const state = { focused: null };
  const g = new ValueGuard({ activeElement: () => state.focused });
  return { g, focus: (field) => (state.focused = field) };
}

test("an answer opens its own fields and no other ref's (a null answer cannot close a later one)", () => {
  // NULL_BEFORE_MORPH: A and B from the same field; A's answer changed nothing,
  // B's empties the field. A's bookkeeping must not take B's permission.
  const { g, focus } = guard();
  const field = input("abc");
  focus(field);
  g.record(1, new Map([[field, "abc"]]));
  g.record(2, new Map([[field, "abc"]]));

  const first = g.answer(1);
  const second = g.answer(2);

  assert.equal(first.size, 1, "A's answer carries A's fields");
  assert.equal(g.keep(field, next(""), second), false, "B's answer empties the field");
});

test("a permission covers only the morph it is passed to (renders cannot share it)", () => {
  // COALESCED_RENDER: after B's answer, an unrelated render with another
  // server value must not use B's permission on the focused field.
  const { g, focus } = guard();
  const field = input("abc");
  focus(field);
  g.record(1, new Map([[field, "abc"]]));
  g.answer(1);

  assert.equal(g.keep(field, next("broadcast")), true, "no permission: the focused edit stays");
});

test("keystrokes after the action was sent survive its answer, focused or not (post-send edits)", () => {
  // POST_SEND_BLURRED: sent abc, typed z, moved away; the answer normalizes to ABC.
  const { g, focus } = guard();
  const blurred = input("abcz", "old");
  g.record(1, new Map([[blurred, "abc"]]));
  focus(null);
  assert.equal(g.keep(blurred, next("ABC"), g.answer(1)), true);

  // POST_SEND_DEFAULT: sent abc, then deleted it all; the field now equals its
  // server value, so it no longer looks edited, but the deletion is the user's.
  const cleared = input("", "");
  focus(cleared);
  g.record(2, new Map([[cleared, "abc"]]));
  assert.equal(g.keep(cleared, next("ABC"), g.answer(2)), true);
});

test("the answer resets exactly what was sent (the todo case)", () => {
  const { g, focus } = guard();
  const field = input("buy milk");
  focus(field);
  g.record(1, new Map([[field, "buy milk"]]));

  assert.equal(g.keep(field, next(""), g.answer(1)), false);
});

test("an answer settles every earlier ref: the server handles a connection's events in order", () => {
  // PENDING: a ref whose event was never answered (a handler that raised, a
  // component that was gone) must not stay forever.
  const { g } = guard();
  const early = input("a");
  const late = input("b");
  g.record(1, new Map([[early, "a"]]));
  g.record(2, new Map([[late, "b"]]));

  g.answer(2);

  assert.equal(g.pendingCount(), 0);
});

test("closing the connection forgets every mark, and a detached field is pruned", () => {
  const { g } = guard();
  const field = input("a");
  const gone = input("b");
  g.record(1, new Map([[field, "a"]]));
  g.record(2, new Map([[gone, "b"]]));
  g.record(null, new Map([[field, "a"]]));

  gone.isConnected = false;
  g.prune();
  assert.equal(g.pendingCount(), 1, "the detached field's ref is gone, the other stays");

  g.clear();
  assert.equal(g.pendingCount(), 0);
  assert.equal(g.keep(field, next(""), undefined), true, "no unpaired mark left either");
});

test("with a server that echoes no refs, the first morph that touches a marked field uses the mark", () => {
  // The #91 behaviour, kept for an older server.
  const { g, focus } = guard();
  const field = input("buy milk");
  focus(field);
  g.record(null, new Map([[field, "buy milk"]]));

  assert.equal(g.keep(field, next("")), false, "the first morph takes the server value");
  field.value = "next item";
  assert.equal(g.keep(field, next("")), true, "the mark was used once");
});

test("a field that is not editable is never kept", () => {
  const { g } = guard();
  const box = { tagName: "INPUT", type: "checkbox", value: "on", defaultValue: "off", isConnected: true };

  assert.equal(g.keep(box, { tagName: "INPUT", type: "checkbox", defaultValue: "on" }), false);
});

/** A form whose fields and elements the test lists. */
function form(fields) {
  const f = { tagName: "FORM", elements: fields, contains: (el) => fields.includes(el) };
  for (const field of fields) field.form = f;
  return f;
}

test("the committed fields are the ones sent: the element's form when it is the scope sent, else the element", () => {
  // MYSELF_SCOPE: a child LiveComponent inside an ancestor form, targeted with
  // myself; only the child's scope is sent, so only the element is committed.
  const a = input("a");
  const b = input("b");
  const outer = form([a, b]);
  const component = { tagName: "DIV", contains: () => true };

  assert.deepEqual([...commitScope(a, outer).keys()], [a, b], "the form is what gets sent");
  assert.deepEqual([...commitScope(a, component).keys()], [a], "the form lies outside what gets sent");
  assert.equal(commitScope(a, outer).get(b), "b", "with the values as sent");

  const button = { tagName: "BUTTON", type: "submit" };
  assert.deepEqual([...commitScope(button, component).keys()], [], "a button outside a sent form commits nothing");
});
