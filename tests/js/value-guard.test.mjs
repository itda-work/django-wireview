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

import { ValueGuard, commitScope, holdShown, radiosShown } from "../../wireview/static/wireview/values.mjs";

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

test("a field with nothing to type or pick is never kept", () => {
  const { g } = guard();
  const file = { tagName: "INPUT", type: "file", value: "a.png", defaultValue: "", isConnected: true };
  const hidden = { tagName: "INPUT", type: "hidden", value: "x", defaultValue: "y", isConnected: true };

  assert.equal(g.keep(file, { tagName: "INPUT", type: "file", defaultValue: "" }), false);
  assert.equal(g.keep(hidden, { tagName: "INPUT", type: "hidden", defaultValue: "z" }), false);
});

/** A checkbox (or a radio) as the guard sees it. */
function box(checked, defaultChecked = false, type = "checkbox") {
  return { tagName: "INPUT", type, value: "on", defaultValue: "on", checked, defaultChecked, isConnected: true };
}

/** The checkbox a render would morph it into. */
function nextBox(defaultChecked, type = "checkbox") {
  return { tagName: "INPUT", type, value: "on", defaultValue: "on", checked: defaultChecked, defaultChecked };
}

/** A select whose options are `[value, selected, defaultSelected]`. */
function select(options, { multiple = false, size = 0 } = {}) {
  return {
    tagName: "SELECT",
    multiple,
    size,
    isConnected: true,
    options: options.map(([value, selected, defaultSelected = false]) => ({ value, selected, defaultSelected, disabled: false })),
  };
}

test("a render answering an older change leaves a newer tick alone (#271)", () => {
  // The tag-run failure of test_dom_events_e2e[submit-values]: the user ticks,
  // and the answer to an event sent before the tick still says unticked.
  const { g, focus } = guard();
  const other = input("x", "x");
  const field = box(true);
  focus(field);
  g.record(1, new Map([[other, "x"]]));

  assert.equal(g.keep(field, nextBox(false), g.answer(1)), true);
});

test("a newer pick in a select survives the answer to the change that sent the older one (#271)", () => {
  // Picked a and c, which a change sent; then picked b alone before its answer came.
  const { g } = guard();
  const field = select([["a", false, true], ["b", true], ["c", false, true]], { multiple: true });
  const older = select([["a", true, true], ["b", false], ["c", true, true]], { multiple: true });
  g.record(1, new Map([[field, JSON.stringify(["a", "c"])]]));

  assert.equal(g.keep(field, older, g.answer(1)), true, "the server answered a and c; the user since picked b");
});

test("the answer to a toggle's own change takes the server's state, as for text (#271)", () => {
  const { g, focus } = guard();
  const field = box(true);
  focus(field);
  g.record(1, new Map([[field, "checked"]]));

  assert.equal(g.keep(field, nextBox(false), g.answer(1)), false, "the server refused the tick");
});

test("a toggle the user left as the server rendered it follows the server (#271)", () => {
  const { g } = guard();
  assert.equal(g.keep(box(false), nextBox(true)), false);
  assert.equal(g.keep(box(true, true), nextBox(false)), false);
});

test("a tick stands through renders the server did not change it in (#271)", () => {
  // A toggle has two states: once the user flipped it, a server that changed
  // its state now renders what the user picked, so only an unchanged one is kept
  const { g } = guard();
  assert.equal(g.keep(box(true), nextBox(false)), true, "ticked by the user, still unticked on the server");
  assert.equal(g.keep(box(false, true), nextBox(true)), true, "unticked by the user, still ticked on the server");
  assert.equal(g.keep(box(false, true), nextBox(false)), false, "the server unticks it too");
});

test("a drop-down with no option marked shows its first, which is not an edit (#271)", () => {
  const { g } = guard();
  const field = select([["a", true], ["b", false]]);
  assert.equal(g.keep(field, select([["a", false], ["b", false, true]])), false, "unedited: the server's pick lands");

  const picked = select([["a", false], ["b", true]]);
  assert.equal(g.keep(picked, select([["a", false], ["b", false]])), true, "b is the user's");
});

test("a field is only kept against an element of its own kind (#271)", () => {
  const { g } = guard();
  assert.equal(g.keep(box(true), { ...input("x"), type: "text" }), false);
});

test("a form's toggles and selects are committed with what they show (#271)", () => {
  const tick = box(true);
  const pick = select([["a", false], ["b", true]]);
  const text = input("t");
  const button = { tagName: "BUTTON", type: "submit" };
  const f = form([text, tick, pick, button]);

  const sent = commitScope(f, f);
  assert.deepEqual([...sent.keys()], [text, tick, pick]);
  assert.equal(sent.get(tick), "checked");
  assert.equal(sent.get(pick), JSON.stringify(["b"]));
});

test("holdShown puts back the check and the options a morph changed, and nothing for text (#271)", () => {
  const tick = box(true);
  const putBack = holdShown(tick);
  tick.checked = false;
  putBack();
  assert.equal(tick.checked, true);

  const many = select([["a", false, true], ["b", true], ["c", false, true]], { multiple: true });
  const restore = holdShown(many);
  for (const option of many.options) option.selected = option.defaultSelected;
  restore();
  assert.deepEqual(many.options.map((option) => option.selected), [false, true, false]);

  const one = select([["a", false], ["b", true]]);
  const restoreOne = holdShown(one);
  one.options[0].selected = true;
  one.options[1].selected = false;
  restoreOne();
  assert.equal(one.options[1].selected, true);

  assert.equal(holdShown(input("x")), null);

  const gone = box(true);
  const late = holdShown(gone);
  gone.checked = false;
  gone.isConnected = false;
  late();
  assert.equal(gone.checked, false, "a field the morph took out is left as it is");
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

test("a field an IME is composing in keeps its value even when it shows the server's (#169)", () => {
  // The field holds what the server last rendered ("하", its own debounced echo),
  // so it reads as unedited; the server now renders another value. Writing it
  // would end the composition under the IME.
  const { g, focus } = guard();
  const field = input("하", "하");
  focus(field);
  g.compose(field, true);

  assert.equal(g.keep(field, next("HA")), true, "the composition is left alone");
  assert.equal(field.defaultValue, "HA", "the server's value still lands in defaultValue");
});

test("a composition is kept against a committing answer too, and only while it is open (#169)", () => {
  const { g, focus } = guard();
  const field = input("하", "");
  focus(field);
  g.compose(field, true);
  g.record(1, new Map([[field, "하"]]));
  assert.equal(g.keep(field, next(""), g.answer(1)), true, "open: the answer waits");

  g.compose(field, false);
  field.defaultValue = "하";
  assert.equal(g.keep(field, next("HA")), false, "ended and unedited: the server's value applies");
});

test("a composing field that left the page is forgotten (#169)", () => {
  const { g } = guard();
  const field = input("하");
  g.compose(field, true);
  field.isConnected = false;
  g.prune();
  assert.equal(g.composing.size, 0);
});

test("clear() is the connection's: an open composition survives it (#169)", () => {
  const { g, focus } = guard();
  const field = input("하", "하");
  focus(field);
  g.compose(field, true);
  g.clear();
  assert.equal(g.keep(field, next("x")), true);
});

test("a composition dropped without compositionend does not hold a field the user left (#169)", () => {
  // A script wrote another value into the field: the IME dropped the composition
  // and no compositionend came. The user moved on; the server renders a new value.
  const { g, focus } = guard();
  const field = input("x", "하");
  focus(field);
  g.compose(field, true);
  focus(null);

  assert.equal(g.keep(field, next("하!")), false, "not focused: the server's change applies");
});

test("a radio is judged by what it showed before the morph checked the server's one (#271)", () => {
  // The user checked pro; the morph reaches free first and checks it, which
  // unchecks pro before the guard looks at pro.
  const { g } = guard();
  const free = box(false, true, "radio");
  const pro = box(true, false, "radio");
  const root = { tagName: "FORM", querySelectorAll: () => [free, pro] };
  const before = radiosShown(root);

  pro.checked = false; // what checking free did
  assert.equal(g.keep(pro, nextBox(false, "radio")), false, "read now, pro looks untouched");
  assert.equal(g.keep(pro, nextBox(false, "radio"), undefined, before.get(pro)), true, "read before, it is the user's pick");

  const restore = holdShown(pro, before.get(pro));
  restore();
  assert.equal(pro.checked, true);
});
