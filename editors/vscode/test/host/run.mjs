// Runs test/host/suite.cjs inside VS Code, the oldest release the extension
// supports (engines.vscode), with this repository as the workspace; then
// test/host/restricted.cjs with the same workspace untrusted.
//
// The metadata is made first, from the test project, and handed to the
// extension as wireview.metadataPath: the suite then checks the features
// against what the project really holds. One test switches to running
// `uv run python manage.py wireview_lsp` itself, the path a user gets.
//
// A render-part SQL record (#188) for a line of the todo example is written to
// a directory of the run's, named by wireview.renderQueries.directory: the
// trusted suite sees its hint, the restricted one does not.
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { downloadAndUnzipVSCode, runTests } from "@vscode/test-electron";

const here = path.dirname(fileURLToPath(import.meta.url));
const extension = path.resolve(here, "..", "..");
const repository = path.resolve(extension, "..", "..");
const VERSION = "1.100.0";

const scratch = mkdtempSync(path.join(tmpdir(), "wireview-host-"));
const metadata = path.join(scratch, "metadata.json");
execFileSync("uv", ["run", "python", "manage.py", "wireview_lsp", "--output", metadata], {
  cwd: path.join(repository, "tests"),
  stdio: "inherit",
});

const queries = path.join(scratch, "render-queries");
mkdirSync(queries);
const item = realpathSync(path.join(repository, "examples", "todo", "templates", "todo", "item.html"));
const itemText = readFileSync(item, "utf8");
const QUERIED_LINE = itemText.split(/\r?\n/).findIndex((line) => line.includes("{{ item.text }}</label>")) + 1;
writeFileSync(
  path.join(queries, "20261008T130000-1.1.jsonl"),
  `${JSON.stringify({
    version: "1.0",
    at: new Date().toISOString(),
    process: "20261008T130000-1",
    segment: 1,
    base: null,
    kind: "render",
    count: 6,
    renders: [{ kind: "render", component: "todo.live.XTodoItem", name: "XTodoItem", id: "item-1", why: "http" }],
    rows: [
      {
        by: 0,
        count: 6,
        repeated: true,
        sql: 'SELECT "todo_item"."id" FROM "todo_item" WHERE "todo_item"."id" = %s',
        template: {
          file: item,
          name: "todo/item.html",
          source: createHash("sha256").update(itemText.replace(/\r\n?/g, "\n")).digest("hex"),
          line: QUERIED_LINE,
          node: "{{ }}",
          text: "item.text",
        },
      },
    ],
  })}\n`,
);

const workspace = path.join(scratch, "wireview.code-workspace");
writeFileSync(
  workspace,
  JSON.stringify(
    {
      folders: [{ path: repository }],
      settings: {
        "wireview.metadataPath": metadata,
        "wireview.managePy": "tests/manage.py",
        "wireview.renderQueries.directory": queries,
        "workbench.startupEditor": "none",
        "extensions.autoUpdate": false,
        "update.mode": "none",
        "telemetry.telemetryLevel": "off",
      },
    },
    null,
    2,
  ),
);
const userData = path.join(scratch, "user-data");
mkdirSync(userData, { recursive: true });

// Already in .vscode-test when it was fetched once: nothing is downloaded then
const vscodeExecutablePath = await downloadAndUnzipVSCode({ version: VERSION, cachePath: path.join(extension, ".vscode-test") });

const env = { WIREVIEW_HOST_REPOSITORY: repository, WIREVIEW_HOST_METADATA: metadata, WIREVIEW_HOST_QUERIED_LINE: String(QUERIED_LINE) };
const run = (suite, args) =>
  runTests({
    vscodeExecutablePath,
    extensionDevelopmentPath: extension,
    extensionTestsPath: path.join(here, suite),
    launchArgs: [workspace, "--disable-extensions", "--skip-welcome", "--skip-release-notes", ...args],
    extensionTestsEnv: env,
  }).catch((error) => {
    console.error(error);
    return 1;
  });

const trusted = await run("suite.cjs", ["--user-data-dir", userData, "--disable-workspace-trust"]);

// The same workspace, never trusted: a profile of its own, and no prompt to answer
const restrictedData = path.join(scratch, "user-data-restricted");
mkdirSync(path.join(restrictedData, "User"), { recursive: true });
writeFileSync(
  path.join(restrictedData, "User", "settings.json"),
  JSON.stringify({ "security.workspace.trust.enabled": true, "security.workspace.trust.startupPrompt": "never", "security.workspace.trust.banner": "never" }),
);
// runTests() always adds --disable-workspace-trust: this run starts VS Code itself, with its arguments but that one
const restricted = await new Promise((done) => {
  const child = spawn(
    vscodeExecutablePath,
    [
      workspace,
      "--disable-extensions",
      "--skip-welcome",
      "--skip-release-notes",
      "--no-sandbox",
      "--disable-gpu-sandbox",
      "--disable-updates",
      "--no-cached-data",
      `--extensionTestsPath=${path.join(here, "restricted.cjs")}`,
      `--extensionDevelopmentPath=${extension}`,
      "--user-data-dir",
      restrictedData,
      "--extensions-dir",
      path.join(scratch, "extensions-restricted"),
    ],
    { env: { ...process.env, ...env }, stdio: "inherit" },
  );
  child.on("error", (error) => {
    console.error(error);
    done(1);
  });
  child.on("exit", (code, signal) => done(code ?? (signal ? 1 : 0)));
});
process.exit(trusted || restricted);
