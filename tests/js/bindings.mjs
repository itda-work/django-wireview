/**
 * Driver for tests/test_dom_events.py: runs the client's binding functions
 * (events.mjs) on the cases the test helpers (wireview/testing_dom.py) get,
 * and prints what fired.
 *
 * stdin:  JSON array of cases `{chain, type, event, bubbles, others}`. `chain`
 *         is the elements an event passes from its target up, each the list of
 *         its attribute names; `others` are elements beside the chain's top,
 *         outside it; `event` holds the fields runSteps reads (key, ctrlKey,
 *         ...); `bubbles` is false for an event like focus, whose target's
 *         bindings alone run (the root's capture listener). Element indexes
 *         count the chain, then `others`.
 * stdout: JSON array of `{fired, prevented}`: the bindings that fired as
 *         `[element index, attribute]` in the order they fire, and whether the
 *         event's default was prevented while it was dispatched.
 *
 * The loop mirrors `EventBindings.bubble` and `run` in wireview.js: an
 * element's bindings for the type in attribute order, propagation stopped
 * after the element whose binding called `stop`. A debounce hands the rest of
 * the run to a timer, which runs after the dispatch -- timers in order of
 * their delay, then of when they were set -- when `prevent` and `stop` are too
 * late to matter. A throttle lets the event through, as the first one in its
 * window is.
 */

import { belongsToIme, bindingsFor, parseBinding, runSteps } from "../../wireview/static/wireview/events.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;

/**
 * @param {{chain: string[][], type: string, event: Object, bubbles?: boolean, others?: string[][]}} kase
 */
function dispatch({ chain, type, event, bubbles = true, others = [] }) {
  /** @type {Array<[number, string]>} */
  const fired = [];
  let prevented = false;
  let late = false;
  /** @type {Array<{delay: number, seq: number, rest: () => void}>} */
  const timers = [];
  let stopped = false;
  /**
   * EventBindings.run: one element's bindings of a scope
   * @param {number} index
   * @param {string[]} names
   * @param {"element" | "window" | "away"} scope
   */
  const run = (index, names, scope) => {
    for (const attribute of bindingsFor(names, type, scope)) {
      const binding = parseBinding(attribute);
      if (!binding) continue;
      runSteps(binding.steps, event, {
        prevent: () => {
          if (!late) prevented = true;
        },
        stop: () => {
          if (!late && scope === "element") stopped = true;
        },
        debounce: (delay, rest) => {
          timers.push({ delay: Number.isNaN(delay) ? 0 : delay, seq: timers.length, rest });
        },
        throttle: () => true,
        fire: () => fired.push([index, attribute]),
      });
    }
  };
  const ime = belongsToIme(event);
  // The page in document order: the chain from its top down, then the others
  const page = [...chain.map((names, index) => ({ index, names })).reverse(), ...others.map((names, i) => ({ index: chain.length + i, names }))];
  // The window's capture phase: away bindings of the elements the target is not in
  if (!ime) for (const { index, names } of page) if (index >= chain.length) run(index, names, "away");
  for (let index = 0; index < (bubbles ? chain.length : 1); index++) {
    run(index, chain[index], "element");
    if (stopped) break;
  }
  // The window's bubble phase
  if (!ime && bubbles && !stopped) for (const { index, names } of page) run(index, names, "window");
  late = true;
  while (timers.length) {
    timers.sort((a, b) => a.delay - b.delay || a.seq - b.seq);
    /** @type {{rest: () => void}} */ (timers.shift()).rest();
  }
  return { fired, prevented };
}

process.stdout.write(JSON.stringify(JSON.parse(input).map(dispatch)));
