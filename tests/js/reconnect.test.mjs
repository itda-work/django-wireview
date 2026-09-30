import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULTS,
  META_NAME,
  readReconnectSettings,
  reconnectOptions,
} from "../../wireview/static/wireview/reconnect.mjs";

function documentWith(attributes) {
  return {
    querySelector(selector) {
      if (selector !== `meta[name="${META_NAME}"]` || attributes === null) return null;
      return { getAttribute: (name) => (name in attributes ? attributes[name] : null) };
    },
  };
}

test("the defaults are reconnecting-websocket's own", () => {
  // What every page used before the settings existed: an upgrade changes nothing.
  const url = new URL(
    "../../node_modules/reconnecting-websocket/dist/reconnecting-websocket-mjs.js",
    import.meta.url
  );
  const source = readFileSync(url, "utf8");
  assert.match(source, new RegExp(`maxReconnectionDelay: ${DEFAULTS.maxDelay},`));
  assert.match(
    source,
    new RegExp(`minReconnectionDelay: ${DEFAULTS.minDelay} \\+ Math\\.random\\(\\) \\* ${DEFAULTS.jitter},`)
  );
  assert.match(source, new RegExp(`reconnectionDelayGrowFactor: ${DEFAULTS.growFactor.toString().replace(".", "\\.")},`));
});

test("a page without the meta reconnects with the defaults", () => {
  assert.deepEqual(readReconnectSettings(documentWith(null)), DEFAULTS);
  assert.deepEqual(readReconnectSettings(null), DEFAULTS);
});

test("the meta sets each value", () => {
  const settings = readReconnectSettings(
    documentWith({
      "data-min-delay": "2000",
      "data-jitter": "8000",
      "data-max-delay": "60000",
      "data-grow-factor": "2",
    })
  );
  assert.deepEqual(settings, { minDelay: 2000, jitter: 8000, maxDelay: 60000, growFactor: 2 });
});

test("a value that is missing or unusable keeps its default, and only that one", () => {
  const settings = readReconnectSettings(
    documentWith({
      "data-min-delay": "abc",
      "data-jitter": "",
      "data-max-delay": "-5",
      "data-grow-factor": "0.5",
    })
  );
  assert.deepEqual(settings, DEFAULTS);
  assert.equal(readReconnectSettings(documentWith({ "data-max-delay": "30000" })).maxDelay, 30000);
  assert.equal(readReconnectSettings(documentWith({ "data-max-delay": "30000" })).minDelay, 1000);
});

test("zero is a delay, not a missing one", () => {
  const settings = readReconnectSettings(documentWith({ "data-min-delay": "0", "data-jitter": "0" }));
  assert.equal(settings.minDelay, 0);
  assert.equal(settings.jitter, 0);
});

test("the jitter is drawn once and added to the first delay", () => {
  const settings = { minDelay: 1000, jitter: 4000, maxDelay: 10000, growFactor: 1.3 };
  assert.equal(reconnectOptions(settings, () => 0).minReconnectionDelay, 1000);
  assert.equal(reconnectOptions(settings, () => 0.5).minReconnectionDelay, 3000);
  assert.deepEqual(reconnectOptions(settings, () => 0.25), {
    minReconnectionDelay: 2000,
    maxReconnectionDelay: 10000,
    reconnectionDelayGrowFactor: 1.3,
  });
});
