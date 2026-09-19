/**
 * Driver for tests/test_diff_roundtrip.py: applies a sequence of server diffs
 * with the real client code and prints the HTML after every step.
 *
 * stdin:  JSON array of sequences; a sequence is an array of steps
 *         `{diff, children}`, one per render, applied from an empty state.
 *         `diff` is null when the parent did not change; `children` maps each
 *         LiveComponent id to its current HTML
 * stdout: the same shape with the HTML after each step in place of the step
 *
 * The state handling mirrors wireview.js: children are registered before the
 * parent is touched, a diff with `s` replaces the render (as
 * `Component.applyPhoenixDiff` does), anything else is a partial, and the
 * parent's HTML resolves component references to the children's current HTML
 * (as `currentHtml` does through `resolveComponentHtml`).
 */

import { applyPartial, buildHtml } from "../../wireview/static/wireview/rendered.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;

/**
 * @param {Array<{diff: Object|null, children: Object<string, string>}>} steps
 * @returns {Array<string|null>}
 */
function replay(steps) {
  let statics = null;
  let dynamics = [];
  let children = {};
  const resolve = (id) => children[id] ?? "";
  return steps.map(({ diff, children: current }) => {
    children = current;
    if (diff !== null) {
      if ("s" in diff) {
        statics = diff.s;
        dynamics = diff.d.slice();
      } else {
        applyPartial(dynamics, diff);
      }
    }
    return statics === null ? null : buildHtml(statics, dynamics, resolve);
  });
}

process.stdout.write(JSON.stringify(JSON.parse(input).map(replay)));
