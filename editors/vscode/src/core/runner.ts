// How the metadata gets made: which manage.py, which Python, what command line,
// and when to run it again. The adapter spawns the process; the choices are here.
import * as nodePath from "node:path";

/** Directories a project's own manage.py is never in. */
const NOT_THE_PROJECT = new Set(["node_modules", ".venv", "venv", "site-packages", ".git", ".tox", ".nox"]);

/** The manage.py to run among those found: the shallowest one, outside virtualenvs and dependencies. */
export function pickManagePy(found: string[], folder: string): string | undefined {
  const candidates = found
    .map((path) => ({ path, parts: nodePath.relative(folder, path).split(/[\\/]/) }))
    .filter(({ parts }) => parts[0] !== ".." && !parts.some((part) => NOT_THE_PROJECT.has(part)));
  candidates.sort((a, b) => a.parts.length - b.parts.length || a.path.localeCompare(b.path));
  return candidates[0]?.path;
}

export interface InterpreterSources {
  /** `wireview.pythonPath`, resolved against the folder. */
  setting?: string;
  /** What the Python extension has selected for the folder. */
  pythonExtension?: string;
  manageDir: string;
  folder: string;
  platform: NodeJS.Platform;
}

/** The interpreters to try, in order: the first that exists is the one. The last is a bare command name. */
export function interpreterCandidates(sources: InterpreterSources): string[] {
  const windows = sources.platform === "win32";
  const venv = (directory: string) =>
    windows ? nodePath.win32.join(directory, ".venv", "Scripts", "python.exe") : nodePath.posix.join(directory, ".venv", "bin", "python");
  const candidates: string[] = [];
  if (sources.setting) return [sources.setting];
  if (sources.pythonExtension) candidates.push(sources.pythonExtension);
  candidates.push(venv(sources.manageDir));
  if (sources.folder !== sources.manageDir) candidates.push(venv(sources.folder));
  candidates.push(windows ? "python" : "python3");
  return candidates;
}

export interface CommandLine {
  command: string;
  args: string[];
  cwd: string;
}

/** The command that writes the metadata to `output`, run in manage.py's directory. */
export function buildCommand(options: { metadataCommand?: string[]; python: string; managePy: string; output: string }): CommandLine {
  const cwd = nodePath.dirname(options.managePy);
  if (options.metadataCommand?.length) {
    const [command, ...args] = options.metadataCommand;
    return { command, args: [...args, "--output", options.output], cwd };
  }
  return { command: options.python, args: [nodePath.basename(options.managePy), "wireview_lsp", "--output", options.output], cwd };
}

export type Failure = "no-command" | "failed";

/** Why a run failed: the project has no `wireview_lsp` (not a django-wireview project, or too old), or anything else. */
export function classifyFailure(stderr: string): Failure {
  return /Unknown command:\s*'?wireview_lsp'?/.test(stderr) ? "no-command" : "failed";
}

export interface Timers {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

/**
 * Runs the refresh one at a time. A request while it runs is not dropped and not
 * queued twice: the run that is going on is followed by exactly one more, so the
 * last save is always reflected and a burst of saves costs two runs.
 */
export class Refresher {
  private running: Promise<void> | null = null;
  private again = false;
  private disposed = false;
  private timer: unknown = null;
  private readonly run: () => Promise<void>;
  private readonly delay: number;
  private readonly timers: Timers;

  constructor(run: () => Promise<void>, delay: number, timers: Timers = globalThis as unknown as Timers) {
    this.run = run;
    this.delay = delay;
    this.timers = timers;
  }

  get busy(): boolean {
    return this.running !== null;
  }

  /** After a quiet moment: saves come in bursts. */
  schedule(): void {
    if (this.disposed) return;
    if (this.timer !== null) this.timers.clearTimeout(this.timer);
    this.timer = this.timers.setTimeout(() => {
      this.timer = null;
      void this.now();
    }, this.delay);
  }

  /** Now, or right after the run that is going on. Resolves when the metadata reflects this request. */
  now(): Promise<void> {
    if (this.disposed) return this.running ?? Promise.resolve();
    if (this.running) {
      this.again = true;
      return this.running;
    }
    this.running = (async () => {
      try {
        do {
          this.again = false;
          try {
            await this.run();
          } catch {
            // The run reports its own failure; the next request still runs
          }
        } while (this.again && !this.disposed);
      } finally {
        this.running = null;
      }
    })();
    return this.running;
  }

  /** No run starts after this: not the one asked for during a run, not a scheduled one. */
  dispose(): void {
    this.disposed = true;
    this.again = false;
    if (this.timer !== null) this.timers.clearTimeout(this.timer);
    this.timer = null;
  }
}

/**
 * Which run's results still count. Changing where the metadata comes from (another
 * file, another command) starts a new generation, and so does the project's end;
 * a run holds the signal of the generation it started in, hands it to the process
 * it spawns, and drops what it finds once the signal is aborted. One place decides
 * that an old run's answer is not this project's, whatever the run was doing.
 */
export class Generations {
  private controller = new AbortController();
  private ended = false;

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  /** The runs so far are stale: their processes are stopped and their results dropped. */
  next(): AbortSignal {
    this.controller.abort();
    if (!this.ended) this.controller = new AbortController();
    return this.controller.signal;
  }

  /** For good: every signal after this is aborted. */
  end(): void {
    this.ended = true;
    this.controller.abort();
  }
}
