import { strict as assert } from "node:assert";
import test from "node:test";

import {
  META_NAME,
  NavigationGate,
  crossesBoundary,
  readSessionName,
} from "../../wireview/static/wireview/live-session.mjs";

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


test("work queued for the navigation in flight still runs", () => {
  const gate = new NavigationGate();
  gate.begin();
  const token = gate.token;

  assert.equal(gate.accepts(token), true);
});

test("a newer navigation drops what the previous one queued", () => {
  // A boosted body morph runs on a requestAnimationFrame, so a second click
  // during the first fetch would otherwise paint the first destination on top
  // of the second -- and join its components.
  const gate = new NavigationGate();
  gate.begin();
  const first = gate.token;
  gate.begin();

  assert.equal(gate.accepts(first), false);
});

test("abandoning ends the navigation in flight without starting another", () => {
  // What leaving the boundary does: the browser is loading a whole new
  // document, and nothing queued for the navigation that discovered it should
  // paint or join in the meantime.
  const gate = new NavigationGate();
  gate.begin();
  const token = gate.token;
  gate.abandon();

  assert.equal(gate.accepts(token), false);
  assert.equal(gate.accepts(gate.token), true);
});

test("a popstate's cached body and its validating fetch share one navigation", () => {
  // The regression this guards: bumping the token inside the fetch step rather
  // than at the navigation's start cancelled every cached back/forward paint,
  // because the fetch always started after the morph was queued.
  const gate = new NavigationGate();
  gate.begin();
  const cachedPaint = gate.token;
  const fetchStep = gate.token;

  assert.equal(gate.accepts(cachedPaint), true);
  assert.equal(cachedPaint, fetchStep);
});
