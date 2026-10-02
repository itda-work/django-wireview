// Diagnoses templates the way the extension does, outside VS Code.
//
//   node scripts/diagnose.ts <metadata.json> <file or directory>...
//
// Prints {"<path>": [{code, severity, message, line, column}]} for every .html
// file given or found under a directory given. tests/test_vscode_extension.py
// runs it over every template in the repository and expects nothing; it uses
// the core modules alone, so it needs node and none of the npm packages.
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

const [metadataPath, ...targets] = process.argv.slice(2);
if (!metadataPath || !targets.length) {
  process.stderr.write("usage: node scripts/diagnose.ts <metadata.json> <file or directory>...\n");
  process.exit(2);
}
const project = new Project(JSON.parse(readFileSync(metadataPath, "utf8")) as Metadata);
const result: Record<string, unknown[]> = {};
for (const target of targets) {
  for (const file of htmlFiles(target)) {
    const path = realpathSync(file);
    const text = readFileSync(path, "utf8");
    const env: Env = {
      project,
      path,
      readFile: (other) => (isFile(other) ? readFileSync(other, "utf8") : undefined),
      isFile,
      templateNames: () => [],
    };
    const doc = parseTemplate(text, project);
    result[file] = diagnose(doc, env).map((problem) => {
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
