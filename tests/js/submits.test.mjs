import assert from "node:assert/strict";
import { test } from "node:test";

import { SubmitUploads } from "../../wireview/static/wireview/submits.mjs";

/**
 * A page's upload managers, as plain data: `uploads[id][name]` is
 * `{ config, entries, waiting }`; a missing id has no manager.
 */
function page(uploads) {
  return {
    uploads,
    config: (id, name) => uploads[id]?.[name]?.config,
    entries: (id, name) => uploads[id]?.[name]?.entries ?? [],
    waiting: (id, name) => uploads[id]?.[name]?.waiting ?? 0,
    alive: (id) => id in uploads,
  };
}

const MANUAL = { auto_upload: false };
const AUTO = { auto_upload: true };

function manual(...entries) {
  return { config: MANUAL, entries };
}

test("a form without manual entries to send submits at once", () => {
  const submits = new SubmitUploads();
  const view = page({
    c: {
      auto: { config: AUTO, entries: [{ ref: "1", status: "uploading" }] },
      done: manual({ ref: "2", status: "completed", confirmed: true }, { ref: "3", status: "error" }),
    },
  });
  assert.equal(submits.begin("form", [{ id: "c", name: "auto" }, { id: "c", name: "done" }], view, "data"), false);
  assert.equal(submits.has("form"), false);
});

test("a submit starts its registered entries and is sent once the server confirms them all", () => {
  const submits = new SubmitUploads();
  const photos = manual({ ref: "a", status: "registered" }, { ref: "b", status: "pending" });
  const view = page({ c: { photos } });
  assert.equal(submits.begin("form", [{ id: "c", name: "photos" }], view, "data"), true);
  assert.equal(submits.has("form"), true);

  let step = submits.advance(view);
  assert.deepEqual(step.starts, [{ id: "c", name: "photos", ref: "a" }]);
  assert.deepEqual(step.decided, []);

  photos.entries[0].status = "uploading";
  photos.entries[1].status = "registered";
  step = submits.advance(view);
  assert.deepEqual(step.starts, [{ id: "c", name: "photos", ref: "b" }]);

  // Done on this side is not enough: the server may still refuse the file
  photos.entries[0].status = "completed";
  photos.entries[1].status = "completed";
  assert.deepEqual(submits.advance(view), { starts: [], decided: [] });

  photos.entries[0].confirmed = true;
  photos.entries[1].confirmed = true;
  assert.deepEqual(submits.advance(view).decided, [{ form: "form", data: "data", outcome: "send" }]);
  assert.equal(submits.has("form"), false);
});

test("an entry that fails or is cancelled keeps the submit back, and the next submit waits only for what is left", () => {
  for (const ending of ["error", "cancelled"]) {
    const submits = new SubmitUploads();
    const photos = manual({ ref: "a", status: "uploading" }, { ref: "b", status: "uploading" });
    const view = page({ c: { photos } });
    submits.begin("form", [{ id: "c", name: "photos" }], view, "first");

    photos.entries[0].status = ending;
    assert.deepEqual(submits.advance(view).decided, [{ form: "form", data: "first", outcome: "fail" }]);

    // Submitted again: the failed entry is not waited for, the other still is
    assert.equal(submits.begin("form", [{ id: "c", name: "photos" }], view, "second"), true);
    photos.entries[1].status = "completed";
    photos.entries[1].confirmed = true;
    assert.deepEqual(submits.advance(view).decided, [{ form: "form", data: "second", outcome: "send" }], ending);
  }
});

test("a submit whose entries left with their manager is abandoned", () => {
  const submits = new SubmitUploads();
  const uploads = { c: { photos: manual({ ref: "a", status: "registered" }) } };
  const view = page(uploads);
  submits.begin("form", [{ id: "c", name: "photos" }], view, "data");

  // A new instance under the id: a new manager without the entry
  uploads.c = { photos: manual() };
  assert.deepEqual(submits.advance(view).decided, [{ form: "form", data: "data", outcome: "abandon" }]);

  submits.begin("form", [{ id: "c", name: "photos" }], page({ c: { photos: manual({ ref: "b", status: "pending" }) } }), "x");
  assert.deepEqual(submits.advance(page({})).decided, [{ form: "form", data: "x", outcome: "abandon" }]);
});

test("files waiting for their upload's config hold the submit, and join it when the config is a manual one", () => {
  const submits = new SubmitUploads();
  const uploads = { c: { photos: { config: undefined, entries: [], waiting: 1 } } };
  const view = page(uploads);
  assert.equal(submits.begin("form", [{ id: "c", name: "photos" }], view, "data"), true);
  assert.deepEqual(submits.advance(view), { starts: [], decided: [] });

  uploads.c.photos = manual({ ref: "a", status: "pending" });
  assert.deepEqual(submits.advance(view), { starts: [], decided: [] });
  uploads.c.photos.entries[0].status = "registered";
  assert.deepEqual(submits.advance(view).starts, [{ id: "c", name: "photos", ref: "a" }]);
  uploads.c.photos.entries[0].confirmed = true;
  assert.equal(submits.advance(view).decided[0].outcome, "send");
});

test("files waiting for a config that turns out automatic do not hold the submit", () => {
  const submits = new SubmitUploads();
  const uploads = { c: { photos: { config: undefined, entries: [], waiting: 2 } } };
  const view = page(uploads);
  submits.begin("form", [{ id: "c", name: "photos" }], view, "data");

  uploads.c.photos = { config: AUTO, entries: [{ ref: "a", status: "pending" }] };
  assert.deepEqual(submits.advance(view).decided, [{ form: "form", data: "data", outcome: "send" }]);
});

test("two forms waiting for one entry start it once, and the connection closing gives both up", () => {
  const submits = new SubmitUploads();
  const view = page({ c: { photos: manual({ ref: "a", status: "registered" }) } });
  submits.begin("one", [{ id: "c", name: "photos" }], view, 1);
  submits.begin("two", [{ id: "c", name: "photos" }], view, 2);
  assert.deepEqual(submits.advance(view).starts, [{ id: "c", name: "photos", ref: "a" }]);

  assert.deepEqual(submits.clear(), [
    { form: "one", data: 1 },
    { form: "two", data: 2 },
  ]);
  assert.equal(submits.has("one"), false);
});
