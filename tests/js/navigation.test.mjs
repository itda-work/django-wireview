import { strict as assert } from "node:assert";
import test from "node:test";

import {
  NAVIGATED_EVENT,
  NavigationLog,
  PAGE_KEY,
  carriedAcross,
  isPatch,
  newPageId,
  patchesPage,
  returnsToPatch,
  stamped,
} from "../../wireview/static/wireview/navigation.mjs";

const entry = (hook, navigation, connected = true) => ({ hook, navigation, connected });

test("the event has the documented name", () => {
  assert.equal(NAVIGATED_EVENT, "wireview:navigated");
});

test("a hook that was on the page before the navigation hears it", () => {
  assert.deepEqual(carriedAcross([entry("player", 0)], 1), ["player"]);
  assert.deepEqual(carriedAcross([entry("player", 2)], 3), ["player"]);
});

test("a hook the navigation brought hears mounted, not navigated", () => {
  // A Back paints the cached page, mounting its hooks under the navigation's
  // own token, before the fetched page lands
  assert.deepEqual(carriedAcross([entry("old", 0), entry("cached", 1)], 1), ["old"]);
});

test("a hook the morph removed is left to destroyed", () => {
  assert.deepEqual(carriedAcross([entry("gone", 0, false), entry("kept", 0)], 1), ["kept"]);
});

test("the log says where each navigation came from", () => {
  const log = new NavigationLog("http://x/a/");
  assert.deepEqual(log.landed("http://x/b/"), { url: "http://x/b/", previousUrl: "http://x/a/" });
  // A Back has already moved the address bar; the log still knows the page it left
  assert.deepEqual(log.landed("http://x/a/"), { url: "http://x/a/", previousUrl: "http://x/b/" });
});

test("a patch moves the log without an announcement (#169)", () => {
  const log = new NavigationLog("http://x/a/");
  log.patched("http://x/a/?tab=b");
  assert.deepEqual(log.landed("http://x/b/"), { url: "http://x/b/", previousUrl: "http://x/a/?tab=b" });
});

test("a move on the same path is a patch; another path or origin is not (#169)", () => {
  assert.equal(isPatch("http://x/a/", "?tab=b"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "?tab=c"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "#top"), true);
  assert.equal(isPatch("http://x/a/", "/a/?tab=b"), true);
  assert.equal(isPatch("http://x/a/", "http://x/a/?tab=b"), true);
  assert.equal(isPatch("http://x/a/?tab=b", "/a/"), true, "dropping the query stays on the page");
  assert.equal(isPatch("http://x/a/", "/b/"), false);
  assert.equal(isPatch("http://x/a/", "/a"), false, "another path, though Django may redirect it");
  assert.equal(isPatch("http://x/a/", "http://y/a/"), false);
});

test("stamping keeps what the entry held and survives a null state (#169)", () => {
  assert.deepEqual(stamped(null, "p1"), { [PAGE_KEY]: "p1" });
  assert.deepEqual(stamped({ content: "<body>", scrollY: 3 }, "p2"), { content: "<body>", scrollY: 3, [PAGE_KEY]: "p2" });
  assert.deepEqual(stamped({ [PAGE_KEY]: "old" }, "new"), { [PAGE_KEY]: "new" });
});

test("page ids differ, so a reloaded page owns none of the old entries (#169)", () => {
  const ids = new Set(Array.from({ length: 100 }, () => newPageId()));
  assert.equal(ids.size, 100);
});

test("Back or Forward is a patch only to an entry the page on screen made, on its path (#169)", () => {
  const page = { id: "p1", url: "http://x/a/" };
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/a/?tab=b", page), true);
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1", content: "<body>" }, "http://x/a/", page), true);
  // Another page's entry, or one from before a reload
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p0" }, "http://x/a/?tab=b", page), false);
  // An entry a boosted push left behind, or a page loaded before this code
  assert.equal(returnsToPatch({ content: "<body>" }, "http://x/a/", page), false);
  assert.equal(returnsToPatch(null, "http://x/a/", page), false);
  // Its id, but not its path: never made by a patch, so fetched
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/b/", page), false);
});

test("a relative URL is resolved against the document's base, as pushState and fetch resolve it (#169)", () => {
  // <base href="/b/"> on the page /a/: "?x" goes to /b/?x, another path
  assert.equal(isPatch("http://x/a/", "?x", "http://x/b/"), false);
  assert.equal(isPatch("http://x/a/", "?x", "http://x/a/"), true);
  assert.equal(isPatch("http://x/a/", "/a/?x", "http://x/b/"), true, "an absolute path ignores the base");
  assert.equal(patchesPage({ id: "p1", url: "http://x/a/" }, "?x", "http://x/b/"), false);
});

test("a push is judged against the page on screen, not the address bar (#169)", () => {
  // A push to /b/ moved the address bar before its fetch: "?tab=b" from the page
  // still on screen is not a patch of /b/
  const page = { id: "p1", url: "http://x/a/" };
  assert.equal(patchesPage(page, "/b/?tab=b", "http://x/b/"), false);
  assert.equal(patchesPage(page, "/a/?tab=b", "http://x/b/"), true);
});

test("nothing is a patch while a fetching navigation is in flight (#169)", () => {
  const leaving = { id: null, url: "http://x/a/" };
  assert.equal(patchesPage(leaving, "?tab=b", "http://x/a/"), false);
  // A cached paint shows another page's copy: no entry is the page's own, not
  // even one stamped null
  assert.equal(returnsToPatch({ [PAGE_KEY]: "p1" }, "http://x/a/?tab=b", leaving), false);
  assert.equal(returnsToPatch({ [PAGE_KEY]: null }, "http://x/a/?tab=b", leaving), false);
  assert.equal(returnsToPatch({}, "http://x/a/?tab=b", leaving), false);
});
