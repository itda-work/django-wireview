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

/** One way to stop a run: a signal to its process group, or a command that ends the tree. */
export type StopStep = { kind: "group"; pid: number; signal: "SIGTERM" | "SIGKILL" } | { kind: "command"; command: string; args: string[] };

/**
 * Whether a run's process is spawned as the leader of a group of its own. A
 * wrapper such as `uv run` starts the Django process as its child: a signal to
 * the wrapper alone leaves that one running, a signal to the group reaches both.
 * Windows has no groups to signal; taskkill walks the tree instead.
 */
export function ownsGroup(platform: NodeJS.Platform): boolean {
  return platform !== "win32";
}

/**
 * The group's leader on POSIX: a shell that runs the command in the foreground,
 * with its stdin at /dev/null, and hands on its exit status, while the
 * command's stderr is the run's. A SIGTERM does not end the shell, which has a
 * trap for it: the trap runs once the command is over and leaves the shell
 * waiting in `read` on its own stdin, a pipe the editor holds open and never
 * writes, with no output held. So the leader is there until the stop's SIGKILL:
 * the wrapper the user named may die on SIGTERM and leave a process in the
 * group that ignores it.
 *
 * Only builtins keep the leader: `exec sleep` would look the name up on the
 * user's PATH, and a PATH without it, or with another sleep first, would let
 * the leader go before the SIGKILL. If the editor goes away while the trap waits
 * in `read`, the pipe closes and the shell exits; a command that is still
 * running keeps the shell waiting for it. The command runs in the foreground because a background one
 * starts with SIGINT and SIGQUIT ignored, which no trap in the shell undoes. The
 * trap is a handler, not "", which the command would inherit as SIGTERM ignored.
 */
export const SUPERVISOR = [`trap 'exec >/dev/null 2>&1; read -r _; exit 1' TERM`, '"$@" </dev/null', "exit $?"].join("\n");

/** The command line that is spawned: on POSIX the command under SUPERVISOR, passed as arguments so that no shell reads them. */
export function supervised(line: CommandLine, platform: NodeJS.Platform): CommandLine {
  if (!ownsGroup(platform)) return line;
  return { command: "/bin/sh", args: ["-c", SUPERVISOR, "wireview-run", line.command, ...line.args], cwd: line.cwd };
}

/**
 * How a run is stopped, in order, `grace` ms apart: everything it started asked
 * to end, then made to. On Windows a console process cannot be asked, so the one
 * step forces the tree.
 */
export function stopSteps(pid: number, platform: NodeJS.Platform): StopStep[] {
  if (!ownsGroup(platform)) return [{ kind: "command", command: "taskkill", args: ["/T", "/F", "/PID", String(pid)] }];
  return [
    { kind: "group", pid, signal: "SIGTERM" },
    { kind: "group", pid, signal: "SIGKILL" },
  ];
}

/**
 * What a stopper is handed: how to take a step, whether the exit of the run's
 * own process (the group's leader, the root of the tree) has not been reported
 * yet, and how to let go of what the run still holds once the steps are over.
 */
export interface StopperHooks {
  /** Take a step; false when there was nothing left to take it to. */
  take(step: StopStep): boolean;
  /** Whether the leader's exit has not been reported. */
  unreported(): boolean;
  /** The steps are over: let go of the run's output, whoever still holds it. */
  release(): void;
}

/**
 * Takes a run through its stop steps once, `grace` ms apart, and releases it
 * `grace` ms after the last.
 *
 * A step is taken only while Node has not reported the leader's exit. That is
 * what Node knows, not what the system knows, but on POSIX it is close: a pid
 * is not handed out again before its process is reaped, and libuv reaps its
 * children when it handles SIGCHLD and reports each exit in that same pass, not
 * from a timer. A step taken from a timer while the exit is unreported goes to
 * the leader's group. A stop set off synchronously by another child's exit
 * report can fall between this one's reaping and its report: that window stays
 * open. On Windows the step starts taskkill, which looks the pid up a moment
 * later: the check is not atomic with it there.
 *
 * Once the exit is reported nothing more is sent, even to a process the leader
 * left in its group; on POSIX the leader is SUPERVISOR, which stays until the
 * SIGKILL, so that happens when its command finished before the stop reached it
 * (or the editor closed the supervisor's stdin).
 */
export class Stopper {
  private started = false;
  private timer: unknown = null;
  private readonly steps: StopStep[];
  private readonly hooks: StopperHooks;
  private readonly grace: number;
  private readonly timers: Timers;

  constructor(steps: StopStep[], hooks: StopperHooks, grace: number, timers: Timers = globalThis as unknown as Timers) {
    this.steps = steps;
    this.hooks = hooks;
    this.grace = grace;
    this.timers = timers;
  }

  /** Begin; a second call does nothing. */
  stop(): void {
    if (this.started) return;
    this.started = true;
    this.step(0);
  }

  /** The leader is gone and the output closed: nothing more is taken or released. */
  cancel(): void {
    this.started = true;
    if (this.timer !== null) this.timers.clearTimeout(this.timer);
    this.timer = null;
  }

  private step(index: number): void {
    const more = index < this.steps.length && this.hooks.unreported() && this.hooks.take(this.steps[index]);
    const last = !more || index + 1 >= this.steps.length;
    // Not unref'd: what started the stop sees it through, or a supervisor is left waiting for it
    this.timer = this.timers.setTimeout(
      () => {
        this.timer = null;
        if (last) this.hooks.release();
        else this.step(index + 1);
      },
      this.grace,
    );
  }
}
