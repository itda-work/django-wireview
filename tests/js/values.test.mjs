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
