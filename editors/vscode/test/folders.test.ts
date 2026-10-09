// The folder project's lifecycle: src/folders.ts itself, with the VS Code API
// replaced by test/stub/vscode.ts and real child processes for the command. What
// is checked is what a stale run must not do: start again, write, or win.
import { strict as assert } from "node:assert";
import { createHash } from "node:crypto";
import { appendFileSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, utimesSync, writeFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import * as nodePath from "node:path";
import test from "node:test";

registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "vscode") return { url: new URL("./stub/vscode.ts", import.meta.url).href, shortCircuit: true };
    return next(specifier, context);
  },
});
const { state } = await import("./stub/vscode.ts");
const { FolderProject } = await import("../src/folders.ts");
const { digestOf } = await import("../src/queryFiles.ts");
type Folder = InstanceType<typeof FolderProject>;

function metadata(name: string): string {
  return JSON.stringify({ version: "1.1", components: { [name]: { name, fqn: `app.${name}`, app_key: `app:${name}` } } });
}

/** A scratch folder with a manage.py, and a command that takes `delay` ms to write the metadata. */
function workspace() {
  const root = mkdtempSync(nodePath.join(tmpdir(), "wireview-folders-"));
  writeFileSync(nodePath.join(root, "manage.py"), "");
  const marker = nodePath.join(root, "runs.txt");
  writeFileSync(marker, "");
  const child = nodePath.join(root, "command.mjs");
  writeFileSync(
    child,
    [
      'import { appendFileSync, writeFileSync } from "node:fs";',
      `appendFileSync(${JSON.stringify(marker)}, "started\\n");`,
      "setTimeout(() => {",
      `  writeFileSync(process.argv.at(-1), ${JSON.stringify(metadata("Ran"))});`,
      `  appendFileSync(${JSON.stringify(marker)}, "completed\\n");`,
      "}, Number(process.argv[2]));",
    ].join("\n"),
  );
  // Ignores the SIGTERM that ends its run, and writes the metadata afterwards anyway
  const stubborn = nodePath.join(root, "stubborn.mjs");
  writeFileSync(
    stubborn,
    [
      'import { appendFileSync, writeFileSync } from "node:fs";',
      'process.on("SIGTERM", () => {});',
      `appendFileSync(${JSON.stringify(marker)}, "stubborn started\\n");`,
      "setTimeout(() => {",
      `  writeFileSync(process.argv.at(-1), ${JSON.stringify(metadata("Stale"))});`,
      `  appendFileSync(${JSON.stringify(marker)}, "stubborn wrote\\n");`,
      "}, Number(process.argv[2]));",
    ].join("\n"),
  );
  // Exits 0 without writing anything
  const silent = nodePath.join(root, "silent.mjs");
  writeFileSync(silent, "setTimeout(() => {}, Number(process.argv[2]));");
  const runs = () => readFileSync(marker, "utf8").split("\n").filter(Boolean);
  const managePy = nodePath.join(root, "manage.py");
  const command = (delay: number) => ({ managePy, metadataCommand: [process.execPath, child, String(delay)] });
  const stubbornCommand = (delay: number) => ({ managePy, metadataCommand: [process.execPath, stubborn, String(delay)] });
  const silentCommand = (delay: number) => ({ managePy, metadataCommand: [process.execPath, silent, String(delay)] });
  return { root, runs, command, stubbornCommand, silentCommand };
}

function project(root: string, timing?: ConstructorParameters<typeof FolderProject>[4]): { folder: Folder; changes: () => number; log: string[] } {
  let changes = 0;
  const log: string[] = [];
  const output = { appendLine: (line: string) => log.push(line), append: (text: string) => log.push(text) };
  const uri = { fsPath: root, toString: () => `file://${root}` };
  const folder = new FolderProject({ name: "w", uri, index: 0 } as never, nodePath.join(root, "storage"), output as never, () => {
    changes += 1;
  }, timing);
  return { folder, changes: () => changes, log };
}

async function until(check: () => boolean, what: string, timeout = 5000): Promise<void> {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((done) => setTimeout(done, 10));
  }
}

/** A run that must be over soon: one that waits on a process forever fails the test instead of hanging it. */
async function within(promise: Promise<unknown>, what: string, timeout = 3000): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, fail) => {
    timer = setTimeout(() => fail(new Error(`timed out waiting for ${what}`)), timeout);
  });
  try {
    await Promise.race([promise, late]);
  } finally {
    clearTimeout(timer);
  }
}

const pause = (ms: number) => new Promise((done) => setTimeout(done, ms));
const names = (folder: Folder) => folder.project?.components.map((component) => component.name);

test.beforeEach(() => {
  state.config = {};
  state.trusted = true;
  state.watchers = [];
});

test("a folder that goes away stops its run and starts none after it", async () => {
  const { root, runs, command } = workspace();
  state.config = command(300);
  const { folder, changes } = project(root);
  const started = folder.start();
  await until(() => folder.state === "running", "the run");
  void folder.refresh(); // asked for while it runs: one more would follow
  await until(() => runs().length === 1, "the process");
  folder.dispose();
  const seen = changes();
  await started;
  await pause(500);
  assert.deepEqual(runs(), ["started"], "the process was stopped, and the run asked for during it never started");
  assert.equal(folder.project, undefined, "its result is not this folder's");
  assert.equal(changes(), seen, "nothing is reported after the end");
});

test("another metadata file: the old one's watcher is let go and what it reads is dropped", async () => {
  const { root } = workspace();
  const a = nodePath.join(root, "a.json");
  const b = nodePath.join(root, "b.json");
  writeFileSync(a, metadata("A"));
  writeFileSync(b, metadata("B"));
  state.config = { metadataPath: a };
  const { folder } = project(root);
  await folder.start();
  assert.deepEqual(names(folder), ["A"]);
  const [old] = state.watchers;

  state.config = { metadataPath: b };
  await folder.configure();
  assert.deepEqual(names(folder), ["B"]);
  assert.ok(old.disposed, "the old file is no longer watched");
  assert.equal(state.watchers.at(-1)!.pattern.pattern, "b.json");
  old.fireAnyway(); // an event already on its way
  assert.deepEqual(names(folder), ["B"]);
  await folder.refresh();
  assert.deepEqual(names(folder), ["B"], "Refresh reads the file the setting names now");

  writeFileSync(b, metadata("B2"));
  state.watchers.at(-1)!.fire();
  assert.deepEqual(names(folder), ["B2"]);
  folder.dispose();
});

test("a run that a metadata file takes over from is stopped and dropped", async () => {
  const { root, runs, command } = workspace();
  const a = nodePath.join(root, "a.json");
  writeFileSync(a, metadata("A"));
  state.config = command(300);
  const { folder } = project(root);
  const started = folder.start();
  await until(() => runs().length === 1, "the process");

  state.config = { metadataPath: a };
  await folder.configure();
  await started;
  await pause(400);
  assert.deepEqual(names(folder), ["A"]);
  assert.deepEqual(runs(), ["started"], "the process was stopped");
  assert.equal(folder.state, "ok");
  folder.dispose();
});

test("what a stopped run writes late is not the next run's output, and does not stay on disk", async () => {
  const { root, runs, command, stubbornCommand, silentCommand } = workspace();
  state.config = stubbornCommand(200);
  const { folder } = project(root);
  const started = folder.start();
  await until(() => runs().includes("stubborn started"), "the stubborn process");

  // Another command, which writes nothing while the old process writes: the run fails
  state.config = silentCommand(600);
  await folder.configure();
  await started;
  assert.ok(runs().includes("stubborn wrote"), "the old process wrote while the new one ran");
  assert.equal(folder.state, "failed", "the late file is no run's output");
  assert.equal(folder.project, undefined);

  state.config = command(0);
  await folder.configure();
  assert.deepEqual(names(folder), ["Ran"]);
  folder.dispose();
  const storage = nodePath.join(root, "storage");
  const left = readdirSync(storage, { recursive: true }).map(String).filter((name) => name.endsWith(".json") || name.includes("next"));
  assert.equal(left.length, 1, `only the metadata that won stays: ${left.join(", ")}`);
});

/** Where the folder project of `root` keeps its metadata, and where its runs write. */
function stored(root: string): { metadata: string; runs: string } {
  const key = createHash("sha256").update(`file://${root}`).digest("hex").slice(0, 16);
  return { metadata: nodePath.join(root, "storage", `metadata-${key}.json`), runs: nodePath.join(root, "storage", `metadata-${key}.runs`) };
}

test("a run takes from the runs folder only what its own runs left, or what is older than a run can take", async () => {
  const { root, command } = workspace();
  // Writes the metadata first, and exits a while after
  const early = nodePath.join(root, "early.mjs");
  writeFileSync(
    early,
    `import { writeFileSync } from "node:fs";\nwriteFileSync(process.argv.at(-1), ${JSON.stringify(metadata("Early"))});\nsetTimeout(() => {}, 400);`,
  );
  state.config = { managePy: nodePath.join(root, "manage.py"), metadataCommand: [process.execPath, early] };
  const { folder: first } = project(root);
  const running = first.start();
  const written = () => {
    try {
      return readdirSync(stored(root).runs).length === 1;
    } catch {
      return false;
    }
  };
  await until(written, "the first run's file");

  // Another project on the same storage: the first one's file is not its leftover
  const { folder: second } = project(root);
  writeFileSync(nodePath.join(stored(root).runs, "recent.json"), "{}");
  const old = nodePath.join(stored(root).runs, "old.json");
  writeFileSync(old, "{}");
  const past = new Date(Date.now() - 10 * 60_000);
  utimesSync(old, past, past);
  state.config = command(0);
  await second.start();
  assert.equal(second.state, "ok");
  await running;
  assert.equal(first.state, "ok", first.detail);
  assert.deepEqual(readdirSync(stored(root).runs).sort(), ["recent.json"], "the old leftover went, the recent one may be a run's");
  first.dispose();
  second.dispose();
});

test("a command that exits 0 without writing says it wrote no output file", async () => {
  const { root, silentCommand } = workspace();
  state.config = silentCommand(0);
  const { folder } = project(root);
  await folder.start();
  assert.equal(folder.state, "failed");
  assert.match(folder.detail, /wrote no output file/);
  folder.dispose();
});

test("metadata that cannot be kept on disk is used, and the output channel says why", async () => {
  const { root, command } = workspace();
  // A directory with something in it where the metadata goes: the rename fails
  mkdirSync(nodePath.join(stored(root).metadata, "in-the-way"), { recursive: true });
  state.config = command(0);
  const { folder, log } = project(root);
  await folder.start();
  assert.equal(folder.state, "ok");
  assert.deepEqual(names(folder), ["Ran"]);
  assert.ok(
    log.some((line) => line.includes("Could not keep the metadata")),
    log.join("\n"),
  );
  folder.dispose();
});

test("from a metadata file to the command: the command runs, and the file is no longer watched", async () => {
  const { root, runs, command } = workspace();
  const a = nodePath.join(root, "a.json");
  writeFileSync(a, metadata("A"));
  state.config = { metadataPath: a };
  const { folder } = project(root);
  await folder.start();
  state.config = command(0);
  await folder.configure();
  assert.deepEqual(names(folder), ["Ran"]);
  assert.deepEqual(runs(), ["started", "completed"]);
  assert.ok(state.watchers[0].disposed);
  folder.dispose();
});

test("Restricted Mode: nothing runs and no file is read until the workspace is trusted", async () => {
  const { root, runs, command } = workspace();
  // A session before left its metadata in storage
  state.config = command(0);
  const before = project(root);
  await before.folder.start();
  assert.deepEqual(names(before.folder), ["Ran"]);
  before.folder.dispose();
  const ran = runs().length;

  state.trusted = false;
  const { folder } = project(root);
  await folder.start();
  await folder.refresh();
  folder.pythonSaved();
  await pause(300);
  assert.equal(folder.state, "restricted");
  assert.equal(folder.project, undefined, "the last session's metadata is not read either");
  assert.equal(runs().length, ran, "no process");

  const a = nodePath.join(root, "a.json");
  writeFileSync(a, metadata("A"));
  state.config = { metadataPath: a };
  await folder.configure();
  await folder.refresh();
  assert.equal(folder.project, undefined, "nor a metadata file the workspace names");
  assert.equal(state.watchers.filter((watcher) => !watcher.disposed).length, 0);

  // Trusted: onDidGrantWorkspaceTrust starts it over
  state.trusted = true;
  await folder.configure();
  assert.deepEqual(names(folder), ["A"]);
  state.config = command(0);
  await folder.configure();
  assert.deepEqual(names(folder), ["Ran"]);
  assert.equal(runs().length, ran + 2);
  folder.dispose();
});

/**
 * A wrapper and the process it starts, which ignores SIGTERM, writes its pid and
 * runs until killed; `held` says whether it keeps the wrapper's stderr open, as
 * one that logs does. The wrapper `dies` on SIGTERM, as one that does nothing
 * about signals does; or `waits` like `uv run`, letting the signal be the
 * process's to answer and exiting with it; or lets the process `escape` into a
 * group of its own, holding stderr, and dies on SIGTERM itself. The wrapper
 * writes its parent's pid (the supervisor's, on POSIX) and the PATH it was given.
 */
function wrapped(root: string, how: { held: boolean; wrapper: "dies" | "waits" | "escapes" }) {
  const pidFile = nodePath.join(root, "grandchild.pid");
  const parentFile = nodePath.join(root, "parent.pid");
  const pathFile = nodePath.join(root, "wrapper.path");
  const grandchild = nodePath.join(root, "grandchild.mjs");
  writeFileSync(
    grandchild,
    [
      'import { writeFileSync } from "node:fs";',
      'process.on("SIGTERM", () => {});',
      `writeFileSync(${JSON.stringify(pidFile)}, String(process.pid));`,
      "setInterval(() => {}, 1000);",
    ].join("\n"),
  );
  const wrapper = nodePath.join(root, "wrapper.mjs");
  const options = how.wrapper === "escapes" ? '{ stdio: ["ignore", "ignore", "inherit"], detached: true }' : `{ stdio: ${JSON.stringify(how.held ? "inherit" : "ignore")} }`;
  writeFileSync(
    wrapper,
    [
      'import { spawn } from "node:child_process";',
      'import { writeFileSync } from "node:fs";',
      `writeFileSync(${JSON.stringify(parentFile)}, String(process.ppid));`,
      `writeFileSync(${JSON.stringify(pathFile)}, process.env.PATH ?? "");`,
      `const child = spawn(process.execPath, [${JSON.stringify(grandchild)}], ${options});`,
      ...(how.wrapper === "waits" ? ['process.on("SIGTERM", () => {});', 'child.on("exit", (code) => process.exit(code ?? 1));'] : ["setInterval(() => {}, 1000);"]),
    ].join("\n"),
  );
  return {
    command: { managePy: nodePath.join(root, "manage.py"), metadataCommand: [process.execPath, wrapper] },
    pid: () => readPid(pidFile),
    supervisor: () => readPid(parentFile),
    path: () => readFileSync(pathFile, "utf8"),
  };
}

function readPid(path: string): number | undefined {
  try {
    return Number(readFileSync(path, "utf8")) || undefined;
  } catch {
    return undefined;
  }
}

/**
 * Whether the process is still running. A zombie is not: a grandchild whose
 * wrapper died is reparented to pid 1, and in a container whose pid 1 never
 * reaps (act runs `tail -f /dev/null`) it stays a zombie after the SIGKILL, which
 * `kill(pid, 0)` still finds.
 */
function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
  return !zombie(pid);
}

/** On Linux, from /proc: the state field follows the parenthesised command name, which may itself hold ")". */
function zombie(pid: number): boolean {
  if (process.platform !== "linux") return false;
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2, stat.lastIndexOf(")") + 3) === "Z";
  } catch {
    return false;
  }
}

/** The supervisor is gone; without one (Windows) the wrapper's parent is this process. */
function ended(supervisor: number | undefined): boolean {
  return supervisor === process.pid || !alive(supervisor!);
}

/** Kill what a test left running: the next tests must not inherit it. */
function reap(...pids: (number | undefined)[]): void {
  for (const pid of pids) if (pid !== undefined && pid !== process.pid && alive(pid)) process.kill(pid, "SIGKILL");
}

/** A run timeout that goes off when the test says, not when a slow machine has not started the process yet. */
function clock() {
  const pending: (() => void)[] = [];
  return {
    timers: {
      setTimeout: (callback: () => void) => pending.push(callback) - 1,
      clearTimeout: (handle: unknown) => {
        pending[handle as number] = () => {};
      },
    },
    fire: () => {
      for (const callback of pending.splice(0)) callback();
    },
  };
}

for (const wrapper of ["dies", "waits"] as const) {
  for (const held of [true, false]) {
    const how = `a wrapper that ${wrapper === "dies" ? "dies on SIGTERM" : "waits for it"} (${held ? "holding" : "not holding"} its stderr)`;
    for (const by of ["a folder that goes away", "a timeout"] as const) {
      test(`${by} stops the process under ${how}, and the supervisor`, async () => {
        const { root } = workspace();
        const { command, pid, supervisor } = wrapped(root, { held, wrapper });
        state.config = command;
        const timeout = clock();
        const { folder, log } = project(root, { run: 500, grace: 300, clock: timeout.timers });
        try {
          const started = folder.start();
          await until(() => pid() !== undefined && supervisor() !== undefined, "the process under the wrapper");
          if (by === "a timeout") timeout.fire();
          else folder.dispose();
          await within(started, "the run to be over");
          await until(() => !alive(pid()!), "the process under the wrapper to end", 3000);
          await until(() => ended(supervisor()), "the supervisor to end", 3000);
          if (by === "a timeout") {
            assert.equal(folder.state, "failed");
            assert.ok(
              log.some((line) => line.includes("Stopped after 0.5s")),
              log.join("\n"),
            );
          }
        } finally {
          folder.dispose();
          reap(pid(), supervisor());
        }
      });
    }
  }
}

test("a process that left the group and holds stderr does not keep the next command from running", async () => {
  const { root, runs, command } = workspace();
  const escaped = wrapped(root, { held: true, wrapper: "escapes" });
  state.config = escaped.command;
  const { folder } = project(root, { run: 60_000, grace: 300 });
  try {
    const started = folder.start();
    await until(() => escaped.pid() !== undefined, "the process that left the group");
    state.config = command(0);
    const next = folder.configure();
    await until(() => runs().includes("completed"), "the next command", 3000);
    await within(next, "the next run");
    await within(started, "the first run to be over");
    assert.deepEqual(names(folder), ["Ran"]);
    assert.equal(folder.state, "ok");
    await until(() => ended(escaped.supervisor()), "the supervisor to end", 3000);
  } finally {
    folder.dispose();
    reap(escaped.pid(), escaped.supervisor());
  }
});

/** A command that writes its parent's pid, checks the arguments it was given, writes to stderr and exits with `code`. */
function plain(root: string, code: number) {
  const parentFile = nodePath.join(root, "parent.pid");
  const script = nodePath.join(root, "plain.mjs");
  writeFileSync(
    script,
    [
      'import { writeFileSync } from "node:fs";',
      `writeFileSync(${JSON.stringify(parentFile)}, String(process.ppid));`,
      `const given = JSON.stringify(process.argv.slice(2, -2));`,
      `if (given !== ${JSON.stringify(JSON.stringify(["a b", "$HOME", "'q'", '"; exit 9'])) }) { process.stderr.write("arguments: " + given); process.exit(2); }`,
      'process.stderr.write("said on stderr\\n");',
      `if (${code} === 0) writeFileSync(process.argv.at(-1), ${JSON.stringify(metadata("Plain"))});`,
      `process.exit(${code});`,
    ].join("\n"),
  );
  return {
    command: { managePy: nodePath.join(root, "manage.py"), metadataCommand: [process.execPath, script, "a b", "$HOME", "'q'", '"; exit 9'] },
    supervisor: () => readPid(parentFile),
  };
}

test("a command that ends by itself: its arguments as given, its exit status and stderr, and no supervisor left", async () => {
  const { root } = workspace();
  const ok = plain(root, 0);
  state.config = ok.command;
  const { folder, log } = project(root);
  try {
    await folder.start();
    assert.equal(folder.state, "ok", folder.detail);
    assert.deepEqual(names(folder), ["Plain"]);
    await until(() => ended(ok.supervisor()), "the supervisor to end");

    const failing = plain(root, 3);
    state.config = failing.command;
    await folder.refresh();
    assert.equal(folder.state, "failed");
    assert.match(folder.detail, /failed \(exit 3\)/);
    assert.ok(
      log.some((line) => line.includes("said on stderr")),
      log.join("\n"),
    );
    await until(() => ended(failing.supervisor()), "the supervisor to end");
  } finally {
    folder.dispose();
    reap(ok.supervisor());
  }
});

for (const user of ["no sleep on it", "another sleep first on it"] as const) {
  // The supervisor and the fake sleep are POSIX shell scripts
  test(`with ${user}, the user's PATH, a stop still reaches what a wrapper that died on SIGTERM left`, { skip: process.platform === "win32" }, async () => {
    const { root } = workspace();
    const { command, pid, supervisor, path } = wrapped(root, { held: false, wrapper: "dies" });
    let PATH = "/nonexistent";
    if (user === "another sleep first on it") {
      // Ends at once, as a sleep the supervisor must not count on
      const bin = nodePath.join(root, "bin");
      mkdirSync(bin);
      writeFileSync(nodePath.join(bin, "sleep"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
      PATH = `${bin}${nodePath.delimiter}${process.env.PATH}`;
    }
    const before = process.env.PATH;
    process.env.PATH = PATH;
    state.config = command;
    const { folder } = project(root, { run: 60_000, grace: 300 });
    try {
      const started = folder.start();
      await until(() => pid() !== undefined && supervisor() !== undefined, "the process under the wrapper");
      assert.equal(path(), PATH, "the command gets the user's PATH as it is");
      folder.dispose();
      await within(started, "the run to be over");
      await until(() => !alive(pid()!), "the process under the wrapper to end", 3000);
      await until(() => ended(supervisor()), "the supervisor to end", 3000);
    } finally {
      process.env.PATH = before;
      folder.dispose();
      reap(pid(), supervisor());
    }
  });
}

test("a command starts with SIGINT's default action: one it sends itself ends it", { skip: process.platform === "win32" }, async () => {
  const { root } = workspace();
  // A shell, not node: node puts the signals it inherits ignored back to their defaults.
  // The command line ends in `--output <file>`: $0 and $1 here; printf is a builtin.
  const write = `printf '%s' '${metadata("Interrupted")}' > "$1"`;
  state.config = { managePy: nodePath.join(root, "manage.py"), metadataCommand: ["/bin/sh", "-c", `kill -INT $$; ${write}`] };
  const { folder } = project(root);
  try {
    await within(folder.start(), "the run");
    assert.equal(folder.state, "failed", folder.detail);
    assert.equal(folder.project, undefined, "the command went on past its SIGINT");
  } finally {
    folder.dispose();
  }
});

// -- render-part SQL records (#188) ------------------------------------------------------------

const PROCESS = "20261008T130000-1";

/** A record of the dev server's: `List` ran `count` queries at line 2 of `template`. */
function queryLine(template: string, count: number, version = "1.0"): string {
  const source = digestOf(readFileSync(template));
  const rows = count ? [{ by: 0, count, sql: "SELECT 1", template: { file: template, rel: "templates/list.html", source, line: 2, node: "{{ }}", text: "items.count" } }] : [];
  const renders = [{ kind: "render", component: "app.live.List", name: "List", id: "list", why: "http" }];
  return `${JSON.stringify({ version, at: new Date().toISOString(), process: PROCESS, segment: 1, base: null, kind: "render", count, renders, rows })}\n`;
}

/** A folder with a manage.py and a template, its metadata from a file so that nothing runs. */
function queriesWorkspace() {
  const { root } = workspace();
  const templates = nodePath.join(root, "templates");
  mkdirSync(templates);
  const template = nodePath.join(templates, "list.html");
  writeFileSync(template, "<ul>\n{{ items.count }}\n</ul>\n");
  const records = nodePath.join(root, ".wireview", "render-queries");
  mkdirSync(records, { recursive: true });
  const meta = nodePath.join(root, "metadata.json");
  writeFileSync(meta, metadata("A"));
  const config = { metadataPath: meta, managePy: nodePath.join(root, "manage.py") };
  const facts = () => ({
    path: template,
    kind: "template" as const,
    dirty: false,
    lineText: (line: number) => readFileSync(template, "utf8").split("\n")[line - 1],
    digest: () => digestOf(readFileSync(template)),
    stat: () => undefined,
  });
  return { root, template, records, config, facts };
}

const labels = (folder: Folder, facts: Parameters<Folder["queryHints"]>[0]) => folder.queryHints(facts).map((hint) => `${hint.line}: ${hint.label}`);

test("the records beside manage.py are read, and again when the watcher says they grew", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  state.config = config;
  const { folder } = project(root);
  let told = 0;
  folder.onQueries = () => (told += 1);
  await folder.start();
  await until(() => folder.queries?.state.size === 1, "the first read");
  assert.deepEqual(labels(folder, facts()), ["2: 6 queries"]);
  const watcher = state.watchers.find((each) => each.pattern.pattern === "*.jsonl");
  assert.ok(watcher, "the directory is watched");

  appendFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 0));
  const before = told;
  watcher.fire();
  watcher.fire(); // a burst: one read
  await until(() => labels(folder, facts()).length === 0, "the read after the event");
  assert.equal(told, before + 1);
  folder.dispose();
});

test("a reader of an older generation reads nothing more and its state is not the folder's", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  const other = nodePath.join(root, "elsewhere");
  mkdirSync(other);
  state.config = config;
  const { folder } = project(root);
  await folder.start();
  await until(() => folder.queries?.state.size === 1, "the first read");
  const old = folder.queries!;
  const oldWatcher = state.watchers.find((each) => each.pattern.pattern === "*.jsonl" && !each.disposed)!;

  state.config = { ...config, "renderQueries.directory": other };
  await folder.configure();
  await until(() => folder.queries !== undefined && folder.queries !== old, "the new reader");
  assert.equal(folder.queries!.directory, other);
  assert.ok(oldWatcher.disposed, "the old directory is no longer watched");
  assert.deepEqual(labels(folder, facts()), [], "what the old directory said is not the new one's");

  // An event already on its way, and a read asked of the old reader: nothing comes in
  appendFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 9));
  oldWatcher.fireAnyway();
  old.read();
  await pause(300);
  assert.equal([...old.state.all()][0].rows[0].count, 6);
  assert.deepEqual(labels(folder, facts()), []);

  folder.dispose();
  assert.equal(folder.queries, undefined);
});

test("a reader still finding manage.py when the folder is configured again starts nothing", async () => {
  const { root, template, records, config } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  state.config = config;
  const { folder } = project(root);
  const first = folder.start();
  folder.dispose(); // before the reader found its directory
  await first;
  await pause(50);
  assert.equal(folder.queries, undefined);
  assert.equal(state.watchers.filter((each) => each.pattern.pattern === "*.jsonl" && !each.disposed).length, 0);
});

test("Restricted Mode: the records are not read until the workspace is trusted", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  state.config = config;
  state.trusted = false;
  const { folder } = project(root);
  await folder.start();
  await pause(100);
  assert.equal(folder.queries, undefined);
  assert.equal(state.watchers.filter((each) => each.pattern.pattern === "*.jsonl").length, 0, "not even watched");
  assert.deepEqual(labels(folder, facts()), []);

  state.trusted = true;
  await folder.configure();
  await until(() => folder.queries?.state.size === 1, "the read once trusted");
  assert.deepEqual(labels(folder, facts()), ["2: 6 queries"]);
  folder.dispose();
});

test("turned off, nothing is read; turned on, the generation there is reads", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  state.config = { ...config, "renderQueries.enable": false };
  const { folder } = project(root);
  await folder.start();
  await pause(100);
  assert.equal(folder.queries, undefined);

  state.config = config;
  await folder.configureQueries();
  assert.deepEqual(labels(folder, facts()), ["2: 6 queries"]);
  state.config = { ...config, "renderQueries.enable": false };
  await folder.configureQueries();
  assert.equal(folder.queries, undefined);
  folder.dispose();
});

test("Clear forgets what was read; the files stay", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  const file = nodePath.join(records, `${PROCESS}.1.jsonl`);
  writeFileSync(file, queryLine(template, 6));
  state.config = config;
  const { folder } = project(root);
  await folder.start();
  await until(() => folder.queries?.state.size === 1, "the first read");
  folder.clearQueries();
  assert.deepEqual(labels(folder, facts()), []);
  assert.ok(readFileSync(file, "utf8").length > 0);
  folder.dispose();
});

test("lines of a version this extension does not read are left, and said once", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6, "2.0") + queryLine(template, 6, "2.1") + queryLine(template, 3));
  state.config = config;
  const { folder, log } = project(root);
  await folder.start();
  await until(() => folder.queries?.state.size === 1, "the first read");
  assert.deepEqual(labels(folder, facts()), ["2: 3 queries"]);
  const said = log.filter((line) => line.includes("Render queries of version"));
  assert.equal(said.length, 1, said.join("\n"));
  assert.match(said[0], /Update the Django Wireview extension/);
  folder.dispose();
});

test("the time limit is the setting's, in minutes", async () => {
  const { root, template, records, config, facts } = queriesWorkspace();
  writeFileSync(nodePath.join(records, `${PROCESS}.1.jsonl`), queryLine(template, 6));
  state.config = { ...config, "renderQueries.maxAge": 5 };
  const { folder } = project(root);
  await folder.start();
  await until(() => folder.queries?.state.size === 1, "the first read");
  assert.equal(folder.queryHints(facts(), Date.now() + 4 * 60_000).length, 1);
  assert.equal(folder.queryHints(facts(), Date.now() + 6 * 60_000).length, 0);
  folder.dispose();
});
