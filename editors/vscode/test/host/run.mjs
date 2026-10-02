// Runs test/host/suite.cjs inside VS Code, the oldest release the extension
// supports (engines.vscode), with this repository as the workspace; then
// test/host/restricted.cjs with the same workspace untrusted.
//
// The metadata is made first, from the test project, and handed to the
// extension as wireview.metadataPath: the suite then checks the features
// against what the project really holds. One test switches to running
// `uv run python manage.py wireview_lsp` itself, the path a user gets.
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
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

const workspace = path.join(scratch, "wireview.code-workspace");
writeFileSync(
  workspace,
  JSON.stringify(
    {
      folders: [{ path: repository }],
      settings: {
        "wireview.metadataPath": metadata,
        "wireview.managePy": "tests/manage.py",
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

const env = { WIREVIEW_HOST_REPOSITORY: repository, WIREVIEW_HOST_METADATA: metadata };
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
