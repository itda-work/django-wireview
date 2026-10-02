// Runs test/host/suite.cjs inside VS Code, the oldest release the extension
// supports (engines.vscode), with this repository as the workspace.
//
// The metadata is made first, from the test project, and handed to the
// extension as wireview.metadataPath: the suite then checks the features
// against what the project really holds. One test switches to running
// `uv run python manage.py wireview_lsp` itself, the path a user gets.
import { execFileSync } from "node:child_process";
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

const code = await runTests({
  vscodeExecutablePath,
  extensionDevelopmentPath: extension,
  extensionTestsPath: path.join(here, "suite.cjs"),
  launchArgs: [workspace, "--disable-extensions", "--user-data-dir", userData, "--skip-welcome", "--skip-release-notes", "--disable-workspace-trust"],
  extensionTestsEnv: { WIREVIEW_HOST_REPOSITORY: repository, WIREVIEW_HOST_METADATA: metadata },
}).catch((error) => {
  console.error(error);
  return 1;
});
process.exit(code);
