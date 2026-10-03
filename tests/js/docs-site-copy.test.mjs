// The documentation site's copy button: what a code block copies, and which way it reaches the
// clipboard (scripts/docs_site/assets/site.js, #173). The browser itself is
// tests/test_docs_site_copy_e2e.py.
import { strict as assert } from "node:assert";
import { createRequire } from "node:module";
import test from "node:test";

const { codeText, copyText } = createRequire(import.meta.url)("../../scripts/docs_site/assets/site.js");

test("the newline that ends a block is not copied", () => {
  assert.equal(codeText("make test\n"), "make test");
  assert.equal(codeText("a\nb\n\n"), "a\nb");
});

test("everything else is copied as it is", () => {
  assert.equal(codeText("  indented\n\nblank line above"), "  indented\n\nblank line above");
  assert.equal(codeText("\nleading newline"), "\nleading newline");
  assert.equal(codeText(""), "");
});

const never = () => {
  throw new Error("the fallback ran");
};

test("the Clipboard API is used where the page has it", async () => {
  const written = [];
  const clipboard = { writeText: async (text) => void written.push(text) };
  assert.equal(await copyText("x", clipboard, never), true);
  assert.deepEqual(written, ["x"]);
});

test("without the Clipboard API the fallback copies", async () => {
  const copied = [];
  const fallback = (text) => (copied.push(text), true);
  assert.equal(await copyText("x", undefined, fallback), true);
  assert.equal(await copyText("y", {}, fallback), true);
  assert.deepEqual(copied, ["x", "y"]);
});

test("a refused Clipboard API falls back", async () => {
  const copied = [];
  const clipboard = { writeText: async () => Promise.reject(new DOMException("denied", "NotAllowedError")) };
  assert.equal(await copyText("x", clipboard, (text) => (copied.push(text), true)), true);
  assert.deepEqual(copied, ["x"]);
});

test("a Clipboard API that throws at once falls back", async () => {
  const clipboard = {
    writeText() {
      throw new TypeError("not allowed");
    },
  };
  assert.equal(await copyText("x", clipboard, () => true), true);
});

test("a fallback that cannot copy is a failure", async () => {
  assert.equal(await copyText("x", undefined, () => false), false);
  assert.equal(await copyText("x", undefined, never), false);
  const refused = { writeText: async () => Promise.reject(new Error("denied")) };
  assert.equal(await copyText("x", refused, () => false), false);
});
