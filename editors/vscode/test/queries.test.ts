// The render-part SQL records (#188) under src/core/queries.ts: a line's version,
// the latest snapshot of each class, reading the segments as they grow, the
// writers that overlap, and when a line is certain enough to get a hint. The
// library's real files are read by tests/test_vscode_extension.py.
import { strict as assert } from "node:assert";
import * as nodePath from "node:path";
import test from "node:test";

import { checkQueriesVersion, FIRST_READ, hintsFor, ingest, QueryState, segmentOf, Tailer } from "../src/core/queries.ts";
import type { DocumentFacts, Files, HintOptions, QueryRecord, QueryRow, Refusal } from "../src/core/queries.ts";

const DIR = "/proj/.wireview/render-queries";
const TEMPLATE = "/proj/app/templates/app/list.html";
const PYTHON = "/proj/app/live.py";
const DIGEST = "d".repeat(64);
const STAT: [string, string] = ["1791465001123456789", "4211"];
const T0 = Date.parse("2026-10-08T13:00:00.000Z");
const MINUTE = 60_000;

function at(ms: number): string {
  return new Date(ms).toISOString();
}

/** What the server keeps of the variable on each line of TEMPLATE_LINES. */
const TEXTS: Record<number, string> = { 3: "item.name", 5: "items.count" };

function templateRow(line: number, count: number, extra: Partial<QueryRow> = {}, text = TEXTS[line] ?? "item.name"): QueryRow {
  return {
    by: 0,
    count,
    sql: `SELECT ${line}`,
    template: { file: TEMPLATE, rel: "app/templates/app/list.html", name: "app/list.html", source: DIGEST, line, node: "{{ }}", text },
    ...extra,
  };
}

function propertyRow(count: number, stat: [string, string] = STAT): QueryRow {
  return { by: 0, count, sql: "SELECT COUNT(*)", property: { name: "total", owner: "app.live.List", async: false, file: PYTHON, rel: "app/live.py", line: 4, stat } };
}

function record(ms: number, rows: QueryRow[], components: string[] = ["app.live.List"], extra: Partial<QueryRecord> = {}): QueryRecord {
  return {
    version: "1.0",
    at: at(ms),
    process: "20261008T130000-1",
    segment: 1,
    base: "/proj",
    kind: "render",
    count: rows.reduce((sum, row) => sum + row.count, 0),
    renders: components.map((component, index) => ({ kind: "render", component, name: component.split(".").pop()!, id: `id${index}`, why: "http" })),
    rows,
    ...extra,
  };
}

const TEMPLATE_LINES = ["<ul>", "{% for item in items %}", "<li>{{ item.name }}</li>", "{% endfor %}", "{{ items.count }}"];
const PYTHON_LINES = ["class List(Component):", "", "    @property", "    def total(self):", "        return Item.objects.count()"];

function facts(kind: "template" | "python", overrides: Partial<DocumentFacts> = {}): DocumentFacts {
  const lines = kind === "template" ? TEMPLATE_LINES : PYTHON_LINES;
  return {
    path: kind === "template" ? TEMPLATE : PYTHON,
    kind,
    dirty: false,
    lineText: (line) => lines[line - 1],
    digest: () => DIGEST,
    stat: () => STAT,
    ...overrides,
  };
}

const OPTIONS: HintOptions = { now: T0 + MINUTE, maxAge: 30 * MINUTE, mapRelative: false };

function state(...records: QueryRecord[]): QueryState {
  const kept = new QueryState();
  for (const each of records) kept.add(each, each.process);
  return kept;
}

const hints = (kept: QueryState, document = facts("template"), options = OPTIONS) => hintsFor(kept, document, options).map(({ line, label }) => ({ line, label }));

// -- version -----------------------------------------------------------------------------------

test("a line is read when its major is known and its minor at least the least", () => {
  assert.deepEqual(checkQueriesVersion({ version: "1.0" }), { ok: true });
  assert.deepEqual(checkQueriesVersion({ version: "1.7" }), { ok: true }, "a key added: still read");
  assert.deepEqual(checkQueriesVersion({ version: "2.0" }), { ok: false, reason: "newer", version: "2.0" });
  assert.deepEqual(checkQueriesVersion({ version: "0.9" }), { ok: false, reason: "older", version: "0.9" });
  assert.deepEqual(checkQueriesVersion({ version: 1 }), { ok: false, reason: "unreadable", version: "1" });
  assert.deepEqual(checkQueriesVersion({}), { ok: false, reason: "unreadable", version: "undefined" });
});

test("a line of an unknown major is left and said once per reason by the caller; one that is not JSON is left silently", () => {
  const kept = new QueryState();
  const refused: Refusal[] = [];
  const changed = ingest(
    kept,
    [
      { kind: "line", process: "p", text: JSON.stringify({ ...record(T0, [templateRow(3, 2)]), version: "2.0" }) },
      { kind: "line", process: "p", text: '{"version": "1.0", "at": ' },
      { kind: "line", process: "p", text: JSON.stringify(record(T0, [templateRow(5, 1)])) },
    ],
    (refusal) => refused.push(refusal),
  );
  assert.equal(changed, true);
  assert.deepEqual(refused, [{ reason: "newer", version: "2.0" }]);
  assert.deepEqual(hints(kept), [{ line: 5, label: "1 query" }]);
});

test("a segment's name is the process and its number", () => {
  assert.deepEqual(segmentOf("20261008T131003-80673.12.jsonl"), { process: "20261008T131003-80673", segment: 12 });
  assert.equal(segmentOf(".gitignore"), undefined);
  assert.equal(segmentOf("20261008T131003-80673.0.jsonl"), undefined);
  assert.equal(segmentOf("notes.1.jsonl"), undefined);
});

// -- snapshots ---------------------------------------------------------------------------------

test("a class's latest snapshot replaces the one before, and none clears it", () => {
  // B inside A runs 6; B alone runs 6; B alone runs none (설계 메모 §1-2)
  const inside = record(T0, [{ ...templateRow(3, 6), by: 1 }], ["app.live.Shelf", "app.live.List"]);
  const alone = record(T0 + 1000, [templateRow(3, 6)]);
  const fixed = record(T0 + 2000, []);
  assert.deepEqual(hints(state(inside)), [{ line: 3, label: "6 queries" }]);
  assert.deepEqual(hints(state(inside, alone)), [{ line: 3, label: "6 queries" }], "replaced, not added");
  assert.deepEqual(hints(state(inside, alone, fixed)), []);
});

test("an older record does not replace a newer one: two processes' files come in any order", () => {
  const newer = record(T0 + 1000, []);
  const older = { ...record(T0, [templateRow(3, 6)]), process: "20261008T120000-2" };
  assert.deepEqual(hints(state(newer, older)), []);
});

test("the instances of one class in one record are one snapshot; rows outside every render are left", () => {
  const both = record(T0, [templateRow(3, 2), { ...templateRow(3, 4), by: 1 }, { ...templateRow(5, 9), by: undefined }], ["app.live.List", "app.live.List"]);
  const kept = state(both);
  assert.deepEqual(hints(kept), [{ line: 3, label: "6 queries" }]);
  assert.deepEqual(kept.get("app.live.List")?.ids, ["id0", "id1"]);
});

test("a class whose every render was cut at the render limit loses its old count, and an older record does not bring it back", () => {
  // Review B1 P2-1: 100 renders of other classes kept, the class's own past the limit
  const others = Array.from({ length: 100 }, (_, n) => `app.live.Other${n}`);
  const cut = record(T0 + 1000, [], others, { renders_more: 1, partial: ["app.live.List"] });
  const kept = state(record(T0, [templateRow(3, 6)]), cut);
  assert.deepEqual(hints(kept), []);
  assert.equal(kept.get("app.live.List")?.partial, true);
  kept.add(record(T0 - 500, [templateRow(3, 6)]), "20261008T120000-2"); // a process before this one
  assert.deepEqual(hints(kept), [], "read later, still older");
  kept.add(record(T0 + 2000, [templateRow(3, 2)]), "20261008T130000-1");
  assert.deepEqual(hints(kept), [{ line: 3, label: "2 queries" }], "seen whole again");
});

test("lost records drop what their process said last", () => {
  const kept = state(record(T0, [templateRow(3, 6)]), { ...record(T0, [templateRow(5, 1)], ["app.live.Other"]), process: "20261008T130000-2" });
  assert.equal(kept.forget("20261008T130000-1"), true);
  assert.deepEqual(hints(kept), [{ line: 5, label: "1 query" }]);
});

test("past its limit the state lets the class set longest ago go", () => {
  const kept = new QueryState(2);
  for (const [n, name] of ["a.A", "a.B", "a.C"].entries()) kept.add(record(T0 + n, [], [name]), "p");
  kept.add(record(T0 + 5, [], ["a.B"]), "p");
  kept.add(record(T0 + 6, [], ["a.D"]), "p");
  assert.deepEqual([...kept.all()].map((snapshot) => snapshot.component), ["a.B", "a.D"]);
});

// -- hints ---------------------------------------------------------------------------------------

test("a line's label: a count, a repeat, several components", () => {
  assert.deepEqual(hints(state(record(T0, [templateRow(5, 1)]))), [{ line: 5, label: "1 query" }]);
  assert.deepEqual(hints(state(record(T0, [templateRow(3, 6, { repeated: true })]))), [{ line: 3, label: "⚠ 6× same query" }]);
  assert.deepEqual(hints(state(record(T0, [templateRow(3, 6, { repeated: true }), templateRow(3, 1, { sql: "SELECT other" })]))), [
    { line: 3, label: "⚠ 6× same query · 7 queries" },
  ]);
  const two = state(record(T0, [templateRow(3, 6), { ...templateRow(3, 1), by: 1 }], ["app.live.List", "app.live.Card"]));
  assert.deepEqual(hints(two), [{ line: 3, label: "7 queries · 2 components" }]);
  const [tooltip] = hintsFor(two, facts("template"), OPTIONS).map((hint) => hint.tooltip);
  assert.match(tooltip, /`app\.live\.Card`[\s\S]*`app\.live\.List`/, "one part per class");
  assert.match(tooltip, /```sql\nSELECT 3\n```/);
});

test("the renders of one class that ran one statement from one place are one row (#189)", () => {
  // Three sibling LiveComponents, one statement each: the server writes one row per render
  const siblings = ["app.live.Rack", "app.live.Card", "app.live.Card", "app.live.Card"];
  const rows = [1, 2, 3].map((by) => ({ ...templateRow(3, 1, { repeated: true }), by }));
  const kept = state(record(T0, [...rows, { ...templateRow(5, 1), by: 2 }], siblings));
  assert.deepEqual(hints(kept), [
    { line: 3, label: "⚠ 3× same query" },
    { line: 5, label: "1 query" },
  ]);
  assert.deepEqual(
    kept.get("app.live.Card")!.rows.map((row) => [row.template!.line, row.count]),
    [
      [3, 3],
      [5, 1],
    ],
  );
  assert.equal(rows[0].count, 1, "the record's rows are not changed");
  // Another class on the same line stays apart
  const two = state(record(T0, [{ ...templateRow(3, 1, { repeated: true }), by: 0 }, ...rows], siblings));
  assert.deepEqual(hints(two), [{ line: 3, label: "⚠ 4 queries · 2 components" }]);
});

test("a snapshot older than the limit is not shown", () => {
  const kept = state(record(T0, [templateRow(3, 6)]));
  assert.equal(hints(kept, facts("template"), { ...OPTIONS, now: T0 + 30 * MINUTE }).length, 1);
  assert.deepEqual(hints(kept, facts("template"), { ...OPTIONS, now: T0 + 30 * MINUTE + 1 }), []);
});

test("a template line is told only for the source the server ran, at a line holding its tag, while saved", () => {
  const kept = state(record(T0, [templateRow(3, 6), templateRow(2, 1, { template: { ...templateRow(2, 1).template!, node: "for", text: "for item in items" } })]));
  assert.deepEqual(hints(kept), [
    { line: 2, label: "1 query" },
    { line: 3, label: "6 queries" },
  ]);
  assert.deepEqual(hints(kept, facts("template", { digest: () => "e".repeat(64) })), [], "a cached old compile ran: another source");
  assert.deepEqual(hints(kept, facts("template", { digest: () => undefined })), []);
  assert.deepEqual(hints(kept, facts("template", { dirty: true })), [], "edited: hidden until saved");
  const moved = ["<ul>", "<li>{{ item.name }}</li>", "{% for item in items %}"];
  assert.deepEqual(
    hints(kept, facts("template", { lineText: (line) => moved[line - 1] })),
    [],
    "the tags are not on their lines: the double check holds even when the digest does not catch it",
  );
  const noSource = state(record(T0, [{ ...templateRow(3, 6), template: { ...templateRow(3, 6).template!, source: undefined } }]));
  assert.deepEqual(hints(noSource), [], "no digest (a let: slot drawn later, a loader not checked): never a guess");
});

test("a tag's text the server cut is matched by its start", () => {
  const long = "x".repeat(250);
  const lines = [`{{ ${long} }}`];
  const cut = templateRow(1, 2, {}, long.slice(0, 200));
  assert.deepEqual(hints(state(record(T0, [cut])), facts("template", { lineText: (line) => lines[line - 1] })), [{ line: 1, label: "2 queries" }]);
});

test("a property is told at its def line while the file's stat is the server's, to the nanosecond", () => {
  const kept = state(record(T0, [propertyRow(1)]));
  assert.deepEqual(hints(kept, facts("python")), [{ line: 4, label: "1 query per render" }]);
  // 2**53 and past: one nanosecond apart, the same as JSON numbers
  assert.deepEqual(hints(kept, facts("python", { stat: () => ["1791465001123456788", "4211"] })), []);
  assert.deepEqual(hints(kept, facts("python", { stat: () => [STAT[0], "4212"] })), []);
  assert.deepEqual(hints(kept, facts("python", { dirty: true })), []);
  const shifted = ["", ...PYTHON_LINES];
  assert.deepEqual(hints(kept, facts("python", { lineText: (line) => shifted[line - 1] })), [], "no def of that name on the line");
  assert.deepEqual(hints(kept, facts("template")), [], "a property row is not a template's");
  const unplaced = state(record(T0, [{ ...propertyRow(1), property: { name: "total", owner: "app.live.List" } }]));
  assert.deepEqual(hints(unplaced, facts("python")), [], "no place: the file changed after the server loaded it");
});

test("an async def line is a def line", () => {
  const lines = ["", "", "    @property", "    async def total(self):"];
  assert.deepEqual(hints(state(record(T0, [propertyRow(2)])), facts("python", { lineText: (line) => lines[line - 1] })), [{ line: 4, label: "2 queries per render" }]);
});

test("a record's rel path is matched under the base only when mapping is on", () => {
  const elsewhere = record(T0, [{ ...templateRow(3, 6), template: { ...templateRow(3, 6).template!, file: "/srv/app/templates/app/list.html" } }]);
  const kept = state(elsewhere);
  assert.deepEqual(hints(kept), []);
  assert.deepEqual(hints(kept, facts("template"), { ...OPTIONS, mapRelative: true, base: "/proj" }), [{ line: 3, label: "6 queries" }]);
  assert.deepEqual(hints(kept, facts("template"), { ...OPTIONS, mapRelative: true, base: "/other" }), []);
});

test("a class cut at the record's limits says so in its tooltip", () => {
  const cut = record(T0, [templateRow(3, 6)], ["app.live.List"], { partial: ["app.live.List"], more: { groups: 4, statements: 9 } });
  const [hint] = hintsFor(state(cut), facts("template"), OPTIONS);
  assert.match(hint.tooltip, /cut at its size limit/);
  const [whole] = hintsFor(state(record(T0, [templateRow(3, 6)])), facts("template"), OPTIONS);
  assert.doesNotMatch(whole.tooltip, /cut at its size limit/);
});

// -- writers that overlap ----------------------------------------------------------------------

function spans(...processes: [string, number[]][]): QueryState {
  const kept = new QueryState();
  for (const [process, ats] of processes) for (const ms of ats) kept.add({ ...record(T0 + ms, []), process }, process);
  return kept;
}

const NOW = T0 + 10 * MINUTE;
const AGE = 30 * MINUTE;

test("a restart is not an overlap: one process after another, or ending in the millisecond the next starts", () => {
  assert.equal(spans(["a", [0, 1000]], ["b", [2000, 3000]]).overlapping(NOW, AGE), undefined);
  assert.equal(spans(["a", [0, 1000]], ["b", [1000, 3000]]).overlapping(NOW, AGE), undefined);
  // Read in the other order
  assert.equal(spans(["b", [2000, 3000]], ["a", [0, 1000]]).overlapping(NOW, AGE), undefined);
});

test("spans that strictly cross are an overlap, and no hint is shown", () => {
  const kept = spans(["a", [0, 3000]], ["b", [1000, 2000]]);
  assert.deepEqual(kept.overlapping(NOW, AGE), ["a", "b"]);
  kept.add(record(T0 + 3000, [templateRow(3, 6)]), "a");
  assert.deepEqual(hints(kept, facts("template"), { ...OPTIONS, now: NOW }), []);
  assert.equal(spans(["a", [0, 3000]], ["b", [1000, 2000]]).overlapping(T0 + 3000 + AGE + 1, AGE), undefined, "only within the time limit");
});

test("an overlap the server's skipping hides is not told: the detection is best effort", () => {
  // B writes K=0, A writes K=6, B's next 0 is skipped as unchanged: [0] and [1] never cross
  assert.equal(spans(["b", [0]], ["a", [1000]]).overlapping(NOW, AGE), undefined);
});

// -- reading the segments --------------------------------------------------------------------

class Disk implements Files {
  readonly files = new Map<string, Buffer>();
  reads: [string, number, number][] = [];

  write(name: string, text: string): void {
    const path = nodePath.join(DIR, name);
    this.files.set(path, Buffer.concat([this.files.get(path) ?? Buffer.alloc(0), Buffer.from(text)]));
  }

  remove(name: string): void {
    this.files.delete(nodePath.join(DIR, name));
  }

  list(directory: string): string[] | undefined {
    const names = [...this.files.keys()].filter((path) => nodePath.dirname(path) === directory).map((path) => nodePath.basename(path));
    return names.length ? names : undefined;
  }

  size(path: string): number | undefined {
    return this.files.get(path)?.length;
  }

  read(path: string, start: number, end: number): Uint8Array | undefined {
    this.reads.push([path, start, end]);
    return this.files.get(path)?.subarray(start, end);
  }
}

const P = "20261008T130000-1";
const line = (ms: number, rows: QueryRow[] = []) => `${JSON.stringify(record(T0 + ms, rows))}\n`;
const texts = (events: ReturnType<Tailer["scan"]>) => events.map((event) => (event.kind === "line" ? (JSON.parse(event.text) as QueryRecord).at : "lost"));

test("a reader that starts reads the whole of a small file, its one line included", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  assert.deepEqual(texts(new Tailer(disk).scan(DIR)), [at(T0)]);
  const empty = new Disk();
  empty.write(`${P}.1.jsonl`, "");
  assert.deepEqual(new Tailer(empty).scan(DIR), []);
});

test("a reader that starts reads the end of a large file from a whole line", () => {
  // Lines of 1 KiB: the read starts on a line's first byte or in its middle
  const filler = (n: number) => line(n).slice(0, -1).padEnd(1023, " ") + "\n";
  const lines = (count: number) => Array.from({ length: count }, (_, n) => filler(n)).join("");
  for (const [text, expected, first] of [
    [lines(512), 512, 0], // exactly FIRST_READ: all of it
    [lines(513), 512, 1], // the read starts right after a newline: that line is whole
    [lines(513) + "x".repeat(100), 511, 2], // it starts in the middle of a line: that one is left, so is the unfinished end
  ] as const) {
    const disk = new Disk();
    disk.write(`${P}.1.jsonl`, text);
    assert.equal(text.length > FIRST_READ, first > 0);
    const found = texts(new Tailer(disk).scan(DIR));
    assert.equal(found.length, expected, `${text.length}`);
    assert.equal(found[0], at(T0 + first));
  }
});

test("only the last segment of a process is read at the start, from where it ends", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  disk.write(`${P}.2.jsonl`, line(1000));
  const tailer = new Tailer(disk);
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 1000)]);
  disk.write(`${P}.2.jsonl`, line(2000));
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 2000)], "then what is added");
});

test("a segment that appears later is read from its start, after the rest of the one before", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  const tailer = new Tailer(disk);
  tailer.scan(DIR);
  disk.write(`${P}.1.jsonl`, line(1000));
  disk.write(`${P}.2.jsonl`, line(2000));
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 1000), at(T0 + 2000)]);
  // A new process after the start: from its first line, whatever its size
  disk.write("20261008T140000-2.1.jsonl", line(3000) + line(4000));
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 3000), at(T0 + 4000)]);
});

test("the end of a file without a newline is held until the server finishes the line", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  const tailer = new Tailer(disk);
  tailer.scan(DIR);
  const next = line(1000);
  disk.write(`${P}.1.jsonl`, next.slice(0, 20));
  assert.deepEqual(tailer.scan(DIR), []);
  disk.write(`${P}.1.jsonl`, next.slice(20));
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 1000)]);
});

test("a segment missed, or gone before it was read to its end, is a loss", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  const tailer = new Tailer(disk);
  tailer.scan(DIR);
  // 2 was filled and given way to 3, which removed 1, all between two reads
  disk.write(`${P}.1.jsonl`, line(500));
  disk.write(`${P}.2.jsonl`, line(1000));
  disk.write(`${P}.3.jsonl`, line(2000));
  disk.remove(`${P}.1.jsonl`);
  assert.deepEqual(texts(tailer.scan(DIR)), ["lost", at(T0 + 1000), at(T0 + 2000)]);
  // A number skipped: the file being written was removed and the next opened
  disk.write(`${P}.5.jsonl`, line(3000));
  assert.deepEqual(texts(tailer.scan(DIR)), ["lost", at(T0 + 3000)]);
});

test("a process whose files all went is a loss when it writes again, in a later scan", () => {
  // Review B1 P2-2: the 0 that cleared A was appended, then the file removed before a read;
  // the writer finds it unlinked and opens its next number, which a later scan sees
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0, [templateRow(3, 6)]));
  const tailer = new Tailer(disk);
  const kept = new QueryState();
  ingest(kept, tailer.scan(DIR), () => {});
  assert.deepEqual(hints(kept), [{ line: 3, label: "6 queries" }]);
  disk.write(`${P}.1.jsonl`, line(1000));
  disk.remove(`${P}.1.jsonl`);
  disk.write("20261008T120000-9.1.jsonl", line(1500, [])); // the directory is still there, with another's file
  assert.deepEqual(texts(tailer.scan(DIR)).filter((kind) => kind === "lost"), [], "gone is not yet lost");
  assert.deepEqual(hints(kept), [{ line: 3, label: "6 queries" }]);
  disk.write(`${P}.2.jsonl`, `${JSON.stringify(record(T0 + 2000, [], ["app.live.Other"]))}\n`);
  const events = tailer.scan(DIR);
  assert.deepEqual(texts(events), ["lost", at(T0 + 2000)]);
  ingest(kept, events, () => {});
  assert.deepEqual(hints(kept), []);
});

test("a directory that cannot be listed for a moment loses nothing", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0));
  const tailer = new Tailer(disk);
  tailer.scan(DIR);
  const files = new Map(disk.files);
  disk.files.clear(); // list() answers undefined: no directory
  assert.deepEqual(tailer.scan(DIR), []);
  for (const [path, bytes] of files) disk.files.set(path, bytes);
  disk.write(`${P}.1.jsonl`, line(1000));
  assert.deepEqual(texts(tailer.scan(DIR)), [at(T0 + 1000)]);
});

test("a file shorter than what was read of it is a loss, and is read again from its start", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0) + line(1000));
  const tailer = new Tailer(disk);
  tailer.scan(DIR);
  disk.remove(`${P}.1.jsonl`);
  disk.write(`${P}.1.jsonl`, line(2000));
  assert.deepEqual(texts(tailer.scan(DIR)), ["lost", at(T0 + 2000)]);
});

test("a loss drops what that process said last, and what comes after it counts", () => {
  const disk = new Disk();
  disk.write(`${P}.1.jsonl`, line(0, [templateRow(3, 6)]));
  const tailer = new Tailer(disk);
  const kept = new QueryState();
  ingest(kept, tailer.scan(DIR), () => {});
  assert.deepEqual(hints(kept), [{ line: 3, label: "6 queries" }]);
  // The 0 that cleared it was in the segment that was missed
  disk.write(`${P}.3.jsonl`, `${JSON.stringify(record(T0 + 2000, [], ["app.live.Other"]))}\n`);
  ingest(kept, tailer.scan(DIR), () => {});
  assert.deepEqual(hints(kept), []);
  assert.ok(kept.get("app.live.Other"));
});

test("a restart read at the start is not an overlap, even when the older process's early records are past the tail", () => {
  const disk = new Disk();
  const old = "20261008T120000-1";
  disk.write(`${old}.1.jsonl`, line(0));
  disk.write(`${old}.2.jsonl`, line(1000) + line(2000));
  disk.write(`${P}.1.jsonl`, line(3000) + line(4000));
  const kept = new QueryState();
  ingest(kept, new Tailer(disk).scan(DIR), () => {});
  assert.equal(kept.overlapping(T0 + 5000, AGE), undefined);
});
