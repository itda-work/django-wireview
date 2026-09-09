import { strict as assert } from "node:assert";
import test from "node:test";

import {
  RELOAD_COOLDOWN_MS,
  RELOAD_STORAGE_KEY,
  shouldReload,
} from "../../wireview/static/wireview/reload.mjs";

test("no record means the page has not reloaded yet", () => {
  assert.equal(shouldReload(null, 1_000_000), true);
  assert.equal(shouldReload(undefined, 1_000_000), true);
  assert.equal(shouldReload("", 1_000_000), true);
});

test("an unreadable record does not block the reload", () => {
  assert.equal(shouldReload("not a number", 1_000_000), true);
  assert.equal(shouldReload("0", 1_000_000), true);
});

test("a reload inside the cooldown is refused", () => {
  const last = 1_000_000;
  assert.equal(shouldReload(String(last), last + 1), false);
  assert.equal(shouldReload(String(last), last + RELOAD_COOLDOWN_MS - 1), false);
});

test("the cooldown ends exactly at its length", () => {
  const last = 1_000_000;
  assert.equal(shouldReload(String(last), last + RELOAD_COOLDOWN_MS), true);
  assert.equal(shouldReload(String(last), last + RELOAD_COOLDOWN_MS + 1), true);
});

test("a clock that moved backwards does not lock the page out", () => {
  assert.equal(shouldReload("2000000", 1_000_000), true);
});

test("the cooldown is configurable", () => {
  assert.equal(shouldReload("1000000", 1_005_000, 10_000), false);
  assert.equal(shouldReload("1000000", 1_015_000, 10_000), true);
});

test("the storage key is namespaced", () => {
  assert.equal(RELOAD_STORAGE_KEY, "wireview:last-reload");
});
