// Diagnoses templates the way the extension does, outside VS Code.
//
//   node scripts/diagnose.ts [--strict] <metadata.json> <file or directory>...
//
// Prints {"<path>": [{code, severity, message, line, column}]} for every .html
// file given or found under a directory given, and exits 1 when any of them is an
// error (with --strict, a warning too), 0 when none is, 2 on a usage error. An
// error is what Django or django-wireview raises when it renders the template; a
// warning may be fine (another library's tag, an id that already holds the field),
// so a gate fails on it only when asked (#179).
//
// tests/test_vscode_extension.py runs it over every template in the repository
// and expects nothing, and `manage.py wireview_check_templates` runs the copy the
// wheel ships (hatch_build.py). It uses the core modules alone, so it needs node
// and none of the npm packages.
import { readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import * as nodePath from "node:path";

import { diagnose } from "../src/core/diagnostics.ts";
import type { Env } from "../src/core/env.ts";
import type { Metadata } from "../src/core/metadata.ts";
import { Project } from "../src/core/project.ts";
import { parseTemplate } from "../src/core/template.ts";

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function htmlFiles(path: string): string[] {
  if (isFile(path)) return [path];
  const found: string[] = [];
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const child = nodePath.join(path, entry.name);
    if (entry.isDirectory()) found.push(...htmlFiles(child));
    else if (entry.name.endsWith(".html")) found.push(child);
  }
  return found.sort();
}

const args = process.argv.slice(2);
const strict = args[0] === "--strict";
const [metadataPath, ...targets] = strict ? args.slice(1) : args;
if (!metadataPath || !targets.length || metadataPath.startsWith("-")) {
  process.stderr.write("usage: node scripts/diagnose.ts [--strict] <metadata.json> <file or directory>...\n");
  process.exit(2);
}
const failing = new Set<string>(strict ? ["error", "warning"] : ["error"]);
const project = new Project(JSON.parse(readFileSync(metadataPath, "utf8")) as Metadata);
const result: Record<string, unknown[]> = {};
let failed = false;
for (const target of targets) {
  for (const file of htmlFiles(target)) {
    const path = realpathSync(file);
    const text = readFileSync(path, "utf8");
    const env: Env = {
      project,
      path,
      documentPath: path === nodePath.resolve(file) ? undefined : nodePath.resolve(file),
      readFile: (other) => (isFile(other) ? readFileSync(other, "utf8") : undefined),
      isFile,
      templateNames: () => [],
    };
    const doc = parseTemplate(text, project);
    const problems = diagnose(doc, env);
    failed ||= problems.some((problem) => failing.has(problem.severity));
    result[file] = problems.map((problem) => {
      const before = text.slice(0, problem.span.start).split("\n");
      return {
        code: problem.code,
        severity: problem.severity,
        message: problem.message,
        line: before.length,
        column: before[before.length - 1].length + 1,
      };
    });
  }
}
process.stdout.write(JSON.stringify(result, null, 1) + "\n");
process.exitCode = failed ? 1 : 0;
