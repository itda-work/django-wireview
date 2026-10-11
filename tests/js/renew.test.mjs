import { test } from "node:test";
import assert from "node:assert/strict";

import { RENEW_STREAK_MS, Renewal } from "../../wireview/static/wireview/renew.mjs";

const BACKOFF = { minDelay: 1000, maxDelay: 10000, growFactor: 2 };

test("the first renew on a socket reconnects at once, the rest are about a socket already going (#209)", () => {
  const renewal = new Renewal(BACKOFF);
  assert.equal(renewal.request(1, 0), 0);
  assert.equal(renewal.request(1, 10), null);
  assert.equal(renewal.request(1, 20), null);
});

test("the renewal's first round says so; a later reconnect is an ordinary socket", () => {
  const renewal = new Renewal(BACKOFF);
  assert.equal(renewal.opened(), false, "the page's first socket");
  renewal.request(1, 0);
  assert.equal(renewal.opened(), true, "the socket the renew opened");
  // The connection drops and comes back
  assert.equal(renewal.opened(), false);
});

test("a socket that failed to open leaves the renewal for the one that opens", () => {
  const renewal = new Renewal(BACKOFF);
  renewal.request(1, 0);
  // reconnecting-websocket retries; the open handler runs once, for the one that opened
  assert.equal(renewal.opened(), true);
  assert.equal(renewal.opened(), false);
});

test("a later socket may be told to renew again, once (a second change in another tab)", () => {
  const renewal = new Renewal(BACKOFF);
  renewal.request(1, 0);
  renewal.opened();
  // Socket 2 -- the renewal -- answered its first round. A boosted move on it
  // later met another tab's logout and was told to renew.
  assert.equal(renewal.request(2, RENEW_STREAK_MS + 1), 0, "a minute later: a new streak, at once");
  assert.equal(renewal.request(2, RENEW_STREAK_MS + 2), null);
});

test("renews in a row wait longer each time, up to the reconnect ceiling", () => {
  const renewal = new Renewal(BACKOFF);
  const waits = [];
  for (let epoch = 1, now = 0; epoch <= 7; epoch += 1, now += 100) {
    waits.push(renewal.request(epoch, now));
    renewal.opened();
  }
  assert.deepEqual(waits, [0, 1000, 2000, 4000, 8000, 10000, 10000]);
});

test("a clock that moved backwards starts a new streak", () => {
  const renewal = new Renewal(BACKOFF);
  renewal.request(1, 5000);
  assert.equal(renewal.request(2, 1000), 0);
});
