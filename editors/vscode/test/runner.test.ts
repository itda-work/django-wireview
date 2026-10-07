import { strict as assert } from "node:assert";
import test from "node:test";

import { checkVersion } from "../src/core/metadata.ts";
import { buildCommand, classifyFailure, interpreterCandidates, ownsGroup, pickManagePy, Refresher, Stopper, stopSteps, SUPERVISOR, supervised } from "../src/core/runner.ts";
import type { StopStep } from "../src/core/runner.ts";

test("manage.py: the shallowest, never one in a virtualenv or node_modules", () => {
  const found = ["/w/.venv/lib/site-packages/x/manage.py", "/w/src/app/manage.py", "/w/tests/manage.py", "/w/node_modules/a/manage.py"];
  assert.equal(pickManagePy(found, "/w"), "/w/tests/manage.py");
  assert.equal(pickManagePy(["/w/venv/manage.py"], "/w"), undefined);
  assert.equal(pickManagePy([], "/w"), undefined);
});

test("the interpreter: the setting alone, else the Python extension's, the .venvs, then python3", () => {
  assert.deepEqual(interpreterCandidates({ setting: "/py", pythonExtension: "/ext", manageDir: "/w/tests", folder: "/w", platform: "linux" }), ["/py"]);
  assert.deepEqual(interpreterCandidates({ pythonExtension: "/ext", manageDir: "/w/tests", folder: "/w", platform: "darwin" }), [
    "/ext",
    "/w/tests/.venv/bin/python",
    "/w/.venv/bin/python",
    "python3",
  ]);
  assert.deepEqual(interpreterCandidates({ manageDir: "C:\\w", folder: "C:\\w", platform: "win32" }), ["C:\\w\\.venv\\Scripts\\python.exe", "python"]);
});

test("the command runs in manage.py's directory and writes to the file it is given", () => {
  assert.deepEqual(buildCommand({ python: "/py", managePy: "/w/tests/manage.py", output: "/s/m.json" }), {
    command: "/py",
    args: ["manage.py", "wireview_lsp", "--output", "/s/m.json"],
    cwd: "/w/tests",
  });
  assert.deepEqual(buildCommand({ metadataCommand: ["uv", "run", "python", "manage.py", "wireview_lsp"], python: "/py", managePy: "/w/manage.py", output: "/o" }), {
    command: "uv",
    args: ["run", "python", "manage.py", "wireview_lsp", "--output", "/o"],
    cwd: "/w",
  });
});

test("a project without the command is told apart from a failure", () => {
  assert.equal(classifyFailure("Unknown command: 'wireview_lsp'\nType 'manage.py help' for usage."), "no-command");
  assert.equal(classifyFailure("SyntaxError: invalid syntax"), "failed");
});

test("a run is stopped as a group: asked, then made to; Windows ends the tree at once", () => {
  assert.equal(ownsGroup("linux"), true);
  assert.equal(ownsGroup("darwin"), true);
  assert.equal(ownsGroup("win32"), false);
  assert.deepEqual(stopSteps(42, "darwin"), [
    { kind: "group", pid: 42, signal: "SIGTERM" },
    { kind: "group", pid: 42, signal: "SIGKILL" },
  ]);
  assert.deepEqual(stopSteps(42, "win32"), [{ kind: "command", command: "taskkill", args: ["/T", "/F", "/PID", "42"] }]);
});

test("on POSIX the command runs under the supervisor, as arguments no shell reads; Windows runs it as it is", () => {
  const line = { command: "uv", args: ["run", "a b", "$HOME"], cwd: "/w" };
  assert.deepEqual(supervised(line, "linux"), {
    command: "/bin/sh",
    args: ["-c", SUPERVISOR, "wireview-run", "uv", "run", "a b", "$HOME"],
    cwd: "/w",
  });
  assert.deepEqual(supervised(line, "win32"), line);
  // A trap of "" would be inherited by the command as SIGTERM ignored
  assert.match(SUPERVISOR, /trap '.+' TERM/);
  // Nothing looked up on the user's PATH keeps the leader, and the command is not a background job
  assert.doesNotMatch(SUPERVISOR, /sleep/);
  assert.doesNotMatch(SUPERVISOR, /&\s*$/m);
});

/** A stopper on fake timers, over a leader whose state the test sets. */
function stopping(options: { steps?: StopStep[]; gone?: boolean } = {}) {
  const timers: { callback: () => void; ms: number; cleared: boolean }[] = [];
  const taken: string[] = [];
  const leader = { unreported: true, released: 0 };
  const stopper = new Stopper(
    options.steps ?? stopSteps(7, "linux"),
    {
      take: (step) => {
        taken.push(step.kind === "group" ? step.signal : step.command);
        return !options.gone;
      },
      unreported: () => leader.unreported,
      release: () => {
        leader.released += 1;
      },
    },
    300,
    {
      setTimeout: (callback, ms) => {
        timers.push({ callback, ms, cleared: false });
        return timers.length - 1;
      },
      clearTimeout: (handle) => {
        timers[handle as number].cleared = true;
      },
    },
  );
  /** Let the next timer fire. */
  const tick = () => {
    const timer = timers.at(-1)!;
    assert.equal(timer.ms, 300);
    assert.ok(!timer.cleared);
    timer.callback();
  };
  return { stopper, timers, taken, leader, tick };
}

test("the stopper asks, then makes, then lets go, a grace apart; once however often it is asked", () => {
  const { stopper, timers, taken, leader, tick } = stopping();
  stopper.stop();
  stopper.stop();
  assert.deepEqual(taken, ["SIGTERM"]);
  tick();
  assert.deepEqual(taken, ["SIGTERM", "SIGKILL"]);
  assert.equal(leader.released, 0, "the run may still end on its own");
  tick();
  assert.equal(leader.released, 1);
  assert.equal(timers.length, 2, "nothing after letting go");
});

test("no signal once the leader's exit is reported: its number, and its group's, may be someone else's", () => {
  // Reported before the stop: nothing is sent at all
  const before = stopping();
  before.leader.unreported = false;
  before.stopper.stop();
  assert.deepEqual(before.taken, []);
  before.tick();
  assert.equal(before.leader.released, 1, "the run is still let go");

  // Reported after the SIGTERM: the SIGKILL is not sent to the number
  const between = stopping();
  between.stopper.stop();
  between.leader.unreported = false;
  between.tick();
  assert.deepEqual(between.taken, ["SIGTERM"]);
  between.tick();
  assert.equal(between.leader.released, 1);
});

test("a group already gone takes no further step; Windows's one step is followed by letting go", () => {
  const gone = stopping({ gone: true });
  gone.stopper.stop();
  assert.deepEqual(gone.taken, ["SIGTERM"]);
  gone.tick();
  assert.deepEqual(gone.taken, ["SIGTERM"]);
  assert.equal(gone.leader.released, 1);

  const windows = stopping({ steps: stopSteps(7, "win32") });
  windows.stopper.stop();
  assert.deepEqual(windows.taken, ["taskkill"]);
  windows.tick();
  assert.equal(windows.leader.released, 1);
});

test("a run that ends while it is stopped cancels what was still to come", () => {
  const { stopper, timers, taken, leader } = stopping();
  stopper.stop();
  stopper.cancel();
  assert.ok(timers[0].cleared);
  assert.deepEqual(taken, ["SIGTERM"]);
  assert.equal(leader.released, 0);
  stopper.stop();
  assert.equal(timers.length, 1, "and a stop after it does nothing");
});

test("versions: the same major with at least the minor this reads", () => {
  assert.deepEqual(checkVersion({ version: "1.1" }), { ok: true });
  assert.deepEqual(checkVersion({ version: "1.7" }), { ok: true });
  assert.deepEqual(checkVersion({ version: "1.0" }), { ok: false, reason: "older", version: "1.0" });
  assert.deepEqual(checkVersion({ version: "2.0" }), { ok: false, reason: "newer", version: "2.0" });
  assert.equal(checkVersion({}).ok, false);
  assert.equal(checkVersion(null).ok, false);
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => (resolve = done));
  return { promise, resolve };
}

test("one run at a time; requests during a run cost exactly one more", async () => {
  const runs: ReturnType<typeof deferred>[] = [];
  const refresher = new Refresher(() => {
    const run = deferred();
    runs.push(run);
    return run.promise;
  }, 10);
  const first = refresher.now();
  assert.equal(runs.length, 1);
  const second = refresher.now();
  const third = refresher.now();
  assert.equal(runs.length, 1, "nothing starts while one runs");
  runs[0].resolve();
  await new Promise((done) => setImmediate(done));
  assert.equal(runs.length, 2, "one more for both requests");
  runs[1].resolve();
  await Promise.all([first, second, third]);
  assert.equal(runs.length, 2);
  assert.equal(refresher.busy, false);
});

test("a failed run does not stop the next", async () => {
  let calls = 0;
  const refresher = new Refresher(async () => {
    calls += 1;
    throw new Error("boom");
  }, 10);
  await refresher.now();
  await refresher.now();
  assert.equal(calls, 2);
});

test("scheduled requests wait for a quiet moment and run once", () => {
  const timers: { callback: () => void; cleared: boolean }[] = [];
  let calls = 0;
  const refresher = new Refresher(
    async () => {
      calls += 1;
    },
    500,
    {
      setTimeout: (callback) => {
        timers.push({ callback, cleared: false });
        return timers.length - 1;
      },
      clearTimeout: (handle) => {
        timers[handle as number].cleared = true;
      },
    },
  );
  refresher.schedule();
  refresher.schedule();
  refresher.schedule();
  assert.deepEqual(timers.map((timer) => timer.cleared), [true, true, false]);
  timers[2].callback();
  assert.equal(calls, 1);
});

test("after dispose: no run follows, not the one asked for during a run", async () => {
  let runs = 0;
  let release = () => {};
  const refresher = new Refresher(async () => {
    runs += 1;
    if (runs === 1) await new Promise<void>((done) => (release = done));
  }, 0);
  const running = refresher.now();
  void refresher.now(); // one more would follow
  refresher.schedule();
  refresher.dispose();
  release();
  await running;
  await refresher.now();
  await new Promise((done) => setTimeout(done, 20));
  assert.equal(runs, 1);
});
