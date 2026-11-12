import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

import ReconnectingWebSocket from "reconnecting-websocket";

import {
  Backoff,
  DEFAULTS,
  HEARD_RECENTLY_MS,
  META_NAME,
  PROBE_TIMEOUT_MS,
  Revival,
  SYNC_SINCE,
  backoffDelay,
  isReturn,
  onReturn,
  readReconnectSettings,
  reconnectOptions,
  renewsNow,
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

// --- the user coming back cuts the wait short (#228) ---------------------------------

const OPTIONS = { minReconnectionDelay: 2000, maxReconnectionDelay: 10000, reconnectionDelayGrowFactor: 2 };

test("the backoff is the library's: none after an open, then the first delay, growing, capped", () => {
  assert.equal(backoffDelay(OPTIONS, 0), 0);
  assert.equal(backoffDelay(OPTIONS, 1), 2000);
  assert.equal(backoffDelay(OPTIONS, 2), 4000);
  assert.equal(backoffDelay(OPTIONS, 3), 8000);
  assert.equal(backoffDelay(OPTIONS, 4), 10000);
  assert.equal(backoffDelay(OPTIONS, 40), 10000);
});

test("the backoff matches reconnecting-websocket's _getNextDelay, line for line", () => {
  // The page took the wait over from the library; it must not change its pace.
  const url = new URL(
    "../../node_modules/reconnecting-websocket/dist/reconnecting-websocket-mjs.js",
    import.meta.url
  );
  const source = readFileSync(url, "utf8");
  assert.match(source, /if \(this\._retryCount > 0\) \{\s*delay =\s*minReconnectionDelay \* Math\.pow\(reconnectionDelayGrowFactor, this\._retryCount - 1\);\s*if \(delay > maxReconnectionDelay\) \{\s*delay = maxReconnectionDelay;/);
  // And it awaits a URL given as a function returning a promise: the hook the page waits in
  assert.match(source, /if \(typeof urlProvider === 'function'\) \{[\s\S]*?if \(!!url\.then\) \{\s*return url;/);
});

function fakeTimers() {
  const pending = new Map();
  let next = 0;
  return {
    pending,
    set(callback, ms) {
      const handle = ++next;
      pending.set(handle, { callback, ms });
      return handle;
    },
    clear(handle) {
      pending.delete(handle);
    },
    fire() {
      for (const [handle, { callback }] of [...pending]) {
        pending.delete(handle);
        callback();
      }
    },
  };
}

test("an attempt waits its delay, and resolves to the URL when it is over", async () => {
  const timers = fakeTimers();
  const backoff = new Backoff(OPTIONS, timers);
  const attempt = backoff.wait("ws://x/", 2);
  assert.equal(backoff.waiting, true);
  assert.equal([...timers.pending.values()][0].ms, 4000);
  timers.fire();
  assert.equal(await attempt, "ws://x/");
  assert.equal(backoff.waiting, false);
});

test("the first attempt after an open does not wait", async () => {
  const timers = fakeTimers();
  const backoff = new Backoff(OPTIONS, timers);
  assert.equal(await backoff.wait("ws://x/", 0), "ws://x/");
  assert.equal(backoff.waiting, false);
  assert.equal(timers.pending.size, 0);
});

test("skipping ends the wait at once, once", async () => {
  const timers = fakeTimers();
  const backoff = new Backoff(OPTIONS, timers);
  const attempt = backoff.wait("ws://x/", 3);
  assert.equal(backoff.skip(), true);
  assert.equal(await attempt, "ws://x/");
  // The timer is gone: the attempt does not resolve a second time later
  assert.equal(timers.pending.size, 0);
  // A second signal finds nothing waiting
  assert.equal(backoff.skip(), false);
});

const OPEN_QUIET = { visible: true, waiting: false, readyState: 1, probing: false, serverVsn: SYNC_SINCE, quietFor: 60000, awaiting: false };

test("a shown tab whose socket waits out its backoff connects now", () => {
  assert.equal(onReturn({ ...OPEN_QUIET, waiting: true, readyState: 3 }), "connect");
});

test("a hidden tab does nothing, whatever its socket is doing", () => {
  assert.equal(onReturn({ ...OPEN_QUIET, visible: false, waiting: true, readyState: 3 }), "none");
  assert.equal(onReturn({ ...OPEN_QUIET, visible: false }), "none");
});

test("a socket already opening, or closing on the way to opening, is left to it", () => {
  assert.equal(onReturn({ ...OPEN_QUIET, readyState: 0 }), "none");
  assert.equal(onReturn({ ...OPEN_QUIET, readyState: 2 }), "none");
  // Between the library's close and its next attempt: not waiting, not open
  assert.equal(onReturn({ ...OPEN_QUIET, readyState: 3 }), "none");
});

test("a quiet socket that looks open is asked", () => {
  assert.equal(onReturn(OPEN_QUIET), "probe");
});

test("a socket already being asked, or heard from just now, is not asked again", () => {
  assert.equal(onReturn({ ...OPEN_QUIET, probing: true }), "none");
  assert.equal(onReturn({ ...OPEN_QUIET, quietFor: HEARD_RECENTLY_MS - 1 }), "none");
  assert.equal(onReturn({ ...OPEN_QUIET, quietFor: HEARD_RECENTLY_MS }), "probe");
});

test("a server that would not answer sync is not asked", () => {
  // 0: no join answered yet on this socket; below 8: sync is dropped, not answered
  assert.equal(onReturn({ ...OPEN_QUIET, serverVsn: 0 }), "none");
  assert.equal(onReturn({ ...OPEN_QUIET, serverVsn: SYNC_SINCE - 1 }), "none");
});

test("visible and online together: the second finds the first's work under way", () => {
  // Waiting: the first skips the wait, the second sees the attempt opening
  const timers = fakeTimers();
  const backoff = new Backoff(OPTIONS, timers);
  backoff.wait("ws://x/", 1);
  const state = () => ({ ...OPEN_QUIET, waiting: backoff.waiting, readyState: backoff.waiting ? 3 : 0 });
  assert.equal(onReturn(state()), "connect");
  backoff.skip();
  assert.equal(onReturn(state()), "none");
  // Open: the first asks, the second sees the question out
  const revival = new Revival();
  revival.heard(0);
  const asked = () => ({ ...OPEN_QUIET, probing: revival.probing, quietFor: revival.quietFor(60000) });
  assert.equal(onReturn(asked()), "probe");
  revival.probed(7, 1);
  assert.equal(onReturn(asked()), "none");
});

test("a probe nothing answered opens another socket", () => {
  const revival = new Revival();
  revival.probed(7, 1);
  assert.equal(revival.expired(7, 1), true);
  assert.equal(revival.probing, false);
});

test("anything heard answers the probe, not only its synced", () => {
  const revival = new Revival();
  revival.probed(7, 1);
  revival.heard(100);
  assert.equal(revival.expired(7, 1), false);
  assert.equal(revival.quietFor(150), 50);
});

test("a probe whose socket closed or was replaced does not close the next one", () => {
  const closed = new Revival();
  closed.probed(7, 1);
  closed.closed();
  assert.equal(closed.expired(7, 2), false);
  // A socket opened since (its open counts as heard)
  const reopened = new Revival();
  reopened.probed(7, 1);
  reopened.heard(5);
  assert.equal(reopened.expired(7, 2), false);
  // Another epoch, even with the probe still recorded
  const moved = new Revival();
  moved.probed(7, 1);
  assert.equal(moved.expired(7, 2), false);
});

test("an older probe's timer does not end a newer probe", () => {
  const revival = new Revival();
  revival.probed(7, 1);
  revival.heard(10);
  revival.probed(9, 1);
  assert.equal(revival.expired(7, 1), false);
  assert.equal(revival.probing, true);
  assert.equal(revival.expired(9, 1), true);
});

test("the probe gives a socket the time the library gives one to open", () => {
  const url = new URL(
    "../../node_modules/reconnecting-websocket/dist/reconnecting-websocket-mjs.js",
    import.meta.url
  );
  assert.match(readFileSync(url, "utf8"), new RegExp(`connectionTimeout: ${PROBE_TIMEOUT_MS},`));
});

// --- only a conversation at rest is asked (#228, second round) -----------------------

test("a socket the page sent something on since it last heard is not asked", () => {
  // A handler waiting on an outside API for seconds: the sync would wait behind it
  assert.equal(onReturn({ ...OPEN_QUIET, awaiting: true }), "none");
  // Waiting out the backoff is another matter: nothing is in a server's hands
  assert.equal(onReturn({ ...OPEN_QUIET, awaiting: true, waiting: true, readyState: 3 }), "connect");
});

test("the page awaits an answer from a send until it next hears, and not after a close", () => {
  const revival = new Revival();
  assert.equal(revival.awaiting, false);
  revival.sent();
  assert.equal(revival.awaiting, true);
  revival.heard(10);
  assert.equal(revival.awaiting, false);
  revival.sent();
  revival.closed();
  assert.equal(revival.awaiting, false);
});

test("the slow handler: sent, quiet for seconds, then the tab comes back", () => {
  const revival = new Revival();
  revival.heard(0);
  revival.sent();
  const state = { ...OPEN_QUIET, quietFor: revival.quietFor(5000), awaiting: revival.awaiting };
  assert.equal(onReturn(state), "none");
  // Its render lands: the conversation is at rest again, and a later return may ask
  revival.heard(8000);
  assert.equal(onReturn({ ...OPEN_QUIET, quietFor: revival.quietFor(20000), awaiting: revival.awaiting }), "probe");
});

test("a page restored from the back/forward cache is a return; a fresh load is not", () => {
  assert.equal(isReturn({ type: "pageshow", persisted: true }), true);
  assert.equal(isReturn({ type: "pageshow", persisted: false }), false);
  assert.equal(isReturn({ type: "pageshow" }), false);
  assert.equal(isReturn({ type: "visibilitychange" }), true);
  assert.equal(isReturn({ type: "online" }), true);
  assert.equal(isReturn({ type: "focus" }), false);
});

test("a renew reconnects a socket open or opening, and leaves a waiting one to its backoff", () => {
  assert.equal(renewsNow({ waiting: false, readyState: 1 }), true);
  assert.equal(renewsNow({ waiting: false, readyState: 0 }), true);
  // Dropped since: the backoff's socket is the renewal (Renewal.opened)
  assert.equal(renewsNow({ waiting: true, readyState: 3 }), false);
  // Closed, the library about to start its attempt
  assert.equal(renewsNow({ waiting: false, readyState: 3 }), false);
  assert.equal(renewsNow({ waiting: false, readyState: 2 }), false);
});

// --- a return skips one wait, not the growth of the next (#228 with #230) ------------

/** A socket the server accepts and closes at once -- 1008, a rate limit (#230). */
class RefusedSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor() {
    this.readyState = 0;
    this.listeners = { open: [], close: [], message: [], error: [] };
    setImmediate(() => {
      this.readyState = 1;
      this.listeners.open.forEach((listener) => listener({ type: "open" }));
      this.readyState = 3;
      this.listeners.close.forEach((listener) => listener({ type: "close", code: 1008 }));
    });
  }
  addEventListener(type, listener) {
    this.listeners[type].push(listener);
  }
  removeEventListener(type, listener) {
    this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
  }
  close() {
    this.readyState = 3;
  }
}

async function until(condition) {
  for (let i = 0; i < 1000 && !condition(); i++) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(condition(), "never happened");
}

test("skipping a wait leaves the retry count, so a socket closed again at once waits longer", async () => {
  // The rate limit's brake on a page that keeps sending what closes it is the
  // growing backoff. The user coming back starts one attempt now; it does not
  // reset the count the next delay grows from (reconnect() would).
  const timers = fakeTimers();
  const backoff = new Backoff(OPTIONS, timers);
  const delays = () => [...timers.pending.values()].map(({ ms }) => ms);
  /** @type {any} */
  let socket = null;
  socket = new ReconnectingWebSocket(() => backoff.wait("ws://x/", socket?.retryCount ?? 0), [], {
    WebSocket: RefusedSocket,
    minReconnectionDelay: 0,
    maxReconnectionDelay: 0,
    reconnectionDelayGrowFactor: 1,
    maxEnqueuedMessages: 0,
  });
  try {
    await until(() => backoff.waiting);
    assert.deepEqual(delays(), [2000]);

    backoff.skip();
    await until(() => backoff.waiting);
    assert.deepEqual(delays(), [4000]);

    backoff.skip();
    await until(() => backoff.waiting);
    assert.deepEqual(delays(), [8000]);
  } finally {
    socket.close();
    timers.pending.clear();
  }
});
