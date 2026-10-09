// The render-part SQL a dev server writes for the editor (#188): a line's
// version, reading the files as they grow, the latest snapshot of each component
// class, and the hints a document gets. The format is the library's, described
// in docs/features/render-queries.md ("편집기로 보내기"); the reasons for each
// rule are in docs/design/render-queries-editor.md §2.
//
// Nothing here touches the disk: the reads come through `Files`, a document's
// digest and stat through `DocumentFacts`. src/queries.ts watches and reads,
// src/extension.ts draws.
import * as nodePath from "node:path";

/** The major version of the format this extension reads. */
export const QUERIES_MAJOR = 1;
/** The least minor version of each major it reads. */
const READS: Record<number, number> = { 1: 0 };
/** What a refused line is told by, in the output channel. */
export const READABLE_QUERIES = Object.entries(READS)
  .map(([major, minor]) => `${major}.${minor}+`)
  .join(", ");

export type QueriesVersionCheck = { ok: true } | { ok: false; reason: "older" | "newer" | "unreadable"; version: string };

/** Whether the extension reads a line of this version: a known major, a minor at least the least it reads. */
export function checkQueriesVersion(record: unknown): QueriesVersionCheck {
  const version = typeof record === "object" && record !== null ? (record as { version?: unknown }).version : undefined;
  const match = typeof version === "string" ? /^(\d+)\.(\d+)$/.exec(version) : null;
  if (!match) return { ok: false, reason: "unreadable", version: String(version) };
  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (major > QUERIES_MAJOR) return { ok: false, reason: "newer", version: match[0] };
  const least = READS[major];
  if (least === undefined || minor < least) return { ok: false, reason: "older", version: match[0] };
  return { ok: true };
}

export interface TemplatePlace {
  file?: string;
  rel?: string;
  name?: string;
  source?: string;
  line: number;
  node?: string;
  text?: string;
  approximate?: boolean;
}

export interface PropertyPlace {
  name: string;
  owner?: string;
  async?: boolean;
  file?: string;
  rel?: string;
  line?: number;
  /** `[st_mtime_ns, st_size]` as decimal strings: the nanoseconds are past what a JSON number holds. */
  stat?: [string, string];
}

export interface QueryRow {
  by?: number;
  count: number;
  repeated?: boolean;
  sql: string;
  truncated?: boolean;
  template?: TemplatePlace;
  property?: PropertyPlace;
}

export interface RenderEntry {
  kind: string;
  component: string;
  name: string;
  id: string | null;
  why?: string;
}

export interface QueryRecord {
  version: string;
  at: string;
  process: string;
  segment: number;
  base: string | null;
  kind: string;
  detail?: string;
  count: number;
  renders: RenderEntry[];
  renders_more?: number;
  rows: QueryRow[];
  more?: { groups: number; statements: number };
  partial?: string[];
}

/** What one component class ran in the render scopes of one record: the unit a reader keeps. */
export interface Snapshot {
  component: string;
  name: string;
  /** The ids of the instances drawn, first seen first. */
  ids: string[];
  why: string;
  process: string;
  /** When the work ended, in ms since the epoch. */
  at: number;
  /** Some of the class's renders or rows were cut at the record's limits: not a whole count. */
  partial: boolean;
  /** The record left statements out (`more`). */
  cut: boolean;
  rows: QueryRow[];
}

function isRecord(value: unknown): value is QueryRecord {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Partial<QueryRecord>;
  return typeof record.at === "string" && Array.isArray(record.renders) && Array.isArray(record.rows);
}

/** How many `at`s of a process are kept to tell its observed span. */
const SPAN_LIMIT = 4096;

/**
 * The latest snapshot of each component class, and the span each process was seen
 * writing in. A record replaces a class's snapshot, it never adds to it: a class
 * drawn alone after it was drawn inside another one is counted once, and a class
 * fixed to run nothing clears its old count. An older record does not replace a
 * newer one: the files of two processes are read in no particular order.
 */
export class QueryState {
  private readonly snapshots = new Map<string, Snapshot>();
  private readonly spans = new Map<string, number[]>();
  private readonly limit: number;

  constructor(limit = 2000) {
    this.limit = limit;
  }

  get size(): number {
    return this.snapshots.size;
  }

  all(): IterableIterator<Snapshot> {
    return this.snapshots.values();
  }

  get(component: string): Snapshot | undefined {
    return this.snapshots.get(component);
  }

  /** Take a record of `process`; whether a snapshot changed. */
  add(record: QueryRecord, process: string): boolean {
    const at = Date.parse(record.at);
    if (Number.isNaN(at)) return false;
    let span = this.spans.get(process);
    if (!span) this.spans.set(process, (span = []));
    span.push(at);
    if (span.length > SPAN_LIMIT) span.splice(0, span.length - SPAN_LIMIT);

    const partial = new Set(Array.isArray(record.partial) ? record.partial : []);
    const found = new Map<string, Snapshot>();
    for (const render of record.renders) {
      if (typeof render?.component !== "string") continue;
      let snapshot = found.get(render.component);
      if (!snapshot) {
        snapshot = {
          component: render.component,
          name: typeof render.name === "string" ? render.name : render.component,
          ids: [],
          why: typeof render.why === "string" ? render.why : "",
          process,
          at,
          partial: partial.has(render.component),
          cut: record.more !== undefined,
          rows: [],
        };
        found.set(render.component, snapshot);
      }
      if (typeof render.id === "string" && !snapshot.ids.includes(render.id)) snapshot.ids.push(render.id);
    }
    // A row is one render's. The renders of one class -- sibling LiveComponents, the rows of a
    // loop drawn as components -- that ran one statement from one place are one row of the
    // snapshot, their counts summed: three siblings once each is "3× same query", not three 1×.
    const same = new Map<string, QueryRow>();
    for (const row of record.rows) {
      // A row outside every render (a handler's own) has no class: 1.0 readers leave it
      if (typeof row?.by !== "number" || typeof row.count !== "number") continue;
      const component = record.renders[row.by]?.component;
      const snapshot = component === undefined ? undefined : found.get(component);
      if (!snapshot) continue;
      const { by: _by, count: _count, ...place } = row;
      const key = `${component}\u0000${JSON.stringify(place)}`;
      const seen = same.get(key);
      if (seen) {
        seen.count += row.count;
        continue;
      }
      const copy = { ...row };
      same.set(key, copy);
      snapshot.rows.push(copy);
    }
    // A class whose every render was cut at the render limit is still seen at this `at`, just not
    // whole: its last snapshot no longer holds, and an older record read later must not bring it back
    for (const component of partial) {
      if (typeof component !== "string" || found.has(component)) continue;
      const name = component.slice(component.lastIndexOf(".") + 1) || component;
      found.set(component, { component, name, ids: [], why: "", process, at, partial: true, cut: record.more !== undefined, rows: [] });
    }
    let changed = false;
    for (const snapshot of found.values()) {
      const before = this.snapshots.get(snapshot.component);
      if (before && before.at > snapshot.at) continue;
      // Last set, last in line: the first key is the one to let go
      this.snapshots.delete(snapshot.component);
      this.snapshots.set(snapshot.component, snapshot);
      changed = true;
    }
    while (this.snapshots.size > this.limit) this.snapshots.delete(this.snapshots.keys().next().value as string);
    return changed;
  }

  /** Records of `process` were lost: what it said last may have been undone in them. */
  forget(process: string): boolean {
    let changed = false;
    for (const [component, snapshot] of this.snapshots) {
      if (snapshot.process !== process) continue;
      this.snapshots.delete(component);
      changed = true;
    }
    return changed;
  }

  clear(): void {
    this.snapshots.clear();
    this.spans.clear();
  }

  /**
   * Two processes whose observed spans cross, within the last `maxAge` ms: more
   * than one writer at once, which the format does not support. Best effort: a
   * span is what was read of a process, a part of its life, so processes that
   * ran one after the other never cross; writers that did overlap may be missed.
   */
  overlapping(now: number, maxAge: number): [string, string] | undefined {
    const since = now - maxAge;
    const spans: { process: string; first: number; last: number }[] = [];
    for (const [process, ats] of this.spans) {
      const recent = ats.filter((at) => at >= since);
      if (recent.length !== ats.length) this.spans.set(process, recent);
      if (!recent.length) continue;
      spans.push({ process, first: Math.min(...recent), last: Math.max(...recent) });
    }
    spans.sort((a, b) => a.first - b.first);
    let reach: (typeof spans)[number] | undefined;
    for (const span of spans) {
      // Strictly: one ending in the millisecond the next starts is a restart
      if (reach && reach.last > span.first) return [reach.process, span.process];
      if (!reach || span.last > reach.last) reach = span;
    }
    return undefined;
  }
}

// -- reading the files -----------------------------------------------------------------------

/** What the reader needs of the disk. Each answers undefined for a file or directory that is not there. */
export interface Files {
  list(directory: string): string[] | undefined;
  size(path: string): number | undefined;
  read(path: string, start: number, end: number): Uint8Array | undefined;
}

/** How much of each process's last segment a reader that starts reads: the rest is older. */
export const FIRST_READ = 512 * 1024;

/** `<UTC start>-<pid>.<n>.jsonl`, a segment of one process's records. */
export function segmentOf(name: string): { process: string; segment: number } | undefined {
  const match = /^(\d{8}T\d{6}-\d+)\.([1-9]\d*)\.jsonl$/.exec(name);
  return match ? { process: match[1], segment: Number(match[2]) } : undefined;
}

export type TailEvent = { kind: "line"; process: string; text: string } | { kind: "lost"; process: string };

const NEWLINE = 10;
/** How many processes whose files went a reader remembers. */
const GONE_LIMIT = 1024;
const decoder = new TextDecoder("utf-8");

/**
 * Follows each process's segments as the server writes them. A line counts once it
 * ends in a newline: the end of a file without one is the server still writing,
 * read again next time. The server only adds to a file and moves on to the next
 * number, so a missing number, or a file shorter than what was read of it, means
 * records were lost: a `lost` event says so, in order with the lines.
 */
export class Tailer {
  private readonly files: Files;
  private readonly cursors = new Map<string, { segment: number; position: number }>();
  /** Processes followed before whose file went, with the segment they were at: what they wrote before it went is unknown. */
  private readonly gone = new Map<string, number>();
  private started = false;

  constructor(files: Files) {
    this.files = files;
  }

  scan(directory: string): TailEvent[] {
    const events: TailEvent[] = [];
    const segments = new Map<string, number[]>();
    const names = this.files.list(directory);
    if (names === undefined) {
      // No directory, or none readable now: nothing is known to be gone, the cursors stay
      this.started = true;
      return events;
    }
    for (const name of names) {
      const found = segmentOf(name);
      if (!found) continue;
      let list = segments.get(found.process);
      if (!list) segments.set(found.process, (list = []));
      list.push(found.segment);
    }
    // A process whose files are gone (swept, or removed by hand) is remembered: if it writes again
    // (a writer whose file was removed opens its next number) what it wrote before it went is lost
    for (const process of this.cursors.keys()) if (!segments.has(process)) this.leave(process);
    const first = !this.started;
    this.started = true;
    for (const [process, numbers] of segments) {
      numbers.sort((a, b) => a - b);
      const path = (segment: number) => nodePath.join(directory, `${process}.${segment}.jsonl`);
      let cursor = this.cursors.get(process);
      const left = this.gone.get(process);
      if (!cursor && left !== undefined) {
        this.gone.delete(process);
        events.push({ kind: "lost", process });
        // On from the first number after the one it left: an older one was read already
        const next = numbers.find((segment) => segment > left);
        const last = numbers[numbers.length - 1];
        cursor = next !== undefined ? { segment: next, position: 0 } : { segment: last, position: this.files.size(path(last)) ?? 0 };
        this.cursors.set(process, cursor);
      }
      if (!cursor) {
        if (first) {
          // There before the reader: the end of the last segment is enough
          const segment = numbers[numbers.length - 1];
          const size = this.files.size(path(segment)) ?? 0;
          cursor = { segment, position: Math.max(0, size - FIRST_READ) };
          if (cursor.position > 0) {
            // Start at a line: whole when the byte before is a newline, else past the first one
            const before = this.files.read(path(segment), cursor.position - 1, size);
            if (!before) continue;
            const newline = before.indexOf(NEWLINE);
            if (newline < 0) cursor.position = size;
            else cursor.position += newline;
          }
        } else {
          // New since: from its beginning
          cursor = { segment: numbers[0], position: 0 };
        }
        this.cursors.set(process, cursor);
      }
      for (;;) {
        const here = numbers.includes(cursor.segment) ? this.files.size(path(cursor.segment)) : undefined;
        if (here === undefined) {
          // What was left of it to read is gone
          const later = numbers.find((segment) => segment > cursor!.segment);
          if (later === undefined) {
            this.leave(process);
            break;
          }
          events.push({ kind: "lost", process });
          cursor.segment = later;
          cursor.position = 0;
          continue;
        }
        if (here < cursor.position) {
          events.push({ kind: "lost", process });
          cursor.position = 0;
        }
        if (here > cursor.position) {
          const chunk = this.files.read(path(cursor.segment), cursor.position, here);
          if (chunk) {
            const end = chunk.lastIndexOf(NEWLINE);
            if (end >= 0) {
              for (const text of decoder.decode(chunk.subarray(0, end)).split("\n")) if (text) events.push({ kind: "line", process, text });
              cursor.position += end + 1;
            }
          }
        }
        const next = numbers.find((segment) => segment > cursor!.segment);
        if (next === undefined) break;
        // Read to its end: the server opens the next number only after a whole line
        if (next !== cursor.segment + 1) events.push({ kind: "lost", process });
        cursor.segment = next;
        cursor.position = 0;
      }
    }
    return events;
  }

  private leave(process: string): void {
    const cursor = this.cursors.get(process);
    this.cursors.delete(process);
    if (cursor) this.gone.set(process, cursor.segment);
    // Swept processes never come back: the oldest marks can go
    if (this.gone.size > GONE_LIMIT) this.gone.delete(this.gone.keys().next().value as string);
  }
}

/** Why a line was left: told once in the output channel. */
export type Refusal = { reason: "older" | "newer" | "unreadable"; version: string };

/** Put what the files said into the state; whether a snapshot changed. A line that is not JSON is left without a word. */
export function ingest(state: QueryState, events: TailEvent[], refused: (refusal: Refusal) => void): boolean {
  let changed = false;
  for (const event of events) {
    if (event.kind === "lost") {
      if (state.forget(event.process)) changed = true;
      continue;
    }
    let record: unknown;
    try {
      record = JSON.parse(event.text);
    } catch {
      continue;
    }
    const version = checkQueriesVersion(record);
    if (!version.ok) {
      refused({ reason: version.reason, version: version.version });
      continue;
    }
    if (isRecord(record) && state.add(record, event.process)) changed = true;
  }
  return changed;
}

// -- hints -----------------------------------------------------------------------------------

/** What hints need of a document. The digest and stat are of the file on disk, asked only when needed. */
export interface DocumentFacts {
  /** The document's real path: the records name files with links resolved. */
  path: string;
  kind: "template" | "python";
  /** Edited and not saved: the lines may not be the ones the server ran. */
  dirty: boolean;
  /** The text of a line, 1-based. */
  lineText(line: number): string | undefined;
  /** SHA-256 of the file with its line ends made `\n`. */
  digest(): string | undefined;
  /** `[mtime_ns, size]` as decimal strings. */
  stat(): readonly [string, string] | undefined;
}

export interface HintOptions {
  now: number;
  /** A snapshot older than this many ms is not shown. */
  maxAge: number;
  /** Where a record's `rel` paths are taken from, when `mapRelative` is on: the manage.py directory. */
  base?: string;
  mapRelative: boolean;
}

export interface Hint {
  /** 1-based. */
  line: number;
  label: string;
  /** Markdown. */
  tooltip: string;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A tag's text as the server keeps it: cut at this many characters. */
const TEXT_WIDTH = 200;

/** Whether `line` holds the variable or tag the server ran: the double check of a matching digest. */
function holdsTag(line: string, place: TemplatePlace): boolean {
  if (typeof place.text !== "string") return false;
  const text = place.text.trim();
  // Cut by the server: the start is all there is to match
  const close = place.text.length >= TEXT_WIDTH ? "" : place.node === "{{ }}" ? "\\s*\\}\\}" : "\\s*%\\}";
  const open = place.node === "{{ }}" ? "\\{\\{\\s*" : "\\{%\\s*";
  return new RegExp(open + escapeRegExp(text) + close).test(line);
}

function holdsDef(line: string, name: string): boolean {
  return new RegExp(`^\\s*(?:async\\s+)?def\\s+${escapeRegExp(name)}\\b`).test(line);
}

function names(path: string | undefined, rel: string | undefined, document: string, options: HintOptions): boolean {
  if (path !== undefined && nodePath.normalize(path) === document) return true;
  if (!options.mapRelative || !options.base || typeof rel !== "string") return false;
  return nodePath.normalize(nodePath.join(options.base, rel)) === document;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function age(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

/** Inline code that holds anything. */
function code(text: string): string {
  const ticks = Math.max(0, ...[...text.matchAll(/`+/g)].map((match) => match[0].length));
  const fence = "`".repeat(ticks + 1);
  return ticks ? `${fence} ${text} ${fence}` : `${fence}${text}${fence}`;
}

function sqlBlock(sql: string): string {
  const ticks = Math.max(2, ...[...sql.matchAll(/`+/g)].map((match) => match[0].length));
  const fence = "`".repeat(ticks + 1);
  return `${fence}sql\n${sql}\n${fence}`;
}

interface Found {
  snapshot: Snapshot;
  row: QueryRow;
}

function tooltip(found: Found[], python: boolean, now: number): string {
  const byClass = new Map<string, Found[]>();
  for (const item of found) {
    let list = byClass.get(item.snapshot.component);
    if (!list) byClass.set(item.snapshot.component, (list = []));
    list.push(item);
  }
  const parts: string[] = [];
  for (const [component, items] of [...byClass].sort(([a], [b]) => a.localeCompare(b))) {
    const snapshot = items[0].snapshot;
    const total = items.reduce((sum, item) => sum + item.row.count, 0);
    const ids = snapshot.ids.length ? ` · id ${snapshot.ids.slice(0, 3).map(code).join(", ")}${snapshot.ids.length > 3 ? ", …" : ""}` : "";
    const why = snapshot.why ? ` · ${code(snapshot.why)}` : "";
    const lines = [
      `**${plural(total, "query", "queries")}${python ? " per render" : ""}** · ${code(snapshot.name)}${ids}${why} · ${age(now - snapshot.at)}`,
      "",
      `${code(component)}`,
    ];
    const property = items.find((item) => item.row.property)?.row.property;
    if (property) lines.push("", `Property ${code(property.name)}${property.async ? " (async)" : ""}${property.owner ? `, defined on ${code(property.owner)}` : ""}`);
    const rows = [...items].sort((a, b) => Number(Boolean(b.row.repeated)) - Number(Boolean(a.row.repeated)) || b.row.count - a.row.count);
    for (const { row } of rows) {
      const notes = [
        row.repeated ? `**${row.count}× the same query**` : plural(row.count, "time", "times"),
        row.template?.approximate ? "placed at the nearest tag" : "",
        row.truncated ? "statement cut at 1,000 characters" : "",
      ].filter(Boolean);
      lines.push("", notes.join(" · "), "", sqlBlock(row.sql));
    }
    if (snapshot.partial || snapshot.cut)
      lines.push("", "_The record was cut at its size limit: this class may have run more than is shown._");
    parts.push(lines.join("\n"));
  }
  parts.push(
    python
      ? "_From the dev server's last render of each class. Shown while this file is unchanged since the server loaded it; the body that ran is not checked._"
      : "_From the dev server's last render of each class, of this very file._",
  );
  return parts.join("\n\n---\n\n");
}

function label(found: Found[], python: boolean): string {
  const total = found.reduce((sum, item) => sum + item.row.count, 0);
  const classes = new Set(found.map((item) => item.snapshot.component)).size;
  const repeated = found.filter((item) => item.row.repeated);
  const warn = repeated.length ? "⚠ " : "";
  const others = classes > 1 ? ` · ${classes} components` : "";
  if (python) return `${warn}${plural(total, "query", "queries")} per render${others}`;
  if (classes === 1 && repeated.length) {
    const most = Math.max(...repeated.map((item) => item.row.count));
    return `⚠ ${most}× same query${total > most ? ` · ${plural(total, "query", "queries")}` : ""}`;
  }
  return `${warn}${plural(total, "query", "queries")}${others}`;
}

/**
 * The hints of one document: a count at the end of each template line, or property
 * `def` line, the latest snapshots say ran SQL. A line is told only when it is
 * certain to be the line the server ran:
 *
 * - a template: saved, the digest of the source the running template was compiled
 *   from equal to the file's, and the variable or tag on that line;
 * - a property: saved, the file's stat equal to the one the server saw (it checked
 *   the file was not changed after it loaded the module), and its `def` on that line.
 *
 * Nothing is told while two processes write at once.
 */
export function hintsFor(state: QueryState, document: DocumentFacts, options: HintOptions): Hint[] {
  if (document.dirty || !state.size) return [];
  if (state.overlapping(options.now, options.maxAge)) return [];
  const python = document.kind === "python";
  const byLine = new Map<number, Found[]>();
  let digest: string | undefined | null = null;
  let stat: readonly [string, string] | undefined | null = null;
  for (const snapshot of state.all()) {
    if (options.now - snapshot.at > options.maxAge) continue;
    for (const row of snapshot.rows) {
      let line: number;
      if (python) {
        const place = row.property;
        if (!place || typeof place.line !== "number" || !Array.isArray(place.stat)) continue;
        if (!names(place.file, place.rel, document.path, options)) continue;
        if (stat === null) stat = document.stat();
        if (!stat || stat[0] !== place.stat[0] || stat[1] !== place.stat[1]) continue;
        if (!holdsDef(document.lineText(place.line) ?? "", place.name)) continue;
        line = place.line;
      } else {
        const place = row.template;
        if (!place || typeof place.line !== "number" || typeof place.source !== "string") continue;
        if (!names(place.file, place.rel, document.path, options)) continue;
        if (digest === null) digest = document.digest();
        if (digest !== place.source) continue;
        if (!holdsTag(document.lineText(place.line) ?? "", place)) continue;
        line = place.line;
      }
      let list = byLine.get(line);
      if (!list) byLine.set(line, (list = []));
      list.push({ snapshot, row });
    }
  }
  return [...byLine]
    .sort(([a], [b]) => a - b)
    .map(([line, found]) => ({ line, label: label(found, python), tooltip: tooltip(found, python, options.now) }));
}
