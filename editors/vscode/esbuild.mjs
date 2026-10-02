// Bundles the extension into one CommonJS file: VS Code loads `main` with require().
// The sources are ES modules that node runs as they are (type stripping), which is
// what the tests do; only the extension host needs the bundle.
import { copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const production = process.argv.includes("--production");
const watch = process.argv.includes("--watch");

/** @type {import("esbuild").BuildOptions} */
const options = {
  absWorkingDir: here,
  entryPoints: ["src/extension.ts"],
  outfile: "dist/extension.cjs",
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
  // vscode-html-languageservice's `main` is a UMD build whose require() calls
  // esbuild cannot follow: the bundle failed at activation. Its `module` is ESM.
  mainFields: ["module", "main"],
  minify: production,
  sourcemap: !production,
  logLevel: "info",
};

if (production) {
  // The package carries the repository's licence; one copy is tracked, at the root
  copyFileSync(join(here, "..", "..", "LICENSE"), join(here, "LICENSE"));
}

if (watch) {
  const context = await esbuild.context(options);
  await context.watch();
} else {
  await esbuild.build(options);
}
