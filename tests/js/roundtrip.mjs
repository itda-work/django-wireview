/**
 * Driver for tests/test_diff_roundtrip.py: applies a sequence of server diffs
 * with the real client code and prints the HTML after every step.
 *
 * stdin:  JSON array of sequences; a sequence is an array of diffs, one per
 *         render (null when nothing changed), applied from an empty state
 * stdout: the same shape with the HTML after each diff in place of the diff
 *
 * The state handling mirrors `Component.applyPhoenixDiff` in wireview.js: a
 * diff with `s` replaces the render, anything else is a partial.
 */

import { applyPartial, buildHtml } from "../../wireview/static/wireview/rendered.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;

/**
 * @param {Array<Object|null>} diffs
 * @returns {Array<string|null>}
 */
function replay(diffs) {
  let statics = null;
  let dynamics = [];
  return diffs.map((diff) => {
    if (diff !== null) {
      if ("s" in diff) {
        statics = diff.s;
        dynamics = diff.d.slice();
      } else {
        applyPartial(dynamics, diff);
      }
    }
    return statics === null ? null : buildHtml(statics, dynamics);
  });
}

process.stdout.write(JSON.stringify(JSON.parse(input).map(replay)));
