import { strict as assert } from "node:assert";
import test from "node:test";

import { META_NAME, crossesBoundary, readSessionName } from "../../wireview/static/wireview/live-session.mjs";

/** A document stand-in that answers the one selector the module asks for. */
function docWith(content) {
  return {
    querySelector(selector) {
      if (selector !== `meta[name="${META_NAME}"]`) return null;
      if (content === undefined) return null;
      return { getAttribute: () => content };
    },
  };
}

test("a page inside a live_session reports its name", () => {
  assert.equal(readSessionName(docWith("admin")), "admin");
});

test("a page with no meta reads as no boundary", () => {
  assert.equal(readSessionName(docWith(undefined)), "");
  assert.equal(readSessionName({}), "");
  assert.equal(readSessionName(null), "");
});

test("a meta with no content reads as no boundary", () => {
  assert.equal(readSessionName(docWith(null)), "");
  assert.equal(readSessionName(docWith("")), "");
});

test("staying in the same session is not a boundary crossing", () => {
  assert.equal(crossesBoundary("admin", "admin"), false);
  assert.equal(crossesBoundary("", ""), false);
});

test("moving between two sessions crosses the boundary", () => {
  assert.equal(crossesBoundary("admin", "public"), true);
});

test("leaving a session for an unbounded page crosses the boundary", () => {
  // The page the client is walking into may be an old cached one, a page served
  // before the upgrade, or something that is not a wireview page at all. All
  // three answer "no boundary", and all three are a full load from inside one.
  assert.equal(crossesBoundary("admin", ""), true);
  assert.equal(crossesBoundary("admin", undefined), true);
  assert.equal(crossesBoundary("admin", null), true);
});

test("entering a session from an unbounded page crosses the boundary", () => {
  assert.equal(crossesBoundary("", "admin"), true);
  assert.equal(crossesBoundary(undefined, "admin"), true);
});
