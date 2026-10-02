// What the features need from outside a template's text. The adapter gives the
// real file system; the tests give a few strings.
import type { Project } from "./project.ts";

export interface Env {
  /** Undefined without metadata: then only what the text alone shows is offered. */
  project: Project | undefined;
  /** The document's path with symbolic links resolved: the metadata's paths are `Path.resolve()`d. */
  path: string;
  readFile(path: string): string | undefined;
  isFile(path: string): boolean;
  /** The names of the templates under `template_dirs`. */
  templateNames(): string[];
}

/** A place in a file: 1-based line, as the metadata writes it. */
export interface Location {
  path: string;
  line: number;
}
