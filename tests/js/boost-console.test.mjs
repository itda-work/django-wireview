import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

const boost = readFileSync(
  new URL("../../wireview/static/wireview/wireview-boost.js", import.meta.url),
  "utf8"
);

test("the boost module does not write to every visitor's console", () => {
  // It is part of the bundle, so it runs on every page of every project whether or
  // not BOOST_PAGES is on. Two debug lines inherited from reactor printed
  // "BOOST_PAGES false" on each load and "LOAD <url>" on each boosted move.
  // Diagnostics belong behind wireview.debug.enable(), like the rest of the client.
  assert.deepEqual(boost.match(/console\.log\(/g) ?? [], []);
});
