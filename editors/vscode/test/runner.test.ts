import { strict as assert } from "node:assert";
import test from "node:test";

import { checkVersion } from "../src/core/metadata.ts";
import { buildCommand, classifyFailure, interpreterCandidates, pickManagePy, Refresher } from "../src/core/runner.ts";

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
