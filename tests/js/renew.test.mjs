import { test } from "node:test";
import assert from "node:assert/strict";

import { Renewal } from "../../wireview/static/wireview/renew.mjs";

test("the first renew on a socket reconnects, the rest are about a socket already going (#209)", () => {
  const renewal = new Renewal();
  assert.equal(renewal.pending, false);
  assert.equal(renewal.request(1), true);
  assert.equal(renewal.pending, true, "the socket it opens says it is the renewal");
  assert.equal(renewal.request(1), false);
  assert.equal(renewal.request(1), false);
});

test("the renewal is the next socket only: a later reconnect is an ordinary one", () => {
  const renewal = new Renewal();
  renewal.request(1);
  assert.equal(renewal.opened(), true);
  assert.equal(renewal.pending, false);
  // The connection drops and comes back
  assert.equal(renewal.opened(), false);
});

test("a socket that failed to open keeps asking as the renewal until one opens", () => {
  const renewal = new Renewal();
  renewal.request(1);
  // reconnecting-websocket reads the URL again for each attempt
  assert.equal(renewal.pending, true);
  assert.equal(renewal.pending, true);
  assert.equal(renewal.opened(), true);
});

test("a later socket that is told to renew does so once more", () => {
  const renewal = new Renewal();
  renewal.request(1);
  renewal.opened();
  // Socket 2 -- the renewal -- is never told: the server does not renew a
  // renewed socket. Socket 3, after a dropped connection, may be.
  assert.equal(renewal.request(3), true);
  assert.equal(renewal.request(3), false);
});
