import { test } from "node:test";
import assert from "node:assert/strict";

import { TAB_PATTERN, newTab, socketUrl } from "../../wireview/static/wireview/tab.mjs";

test("a tab is 22 URL-safe characters the server accepts", () => {
  for (let i = 0; i < 200; i++) {
    const tab = newTab();
    assert.equal(tab.length, 22);
    assert.match(tab, TAB_PATTERN);
  }
});

test("bytes that base64 writes as + and / come out URL-safe", () => {
  const tab = newTab((bytes) => bytes.fill(0xff));
  assert.equal(tab, "_____________________w");
  assert.match(tab, TAB_PATTERN);
  assert.equal(newTab((bytes) => bytes.fill(0xfb)), "-_v7-_v7-_v7-_v7-_v7-w");
});

test("two page loads get different tabs", () => {
  const seen = new Set(Array.from({ length: 100 }, () => newTab()));
  assert.equal(seen.size, 100);
});

test("the socket URL carries the protocol version and the tab", () => {
  assert.equal(
    socketUrl({ protocol: "https:", host: "example.com:8443" }, "__wireview__", 8, "abcdefghijklmnopqrstuv"),
    "wss://example.com:8443/__wireview__?vsn=8&tab=abcdefghijklmnopqrstuv"
  );
  assert.equal(
    socketUrl({ protocol: "http:", host: "localhost" }, "__wireview__", 8, "a_b-c"),
    "ws://localhost/__wireview__?vsn=8&tab=a_b-c"
  );
});

test("the socket a renew asked for says so, and only that one (#209)", () => {
  const at = { protocol: "http:", host: "localhost" };
  assert.equal(socketUrl(at, "__wireview__", 11, "abc", true), "ws://localhost/__wireview__?vsn=11&tab=abc&renewed=1");
  assert.equal(socketUrl(at, "__wireview__", 11, "abc", false), "ws://localhost/__wireview__?vsn=11&tab=abc");
});
