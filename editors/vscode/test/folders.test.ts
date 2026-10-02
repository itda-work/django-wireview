// The folder project's lifecycle: src/folders.ts itself, with the VS Code API
// replaced by test/stub/vscode.ts and real child processes for the command. What
// is checked is what a stale run must not do: start again, write, or win.
import { strict as assert } from "node:assert";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
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
  const runs = () => readFileSync(marker, "utf8").split("\n").filter(Boolean);
  const command = (delay: number) => ({ managePy: nodePath.join(root, "manage.py"), metadataCommand: [process.execPath, child, String(delay)] });
  return { root, runs, command };
}

function project(root: string): { folder: Folder; changes: () => number; log: string[] } {
  let changes = 0;
  const log: string[] = [];
  const output = { appendLine: (line: string) => log.push(line), append: (text: string) => log.push(text) };
  const uri = { fsPath: root, toString: () => `file://${root}` };
  const folder = new FolderProject({ name: "w", uri, index: 0 } as never, nodePath.join(root, "storage"), output as never, () => {
    changes += 1;
  });
  return { folder, changes: () => changes, log };
}

async function until(check: () => boolean, what: string, timeout = 5000): Promise<void> {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise((done) => setTimeout(done, 10));
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
