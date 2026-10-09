// One folder's render-part SQL records (#188): watching the directory the dev
// server writes to and reading what it adds. A reader belongs to the generation of
// the folder project that started it: once its signal is aborted (another
// manage.py, another directory, the folder gone) it reads nothing more and puts
// nothing more in its state. Nothing is read before the workspace is trusted.
import * as vscode from "vscode";

import { hintsFor, ingest, QueryState, Tailer } from "./core/queries.ts";
import type { DocumentFacts, Files, Hint, HintOptions, Refusal } from "./core/queries.ts";

/** Events from the watcher come in bursts: one read after a quiet moment. */
const QUIET = 200;

export class FolderQueries implements vscode.Disposable {
  readonly directory: string;
  /** The manage.py directory: what a record's `rel` paths are under, when mapping them is on. */
  readonly base: string;
  readonly state = new QueryState();
  private readonly tailer: Tailer;
  private readonly signal: AbortSignal;
  private readonly changed: () => void;
  private readonly refused: (refusal: Refusal) => void;
  private readonly watcher: vscode.Disposable;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;

  constructor(options: {
    directory: string;
    base: string;
    signal: AbortSignal;
    files: Files;
    changed: () => void;
    refused: (refusal: Refusal) => void;
  }) {
    this.directory = options.directory;
    this.base = options.base;
    this.signal = options.signal;
    this.changed = options.changed;
    this.refused = options.refused;
    this.tailer = new Tailer(options.files);
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(this.directory, "*.jsonl"));
    const soon = () => this.soon();
    watcher.onDidCreate(soon);
    watcher.onDidChange(soon);
    watcher.onDidDelete(soon);
    this.watcher = watcher;
  }

  private get live(): boolean {
    return !this.disposed && !this.signal.aborted && vscode.workspace.isTrusted;
  }

  /** Read what was added since the last read. The one place the records are read. */
  read(): void {
    if (!this.live) return;
    const events = this.tailer.scan(this.directory);
    // Aborted while reading: what was read is the old generation's
    if (!this.live) return;
    if (ingest(this.state, events, this.refused)) this.changed();
  }

  private soon(): void {
    if (!this.live) return;
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.read();
    }, QUIET);
  }

  hints(document: DocumentFacts, options: Omit<HintOptions, "base">): Hint[] {
    if (!this.live) return [];
    return hintsFor(this.state, document, { ...options, base: this.base });
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
    this.watcher.dispose();
  }
}
