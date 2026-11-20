import assert from "node:assert/strict";
import { test } from "node:test";

import { DEFAULT_TIME, phasesOf, runPhases } from "../../wireview/static/wireview/transitions.mjs";

/** An element's classes, a log of what changed, and a clock the test moves. */
function stage() {
  /** @type {Set<string>} */
  const classes = new Set();
  /** @type {string[]} */
  const log = [];
  /** @type {Array<() => void>} */
  let frames = [];
  /** @type {Array<{at: number, resolve: () => void}>} */
  let timers = [];
  let now = 0;
  const snapshot = (label) => log.push(`${label}: ${[...classes].sort().join(" ")}`);
  const ops = {
    hold: (names) => {
      for (const name of names) classes.add(name);
      let released = false;
      return () => {
        if (released) return;
        released = true;
        for (const name of names) classes.delete(name);
      };
    },
    keep: (names) => names.forEach((name) => classes.add(name)),
    frame: () => new Promise((resolve) => frames.push(() => resolve(undefined))),
    sleep: (ms) => new Promise((resolve) => timers.push({ at: now + ms, resolve: () => resolve(undefined) })),
  };
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  return {
    classes,
    log,
    ops,
    snapshot,
    async paint() {
      const due = frames;
      frames = [];
      due.forEach((run) => run());
      await settle();
    },
    async advance(ms) {
      now += ms;
      const due = timers.filter((timer) => timer.at <= now);
      timers = timers.filter((timer) => timer.at > now);
      due.forEach((timer) => timer.resolve());
      await settle();
    },
    settle,
  };
}

test("three phases: start is painted, then the transition joins, then end takes start's place, then all come off", async () => {
  const s = stage();
  const done = runPhases(phasesOf({ transition: "ease", start: "o-0", end: "o-100", time: 300 }), s.ops);
  s.snapshot("at once");
  await s.paint();
  s.snapshot("frame 1");
  await s.paint();
  s.snapshot("frame 2");
  await s.advance(299);
  s.snapshot("299ms");
  await s.advance(1);
  await done;
  s.snapshot("300ms");
  assert.deepEqual(s.log, ["at once: o-0", "frame 1: ease o-0", "frame 2: ease o-100", "299ms: ease o-100", "300ms: "]);
});

test("a three-phase transition without a time takes the default", () => {
  assert.equal(phasesOf({ transition: "ease", start: "a", end: "b" })?.time, DEFAULT_TIME);
  assert.equal(phasesOf({ transition: "ease" })?.time, 0, "one class string keeps its old meaning");
  assert.equal(phasesOf({ transition: "" }), null);
  assert.equal(phasesOf(null), null);
});

test("a three-phase transition of 0 ms puts nothing on", async () => {
  const s = stage();
  await runPhases(phasesOf({ transition: "ease", start: "a", end: "b", time: 0 }), s.ops);
  assert.deepEqual([...s.classes], []);
});

test("one class string keeps its classes for the time, or for good without one", async () => {
  const timed = stage();
  const done = runPhases(phasesOf({ transition: "pulse", time: 100 }), timed.ops);
  assert.deepEqual([...timed.classes], ["pulse"]);
  await timed.advance(100);
  await done;
  assert.deepEqual([...timed.classes], []);

  const kept = stage();
  await runPhases(phasesOf({ transition: "pulse" }), kept.ops);
  assert.deepEqual([...kept.classes], ["pulse"]);
});

test("what fails in the middle leaves no class it held", async () => {
  const s = stage();
  let frames = 0;
  const ops = {
    ...s.ops,
    frame: () => (++frames === 2 ? Promise.reject(new Error("gone")) : Promise.resolve()),
  };
  await assert.rejects(runPhases(phasesOf({ transition: "ease", start: "a", end: "b", time: 50 }), ops), /gone/);
  assert.deepEqual([...s.classes], []);
});
