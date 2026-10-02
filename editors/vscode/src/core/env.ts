// What the features need from outside a template's text. The adapter gives the
// real file system; the tests give a few strings.
import type { Project } from "./project.ts";

export interface Env {
  /** Undefined without metadata: then only what the text alone shows is offered. */
  project: Project | undefined;
  /** The document's path with symbolic links resolved: the metadata's paths are `Path.resolve()`d. */
  path: string;
  /** The path the editor opened, when it is not `path`: a link in a template directory is found by its own name. */
  documentPath?: string;
  readFile(path: string): string | undefined;
  isFile(path: string): boolean;
  /** The names of the templates under `template_dirs`. */
  templateNames(): string[];
}

/** The name a loader finds the document by: its real path's, or, for a link in a template directory, the link's. */
export function ownTemplateName(project: Project, env: Env): string | undefined {
  return project.templateName(env.path) ?? (env.documentPath ? project.templateName(env.documentPath) : undefined);
}

/** A place in a file: 1-based line, as the metadata writes it. */
export interface Location {
  path: string;
  line: number;
}
