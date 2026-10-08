// The "ask before leaving unsaved changes" guard docs/features/boost.md shows,
// run as written (#154). A first version set its "already allowed" flag for an
// allowed patch too: the page stayed, the form was still dirty, and a reload
// afterwards left without a word.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const MARKER = "// 저장하지 않은 변경이 있으면 묻는다";
const doc = readFileSync(new URL("../../docs/features/boost.md", import.meta.url), "utf8");

/** The documented block, from its first line to the end of the fence. */
function guardCode() {
  const start = doc.indexOf(MARKER);
  assert.notEqual(start, -1, "the guard example is in boost.md");
  return doc.slice(start).split("```")[0];
}

/**
 * A page with the guard installed. `answers` are what confirm() returns, in order.
 * @param {boolean[]} answers
 */
function page(answers = []) {
  const document = new EventTarget();
  const window = new EventTarget();
  const asked = [];
  vm.runInNewContext(guardCode(), {
    document,
    window,
    confirm: (message) => {
      asked.push(message);
      return answers.shift() ?? true;
    },
  });
  const guarded = { closest: (selector) => (selector === "form[data-guard]" ? {} : null) };
  return {
    asked,
    type(target = guarded) {
      const event = new Event("input");
      Object.defineProperty(event, "target", { value: target });
      document.dispatchEvent(event);
    },
    /** @returns {boolean} whether the move goes ahead */
    move(detail, cancelable = true) {
      const event = new Event("wireview:before-navigate", { cancelable });
      event.detail = { url: "http://x/b/", kind: "link", patch: false, ...detail };
      return document.dispatchEvent(event);
    },
    /** @returns {boolean} whether leaving the document asks */
    unload() {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    },
    landed() {
      document.dispatchEvent(new Event("wireview:navigated"));
    },
    failed() {
      document.dispatchEvent(new Event("wireview:navigation-failed"));
    },
  };
}

test("a clean page moves and unloads without asking", () => {
  const p = page();
  assert.equal(p.move({}), true);
  assert.equal(p.unload(), false);
  assert.deepEqual(p.asked, []);
});

test("input outside a guarded form is not guarded", () => {
  const p = page();
  p.type({ closest: () => null });
  assert.equal(p.move({}), true);
  assert.deepEqual(p.asked, []);
});

test("a dirty form asks, and a no keeps the page", () => {
  const p = page([false]);
  p.type();
  assert.equal(p.move({}), false);
  assert.equal(p.asked.length, 1);
  assert.equal(p.unload(), true, "still guarded");
});

test("an allowed patch keeps guarding the page it stays on", () => {
  // The review's case: push_to on the same path, allowed, and no navigated after it
  const p = page([true]);
  p.type();
  assert.equal(p.move({ kind: "push", patch: true }), true);
  assert.equal(p.unload(), true, "a reload after the patch must still ask");
});

test("an allowed move does not ask twice when it becomes a page load", () => {
  // A boundary crossing or an unanswered fetch hands the move to the browser
  const p = page([true]);
  p.type();
  assert.equal(p.move({}), true);
  assert.equal(p.unload(), false);
});

test("a move that landed leaves a clean page", () => {
  const p = page([true]);
  p.type();
  p.move({});
  p.landed();
  assert.equal(p.unload(), false);
  assert.equal(p.move({}), true);
  assert.equal(p.asked.length, 1);
});

test("a form that could not be sent guards again", () => {
  const p = page([true]);
  p.type();
  p.move({ kind: "form", form: { matches: () => false } });
  p.failed();
  assert.equal(p.unload(), true);
});

test("typing again after an allowed move that stayed guards again", () => {
  const p = page([true]);
  p.type();
  p.move({});
  p.type();
  assert.equal(p.unload(), true);
});

test("sending the guarded form itself is saving, not leaving", () => {
  const p = page();
  p.type();
  assert.equal(p.move({ kind: "form", form: { matches: (selector) => selector === "[data-guard]" } }), true);
  assert.deepEqual(p.asked, []);
});

test("a move that cannot be cancelled is not asked about", () => {
  const p = page([false]);
  p.type();
  assert.equal(p.move({ kind: "redirect" }, false), true);
  assert.deepEqual(p.asked, []);
});
