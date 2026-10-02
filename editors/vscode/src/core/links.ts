// Links to templates: from `{% extends %}` and `{% include %}`, and from a Python
// string that names a template (`template_name = "todo/list.html"`).
import { relativeTemplateName } from "./diagnostics.ts";
import type { Env } from "./env.ts";
import type { Project } from "./project.ts";
import type { Span } from "./scan.ts";
import { symbols } from "./symbols.ts";
import type { TemplateDoc } from "./template.ts";

export interface Link {
  span: Span;
  path: string;
}

export function templateLinks(doc: TemplateDoc, env: Env): Link[] {
  const project = env.project;
  if (!project) return [];
  const links: Link[] = [];
  for (const sym of symbols(doc)) {
    if (sym.kind !== "template") continue;
    const name = relativeTemplateName(project, env.path, sym.name);
    const path = name === undefined ? undefined : project.resolveTemplate(name, env.isFile);
    if (path) links.push({ span: sym.span, path });
  }
  return links;
}

// A one-line string that looks like a path to a file: a template name, perhaps
const PYTHON_STRING = /(?<![\w"'])[rbuRBU]?(["'])([\w][\w.\/-]*\.[A-Za-z0-9]+)\1/g;

/** Strings in Python source that name a template the project's directories hold. */
export function pythonLinks(source: string, project: Project, isFile: (path: string) => boolean): Link[] {
  const links: Link[] = [];
  PYTHON_STRING.lastIndex = 0;
  for (let match = PYTHON_STRING.exec(source); match; match = PYTHON_STRING.exec(source)) {
    const path = project.resolveTemplate(match[2], isFile);
    if (!path) continue;
    const start = match.index + match[0].indexOf(match[1]) + 1;
    links.push({ span: { start, end: start + match[2].length }, path });
  }
  return links;
}
