// The extension's reading of the render-part SQL records, run on the files the
// library really wrote: tests/test_vscode_extension.py (the contract between the
// two, #188) calls it. Not a test file itself.
//
//   node test/queries-driver.ts <spec.json>
//
// The spec: {"directory": …, "documents": [{"path": …, "kind": "template" | "python"}],
// "base"?: …, "mapRelative"?: false, "maxAge"?: ms, "now"?: ms}. It reads the
// directory once, as an editor that has just started does, and prints
// {"hints": {path: [{line, label, tooltip}]}, "refused": […], "overlapping": […] | null}.
import { readFileSync, realpathSync } from "node:fs";

import { hintsFor, ingest, QueryState, Tailer } from "../src/core/queries.ts";
import type { DocumentFacts, Hint, Refusal } from "../src/core/queries.ts";
import { Digests, nodeFiles, statOf } from "../src/queryFiles.ts";

interface Spec {
  directory: string;
  documents: { path: string; kind: "template" | "python" }[];
  base?: string;
  mapRelative?: boolean;
  maxAge?: number;
  now?: number;
}

const spec = JSON.parse(readFileSync(process.argv[2], "utf8")) as Spec;
const state = new QueryState();
const refused: Refusal[] = [];
ingest(state, new Tailer(nodeFiles).scan(spec.directory), (refusal) => refused.push(refusal));

const now = spec.now ?? Date.now();
const maxAge = spec.maxAge ?? 30 * 60_000;
const digests = new Digests();
const hints: Record<string, Hint[]> = {};
for (const document of spec.documents) {
  const path = realpathSync(document.path);
  const lines = readFileSync(path, "utf8").split(/\r\n|\r|\n/);
  const facts: DocumentFacts = {
    path,
    kind: document.kind,
    dirty: false,
    lineText: (line) => lines[line - 1],
    digest: () => digests.of(path),
    stat: () => statOf(path),
  };
  hints[document.path] = hintsFor(state, facts, { now, maxAge, base: spec.base, mapRelative: spec.mapRelative ?? false });
}
process.stdout.write(JSON.stringify({ hints, refused, overlapping: state.overlapping(now, maxAge) ?? null }));
