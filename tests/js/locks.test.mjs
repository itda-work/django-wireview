import assert from "node:assert/strict";
import { test } from "node:test";

import { Locks, lockKind } from "../../wireview/static/wireview/locks.mjs";

/** An element as much as Locks touches. */
function element(tagName = "INPUT", attrs = {}, type = undefined) {
  const attributes = new Map(Object.entries(attrs));
  return {
    tagName,
    type,
    getAttribute: (name) => (attributes.has(name) ? attributes.get(name) : null),
    hasAttribute: (name) => attributes.has(name),
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
  };
}

test("a text field and a textarea are made read-only, a button disabled, the rest left alone", () => {
  assert.equal(lockKind(element("INPUT", {}, "text")), "readonly");
  assert.equal(lockKind(element("INPUT", {}, "email")), "readonly");
  assert.equal(lockKind(element("input", {}, "number")), "readonly");
  assert.equal(lockKind(element("TEXTAREA")), "readonly");
  assert.equal(lockKind(element("BUTTON", {}, "button")), "disabled");
  assert.equal(lockKind(element("INPUT", {}, "submit")), "disabled");
  assert.equal(lockKind(element("INPUT", {}, "image")), "disabled");
  // No read-only state the browser honours
  for (const type of ["checkbox", "radio", "file", "range", "color", "hidden"]) {
    assert.equal(lockKind(element("INPUT", {}, type)), null, type);
  }
  assert.equal(lockKind(element("SELECT")), null);
  assert.equal(lockKind(element("FIELDSET")), null);
  assert.equal(lockKind(element("OUTPUT")), null);
});

test("an unlocked field gets back what it had", () => {
  const locks = new Locks();
  const plain = element();
  const readOnly = element("INPUT", { readonly: "" });
  locks.lock(plain, "readonly", "loading");
  locks.lock(readOnly, "readonly", "loading");
  assert.ok(plain.hasAttribute("readonly"));
  assert.equal(locks.unlock(plain, "loading"), true);
  assert.equal(locks.unlock(readOnly, "loading"), true);
  assert.equal(plain.hasAttribute("readonly"), false);
  // Read-only before the submit, read-only after it
  assert.equal(readOnly.hasAttribute("readonly"), true);
});

test("a lock two holders share goes with the last of them", () => {
  const locks = new Locks();
  const button = element("BUTTON");
  locks.lock(button, "disabled", "loading");
  locks.lock(button, "disabled", "submitted");
  assert.equal(locks.unlock(button, "loading"), false);
  assert.ok(button.hasAttribute("disabled"));
  assert.equal(locks.holds(button, "submitted"), true);
  assert.equal(locks.unlock(button, "submitted"), true);
  assert.equal(button.hasAttribute("disabled"), false);
});

test("locking again keeps what the element had before the first lock", () => {
  const locks = new Locks();
  const field = element();
  locks.lock(field, "readonly", "loading");
  locks.lock(field, "readonly", "loading");
  locks.unlock(field, "loading");
  assert.equal(field.hasAttribute("readonly"), false);
});

test("letting go of what one does not hold changes nothing", () => {
  const locks = new Locks();
  const field = element();
  assert.equal(locks.unlock(field, "loading"), false);
  locks.lock(field, "readonly", "submitted");
  assert.equal(locks.unlock(field, "loading"), false);
  assert.ok(field.hasAttribute("readonly"));
});

test("own() answers as the page has the element, the lock aside", () => {
  const locks = new Locks();
  const button = element("BUTTON");
  const disabled = element("BUTTON", { disabled: "" });
  locks.lock(button, "disabled", "loading");
  locks.lock(disabled, "disabled", "loading");
  assert.equal(locks.own(button, "disabled"), false);
  assert.equal(locks.own(disabled, "disabled"), true);
  // Unlocked, it is the attribute
  assert.equal(locks.own(element("BUTTON", { disabled: "" }), "disabled"), true);
  // Another attribute than the lock's
  assert.equal(locks.own(button, "readonly"), false);
});

test("a render keeps the lock and its HTML becomes what the element comes back to", () => {
  const locks = new Locks();
  const live = element("BUTTON");
  locks.lock(live, "disabled", "loading");
  // The server now draws the button disabled: the form is done
  const incoming = element("BUTTON", { disabled: "" });
  locks.overlay(live, incoming);
  assert.ok(incoming.hasAttribute("disabled"));
  locks.unlock(live, "loading");
  assert.equal(live.hasAttribute("disabled"), true);

  // And the other way: enabled by the render, the lock still on until the answer
  const field = element("TEXTAREA", { readonly: "" });
  locks.lock(field, "readonly", "loading");
  const unlockedHtml = element("TEXTAREA");
  locks.overlay(field, unlockedHtml);
  assert.ok(unlockedHtml.hasAttribute("readonly"));
  locks.unlock(field, "loading");
  assert.equal(field.hasAttribute("readonly"), false);
});

test("a render of an element nobody locked is left as it is", () => {
  const locks = new Locks();
  const incoming = element("BUTTON");
  locks.overlay(element("BUTTON"), incoming);
  assert.equal(incoming.hasAttribute("disabled"), false);
});

test("a navigation's morph forgets the lock and the next page's element keeps its attributes", () => {
  const locks = new Locks();
  const field = element("INPUT", {}, "text");
  locks.lock(field, "readonly", "loading");
  locks.forget(field);
  // The morph copies the next page's attributes on; the answer then finds no lock
  field.removeAttribute("readonly");
  assert.equal(locks.unlock(field, "loading"), false);
  assert.equal(field.hasAttribute("readonly"), false);
});
