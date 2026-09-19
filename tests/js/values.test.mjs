import assert from "node:assert/strict";
import { test } from "node:test";

import { isEditableField, keepsUserValue } from "../../wireview/static/wireview/values.mjs";

const field = (over = {}) => ({ edited: true, focused: false, committing: false, serverChanged: false, ...over });

test("a field the user did not edit takes the server's value", () => {
  assert.equal(keepsUserValue(field({ edited: false })), false);
  assert.equal(keepsUserValue(field({ edited: false, focused: true })), false);
});

test("an edited field keeps its value through a render the server did not change it in", () => {
  assert.equal(keepsUserValue(field()), true);
  assert.equal(keepsUserValue(field({ focused: true })), true);
});

test("the answer to an action from the field or its form takes the server's value", () => {
  assert.equal(keepsUserValue(field({ committing: true })), false);
  assert.equal(keepsUserValue(field({ committing: true, focused: true })), false);
});

test("a new server value lands in an unfocused field but not under the cursor", () => {
  assert.equal(keepsUserValue(field({ serverChanged: true })), false);
  assert.equal(keepsUserValue(field({ serverChanged: true, focused: true })), true);
});

test("editable fields are text-like inputs and textareas", () => {
  for (const type of [undefined, "text", "search", "email", "number", "password", "date", "range", "TEXT"]) {
    assert.equal(isEditableField("INPUT", type), true, String(type));
  }
  for (const type of ["checkbox", "radio", "file", "hidden", "submit", "button", "reset", "image"]) {
    assert.equal(isEditableField("INPUT", type), false, type);
  }
  assert.equal(isEditableField("TEXTAREA"), true);
  assert.equal(isEditableField("SELECT"), false);
});

test("submit, change, leaving a field and Enter commit; other events do not (#92)", async () => {
  const { isCommitAction } = await import("../../wireview/static/wireview/values.mjs");
  const { parseBinding } = await import("../../wireview/static/wireview/events.mjs");
  const steps = (attr) => parseBinding(attr).steps;

  for (const type of ["submit", "change", "blur", "focusout"]) assert.equal(isCommitAction(type), true, type);
  assert.equal(isCommitAction("keypress", steps("wire-on-keypress.enter")), true);
  assert.equal(isCommitAction("keydown", steps("wire-on-keydown.key.enter.prevent")), true);
  assert.equal(isCommitAction("keydown", steps("wire-on-keydown.key.arrowdown.prevent")), false, "arrow keys only move a selection");
  assert.equal(isCommitAction("keydown", steps("wire-on-keydown.esc")), false);
  assert.equal(isCommitAction("keypress"), false, "a key event without a key filter could be any key");
  for (const type of ["input", "click", "mouseenter", "custom:thing", undefined]) {
    assert.equal(isCommitAction(type), false, String(type));
  }
});
