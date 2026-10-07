// One project per workspace folder: where its metadata comes from, running
// `manage.py wireview_lsp` again when Python changes, and keeping the last
// metadata that worked when a run fails.
//
// Nothing runs and no metadata file is read until the workspace is trusted: the
// command is the project's code, and the file's paths are where "go to
// definition" goes.
import { execFile, spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync } from "node:fs";
import * as nodePath from "node:path";

import * as vscode from "vscode";

import { checkVersion, METADATA_MAJOR, METADATA_MINOR } from "./core/metadata.ts";
import type { Metadata } from "./core/metadata.ts";
import { Project } from "./core/project.ts";
import { buildCommand, classifyFailure, Generations, interpreterCandidates, ownsGroup, pickManagePy, Refresher, Stopper, stopSteps, supervised } from "./core/runner.ts";
import type { CommandLine, StopStep, Timers } from "./core/runner.ts";

export type State = "idle" | "running" | "ok" | "failed" | "off" | "restricted";

/** The settings that say where the metadata comes from: a change starts over. */
export const SOURCE_SETTINGS = ["metadataPath", "managePy", "pythonPath", "metadataCommand"] as const;

export function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

/** How long a run may take before it is stopped. */
const RUN_TIMEOUT = 120_000;
/** How long a stopped run's processes have to end before they are killed. */
const STOP_GRACE = 2_000;
/** A file in the runs folder older than this is no run's any more: by then a run stopped at its timeout has had its group killed. */
const RUN_LIFETIME = RUN_TIMEOUT + 3 * STOP_GRACE;
/** The most of a run's stderr kept for the output channel. */
const STDERR_LIMIT = 1024 * 1024;

/** How long a run may take, and how long its processes have to end once it is stopped. */
export interface Timing {
  run: number;
  grace: number;
  /** What times a run out: the tests start the timeout themselves. */
  clock?: Timers;
}

/** Take one step in stopping a run's processes; false when the group is gone. */
function take(step: StopStep): boolean {
  try {
    if (step.kind === "group") process.kill(-step.pid, step.signal);
    else execFile(step.command, step.args, { windowsHide: true }, () => {});
    return true;
  } catch {
    // Nothing left in the group
    return false;
  }
}

/** When a file was last written, or 0 when it is not there. */
function modified(path: string): number {
  try {
    return statSync(path).mtimeMs;
  } catch {
    return 0;
  }
}

/** Remove a file if it is there. */
function remove(path: string): void {
  try {
    unlinkSync(path);
  } catch {
    // Not there, or already gone
  }
}

export class FolderProject implements vscode.Disposable {
  readonly folder: vscode.WorkspaceFolder;
  project: Project | undefined;
  state: State = "idle";
  /** Why the state is what it is, for the status bar's tooltip. */
  detail = "";
  private names: string[] | undefined;
  private readonly refresher: Refresher;
  private readonly generations = new Generations();
  private readonly storage: string;
  /** Where each run writes: a file of its own, so that no other run's output is taken for it. */
  private readonly outputs: string;
  /** The output files of the runs going on now: anything else in `outputs` is left over. */
  private readonly writing = new Set<string>();
  /** The output files of this project's runs that are over, and when: a stopped process may still write them. */
  private readonly finished = new Map<string, number>();
  private readonly output: vscode.OutputChannel;
  private readonly changed: () => void;
  private readonly timing: Timing;
  /** What watches the current source: replaced when the source changes. */
  private watchers: vscode.Disposable[] = [];

  constructor(
    folder: vscode.WorkspaceFolder,
    storage: string,
    output: vscode.OutputChannel,
    changed: () => void,
    timing: Timing = { run: RUN_TIMEOUT, grace: STOP_GRACE },
  ) {
    this.folder = folder;
    this.timing = timing;
    this.output = output;
    this.changed = changed;
    const key = createHash("sha256").update(folder.uri.toString()).digest("hex").slice(0, 16);
    this.storage = nodePath.join(storage, `metadata-${key}.json`);
    this.outputs = nodePath.join(storage, `metadata-${key}.runs`);
    this.refresher = new Refresher(() => this.run(), 1500);
  }

  private config(): vscode.WorkspaceConfiguration {
    return vscode.workspace.getConfiguration("wireview", this.folder.uri);
  }

  private resolve(path: string): string {
    return nodePath.isAbsolute(path) ? path : nodePath.join(this.folder.uri.fsPath, path);
  }

  private log(line: string): void {
    this.output.appendLine(`[${this.folder.name}] ${line}`);
  }

  start(): Promise<void> {
    return this.configure();
  }

  /**
   * Read the settings that say where the metadata comes from and start from them.
   * Called at the start, when one of them changes, and when the workspace is trusted:
   * what the old source was doing, a run or a watcher, no longer counts.
   */
  async configure(): Promise<void> {
    const signal = this.generations.next();
    for (const watcher of this.watchers) watcher.dispose();
    this.watchers = [];
    if (signal.aborted) return;
    if (!vscode.workspace.isTrusted) {
      this.state = "restricted";
      this.detail = "Restricted Mode: trust the workspace to read the project's metadata.";
      this.changed();
      return;
    }
    const fixed = this.config().get<string>("metadataPath", "");
    if (fixed) {
      // Something else writes it: read it, and again whenever it changes
      const path = this.resolve(fixed);
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(nodePath.dirname(path), nodePath.basename(path)));
      watcher.onDidChange(() => this.load(path, "the metadata file", signal));
      watcher.onDidCreate(() => this.load(path, "the metadata file", signal));
      this.watchers.push(watcher);
      this.load(path, "the metadata file", signal);
      return;
    }
    // What the last session left: the editor works while the first run goes on
    if (isFile(this.storage)) this.load(this.storage, "the last run", signal);
    await this.refresher.now();
  }

  /**
   * Read metadata from a file; keep what there was when it cannot be read. The one
   * place a metadata file is read: nothing once the signal is aborted, nothing before
   * the workspace is trusted.
   */
  private load(path: string, from: string, signal: AbortSignal): boolean {
    if (signal.aborted || !vscode.workspace.isTrusted) return false;
    let metadata: unknown;
    try {
      metadata = JSON.parse(readFileSync(path, "utf8"));
    } catch (error) {
      this.fail(`Could not read ${from} (${path}): ${(error as Error).message}`);
      return false;
    }
    const version = checkVersion(metadata);
    if (!version.ok) {
      const wanted = `${METADATA_MAJOR}.${METADATA_MINOR}`;
      const advice =
        version.reason === "newer"
          ? "Update the Django Wireview extension."
          : version.reason === "older"
            ? "Upgrade django-wireview in the project."
            : "Is it the output of manage.py wireview_lsp?";
      this.fail(`The metadata is version ${version.version}; this extension reads ${wanted}. ${advice}`);
      return false;
    }
    this.project = new Project(metadata as Metadata);
    this.names = undefined;
    this.state = "ok";
    this.detail = `${this.project.components.length} components, from ${from}`;
    this.changed();
    return true;
  }

  private fail(message: string): void {
    this.log(message);
    this.state = "failed";
    this.detail = message;
    this.changed();
  }

  /** Run now (or right after the run that is going on). */
  refresh(): Promise<void> {
    const fixed = this.config().get<string>("metadataPath", "");
    if (fixed) {
      this.load(this.resolve(fixed), "the metadata file", this.generations.signal);
      return Promise.resolve();
    }
    return this.refresher.now();
  }

  /** A Python file was saved. */
  pythonSaved(): void {
    if (this.config().get<string>("metadataPath", "") || !this.config().get("refreshOnSave", true)) return;
    if (this.state === "off") return;
    this.refresher.schedule();
  }

  private async managePy(): Promise<string | undefined> {
    const setting = this.config().get<string>("managePy", "");
    if (setting) return this.resolve(setting);
    const found = await vscode.workspace.findFiles(
      new vscode.RelativePattern(this.folder, "**/manage.py"),
      "{**/node_modules/**,**/.venv/**,**/venv/**,**/site-packages/**}",
      50,
    );
    return pickManagePy(
      found.map((uri) => uri.fsPath),
      this.folder.uri.fsPath,
    );
  }

  private async python(manageDir: string): Promise<string> {
    const setting = this.config().get<string>("pythonPath", "");
    let fromExtension: string | undefined;
    if (!setting) {
      try {
        const extension = vscode.extensions.getExtension("ms-python.python");
        if (extension) {
          const api = (await extension.activate()) as {
            environments?: { getActiveEnvironmentPath(resource?: vscode.Uri): { path: string } };
          };
          fromExtension = api.environments?.getActiveEnvironmentPath(this.folder.uri).path;
        }
      } catch {
        // The Python extension is optional
      }
    }
    const candidates = interpreterCandidates({
      setting: setting ? this.resolve(setting) : undefined,
      pythonExtension: fromExtension,
      manageDir,
      folder: this.folder.uri.fsPath,
      platform: process.platform,
    });
    return candidates.find((candidate, index) => index === candidates.length - 1 || isFile(candidate)) ?? candidates[candidates.length - 1];
  }

  /** The one place a process is spawned: never before the workspace is trusted. */
  private async run(): Promise<void> {
    // The run belongs to the generation it starts in: a source changed or a project
    // gone while it awaits makes everything it finds stale
    const signal = this.generations.signal;
    if (signal.aborted || !vscode.workspace.isTrusted) return;
    // A metadata file took over since this run was asked for
    if (this.config().get<string>("metadataPath", "")) return;
    const managePy = await this.managePy();
    if (signal.aborted) return;
    if (!managePy || !isFile(managePy)) {
      this.state = "off";
      this.detail = "No manage.py in this folder.";
      this.changed();
      return;
    }
    const python = await this.python(nodePath.dirname(managePy));
    if (signal.aborted) return;
    // A run stopped before can still write after it was let go: its file, not this one
    mkdirSync(this.outputs, { recursive: true });
    this.sweep();
    const next = nodePath.join(this.outputs, `${randomUUID()}.json`);
    this.writing.add(next);
    try {
      await this.spawn(next, python, managePy, signal);
    } finally {
      this.writing.delete(next);
      remove(next);
      this.finished.set(next, Date.now());
    }
  }

  /**
   * Remove what the runs that are over left in `outputs`: a stopped process may have written there since.
   * Another file may be the output of a run this project does not know (another window on the same
   * storage), so it goes only once it is older than a run can take.
   */
  private sweep(): void {
    let names: string[];
    try {
      names = readdirSync(this.outputs);
    } catch {
      return;
    }
    const now = Date.now();
    for (const name of names) {
      const path = nodePath.join(this.outputs, name);
      if (this.writing.has(path)) continue;
      if (this.finished.has(path) || now - modified(path) > RUN_LIFETIME) remove(path);
    }
    // A file written later than this is old enough by the next sweep's measure
    for (const [path, at] of this.finished) if (now - at > RUN_LIFETIME) this.finished.delete(path);
  }

  private async spawn(next: string, python: string, managePy: string, signal: AbortSignal): Promise<void> {
    const line = buildCommand({ metadataCommand: this.config().get<string[]>("metadataCommand", []), python, managePy, output: next });
    this.state = "running";
    this.changed();
    this.log(`${line.command} ${line.args.join(" ")}  (in ${line.cwd})`);
    const result = await this.execute(line, signal);
    if (signal.aborted) return;
    if (result.code !== 0 || !existsSync(next)) {
      if (classifyFailure(result.stderr) === "no-command") {
        // Not a django-wireview project, or one too old for this: nothing to do here
        this.log("manage.py has no wireview_lsp command: the project does not use django-wireview 1.0 or later.");
        this.state = "off";
        this.detail = "manage.py has no wireview_lsp command.";
        this.changed();
        return;
      }
      if (result.stderr) this.output.append(result.stderr.endsWith("\n") ? result.stderr : `${result.stderr}\n`);
      // The last metadata that worked stays
      const what = result.code === 0 ? "wrote no output file" : `failed (exit ${result.code})`;
      this.fail(`manage.py wireview_lsp ${what}. ${this.project ? "Keeping the last metadata." : ""}`.trim());
      return;
    }
    if (!this.load(next, "manage.py wireview_lsp", signal)) return;
    try {
      renameSync(next, this.storage);
    } catch (error) {
      // The metadata is in use; the next session starts from the older file and runs again
      this.log(`Could not keep the metadata in ${this.storage}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  /**
   * Run a command line to its end, or until it is stopped. On POSIX the process
   * leads a group of its own (SUPERVISOR, with the command under it), and the
   * signal or the timeout sends the group SIGTERM, then SIGKILL `grace` ms later:
   * a wrapper such as `uv run` need not pass a SIGTERM on, and the Django process
   * under it would run on. On Windows taskkill ends the tree.
   *
   * A stopped run is over at once: what was asked of it no longer counts, and the
   * stop goes on without holding the next run up. Its stderr is let go `grace` ms
   * after the last step, as a process outside the group may hold it open.
   */
  private execute(line: CommandLine, signal: AbortSignal): Promise<{ code: number; stderr: string }> {
    const clock = this.timing.clock ?? (globalThis as unknown as Timers);
    return new Promise((done) => {
      let stderr = "";
      let over = false;
      let stopper: Stopper | undefined;
      const finish = (code: number, more = "") => {
        if (over) return;
        over = true;
        clock.clearTimeout(timer);
        signal.removeEventListener("abort", aborted);
        if (more) stderr += `${stderr && !stderr.endsWith("\n") ? "\n" : ""}${more}`;
        done({ code, stderr });
      };
      const stop = (why: string) => {
        stopper?.stop();
        finish(1, why);
      };
      const aborted = () => stop("Stopped.");
      const timer = clock.setTimeout(() => stop(`Stopped after ${this.timing.run / 1000}s.`), this.timing.run);
      const spawned = supervised(line, process.platform);
      let child;
      try {
        child = spawn(spawned.command, spawned.args, {
          cwd: spawned.cwd,
          // The supervisor's stdin: held open, never written, closed when the stop is over
          stdio: [ownsGroup(process.platform) ? "pipe" : "ignore", "ignore", "pipe"],
          detached: ownsGroup(process.platform),
          windowsHide: true,
        });
      } catch (error) {
        finish(1, error instanceof Error ? error.message : String(error));
        return;
      }
      const leader = child;
      if (leader.pid !== undefined) {
        stopper = new Stopper(
          stopSteps(leader.pid, process.platform),
          {
            take,
            // Set when Node reports the exit, in the pass that reaps the process
            unreported: () => leader.exitCode === null && leader.signalCode === null,
            release: () => {
              leader.stderr?.destroy();
              leader.stdin?.destroy();
            },
          },
          this.timing.grace,
        );
      }
      // Never written: a supervisor that is gone must not make it an unhandled error
      leader.stdin?.on("error", () => {});
      leader.stderr?.setEncoding("utf8");
      leader.stderr?.on("data", (chunk: string) => {
        if (stderr.length < STDERR_LIMIT) stderr += chunk.slice(0, STDERR_LIMIT - stderr.length);
      });
      // Not spawned at all: no command by that name, or no directory
      leader.on("error", (error) => finish(1, stderr ? "" : error.message));
      leader.on("close", (code) => {
        stopper?.cancel();
        leader.stdin?.destroy();
        finish(code ?? 1);
      });
      if (signal.aborted) aborted();
      else signal.addEventListener("abort", aborted, { once: true });
    });
  }

  /** The template names under the project's template directories. */
  templateNames(): string[] {
    if (this.names) return this.names;
    const names = new Set<string>();
    for (const directory of this.project?.metadata.template_dirs ?? []) {
      const walk = (path: string, prefix: string, depth: number) => {
        if (depth > 12) return;
        let entries;
        try {
          entries = readdirSync(path, { withFileTypes: true });
        } catch {
          return;
        }
        for (const entry of entries) {
          if (entry.isDirectory()) walk(nodePath.join(path, entry.name), `${prefix}${entry.name}/`, depth + 1);
          else if (entry.isFile()) names.add(`${prefix}${entry.name}`);
        }
      };
      walk(directory, "", 0);
    }
    this.names = [...names].sort();
    return this.names;
  }

  /** A template was added or removed. */
  templatesChanged(): void {
    this.names = undefined;
  }

  dispose(): void {
    this.generations.end();
    this.refresher.dispose();
    for (const watcher of this.watchers) watcher.dispose();
    this.watchers = [];
  }
}
