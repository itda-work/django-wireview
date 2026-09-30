import { strict as assert } from "node:assert";
import test from "node:test";

import { NAVIGATED_EVENT, NavigationLog, carriedAcross } from "../../wireview/static/wireview/navigation.mjs";

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
