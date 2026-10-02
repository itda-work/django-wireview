// One project per workspace folder: where its metadata comes from, running
// `manage.py wireview_lsp` again when Python changes, and keeping the last
// metadata that worked when a run fails.
//
// Nothing runs and no metadata file is read until the workspace is trusted: the
// command is the project's code, and the file's paths are where "go to
// definition" goes.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync } from "node:fs";
import * as nodePath from "node:path";

import * as vscode from "vscode";

import { checkVersion, METADATA_MAJOR, METADATA_MINOR } from "./core/metadata.ts";
import type { Metadata } from "./core/metadata.ts";
import { Project } from "./core/project.ts";
import { buildCommand, classifyFailure, Generations, interpreterCandidates, pickManagePy, Refresher } from "./core/runner.ts";

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
  private readonly output: vscode.OutputChannel;
  private readonly changed: () => void;
  /** What watches the current source: replaced when the source changes. */
  private watchers: vscode.Disposable[] = [];

  constructor(folder: vscode.WorkspaceFolder, storage: string, output: vscode.OutputChannel, changed: () => void) {
    this.folder = folder;
    this.output = output;
    this.changed = changed;
    const key = createHash("sha256").update(folder.uri.toString()).digest("hex").slice(0, 16);
    this.storage = nodePath.join(storage, `metadata-${key}.json`);
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
    const next = `${this.storage}.next`;
    mkdirSync(nodePath.dirname(this.storage), { recursive: true });
    // A file a run left is not this run's output
    rmSync(next, { force: true });
    const line = buildCommand({ metadataCommand: this.config().get<string[]>("metadataCommand", []), python, managePy, output: next });
    this.state = "running";
    this.changed();
    this.log(`${line.command} ${line.args.join(" ")}  (in ${line.cwd})`);
    const result = await new Promise<{ code: number; stderr: string }>((done) => {
      // The signal stops the process when its generation ends
      execFile(line.command, line.args, { cwd: line.cwd, timeout: 120_000, maxBuffer: 64 * 1024 * 1024, signal }, (error, _stdout, stderr) => {
        const code = error ? (typeof error.code === "number" ? error.code : 1) : 0;
        done({ code, stderr: stderr || (error && !stderr ? error.message : "") });
      });
    });
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
      this.fail(`manage.py wireview_lsp failed (exit ${result.code}). ${this.project ? "Keeping the last metadata." : ""}`.trim());
      return;
    }
    if (this.load(next, "manage.py wireview_lsp", signal)) renameSync(next, this.storage);
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
