// The documentation site's table of contents: which section is being read, and how far the
// table scrolls to show it (scripts/docs_site/assets/site.js, #159).
import { strict as assert } from "node:assert";
import { createRequire } from "node:module";
import test from "node:test";

const { currentIndex, scrollToShow } = createRequire(import.meta.url)("../../scripts/docs_site/assets/site.js");

const TOPS = [400, 1200, 1300, 2600];
const page = (scrollY, extra = {}) => ({ scrollY, viewport: 800, height: 4000, line: 88, ...extra });

test("above the first heading nothing is current", () => {
  assert.equal(currentIndex(TOPS, page(0)), -1);
  assert.equal(currentIndex(TOPS, page(311)), -1);
});

test("a heading becomes current when it reaches the reading line", () => {
  assert.equal(currentIndex(TOPS, page(312)), 0);
  assert.equal(currentIndex(TOPS, page(1111)), 0);
  assert.equal(currentIndex(TOPS, page(1112)), 1);
});

test("the last heading past the line wins", () => {
  assert.equal(currentIndex(TOPS, page(1250)), 2);
  assert.equal(currentIndex(TOPS, page(2511)), 2);
  assert.equal(currentIndex(TOPS, page(2512)), 3);
});

test("at the end of the page the last heading is current though it never reached the line", () => {
  const short = [400, 1200, 3700, 3800];
  assert.equal(currentIndex(short, page(3198)), 1);
  assert.equal(currentIndex(short, page(3200)), 3);
  // A fractional scroll offset that stops just short of the end still counts.
  assert.equal(currentIndex(short, page(3199.2)), 3);
});

test("a page that does not scroll marks nothing", () => {
  assert.equal(currentIndex(TOPS, page(0, { height: 800 })), -1);
});

test("no headings, no current", () => {
  assert.equal(currentIndex([], page(500)), -1);
});

test("an item in view leaves the table where it is", () => {
  assert.equal(scrollToShow({ scrollTop: 100, height: 300 }, { top: 200, height: 20 }, 10), 100);
});

test("an item below the view scrolls the table just enough", () => {
  assert.equal(scrollToShow({ scrollTop: 0, height: 300 }, { top: 400, height: 20 }, 10), 130);
});

test("an item above the view scrolls the table back, never past its top", () => {
  assert.equal(scrollToShow({ scrollTop: 500, height: 300 }, { top: 200, height: 20 }, 10), 190);
  assert.equal(scrollToShow({ scrollTop: 500, height: 300 }, { top: 5, height: 20 }, 10), 0);
});

test("an item taller than the view shows its top", () => {
  assert.equal(scrollToShow({ scrollTop: 0, height: 30 }, { top: 100, height: 40 }, 10), 90);
});
