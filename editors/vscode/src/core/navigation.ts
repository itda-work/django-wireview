// Hover and go to definition: what the name under the cursor is, and where it
// is defined. Both read the symbol list (symbols.ts) and the metadata.
import { relativeTemplateName } from "./diagnostics.ts";
import type { Env, Location } from "./env.ts";
import { componentDoc, fieldDoc, functionComponentDoc, handlerDoc } from "./markdown.ts";
import type { ComponentMeta } from "./metadata.ts";
import type { Project } from "./project.ts";
import type { Span } from "./scan.ts";
import { findFieldLine } from "./source.ts";
import { symbolAt } from "./symbols.ts";
import type { Sym } from "./symbols.ts";
import type { TemplateDoc } from "./template.ts";

export interface Hover {
  span: Span;
  markdown: string;
}

function tagOrFilterDoc(name: string, library: string | null, docstring: string | null, what: string): string {
  const parts = [`**${name}** · ${what} · ${library ? `\`{% load ${library} %}\`` : "builtin"}`];
  if (docstring) parts.push(docstring);
  return parts.join("\n\n");
}

/** The owners' handler of this name, with the component that defines it. */
function ownersHandler(project: Project, env: Env, name: string): { owner: ComponentMeta; method: ComponentMeta["methods"][string] } | undefined {
  const owners = project.owners(env.path);
  const candidates = owners.length ? owners : project.components;
  for (const owner of candidates) {
    const method = owner.methods[name];
    if (method?.is_handler) return { owner, method };
  }
  for (const owner of owners) {
    if (owner.methods[name]) return { owner, method: owner.methods[name] };
  }
  return undefined;
}

export function hover(doc: TemplateDoc, offset: number, env: Env): Hover | undefined {
  const project = env.project;
  const sym = symbolAt(doc, offset);
  if (!sym || !project) return undefined;
  const markdown = hoverText(doc, sym, project, env);
  return markdown ? { span: sym.span, markdown } : undefined;
}

function hoverText(doc: TemplateDoc, sym: Sym, project: Project, env: Env): string | undefined {
  switch (sym.kind) {
    case "tag": {
      const entry = doc.visible?.tags.get(sym.name);
      if (entry) return tagOrFilterDoc(sym.name, entry.library, entry.meta.docstring, "tag");
      const opener = doc.blocks.find((block) => block.close === sym.tag || block.middles.includes(sym.tag));
      return opener ? `Part of \`{% ${opener.open.name} %}\` on line ${lineOf(doc.text, opener.open.start)}.` : undefined;
    }
    case "filter": {
      const entry = doc.visible?.filters.get(sym.name);
      if (!entry) return undefined;
      const argument = { none: "takes no argument", optional: "takes an optional argument", required: "takes an argument" }[entry.meta.argument];
      return `${tagOrFilterDoc(sym.name, entry.library, null, "filter")} · ${argument}${entry.meta.docstring ? `\n\n${entry.meta.docstring}` : ""}`;
    }
    case "library": {
      const library = project.libraries[sym.name];
      if (!library) return undefined;
      const tags = Object.keys(library.tags).map((name) => `\`${name}\``).join(", ");
      const filters = Object.keys(library.filters).map((name) => `\`${name}\``).join(", ");
      return [`**${sym.name}** · \`${library.module ?? ""}\``, tags && `Tags: ${tags}`, filters && `Filters: ${filters}`].filter(Boolean).join("\n\n");
    }
    case "template": {
      const name = relativeTemplateName(project, env, sym.name);
      const path = name === undefined ? undefined : project.resolveTemplate(name, env.isFile);
      return path ? `\`${path}\`` : undefined;
    }
    case "component": {
      const component = project.component(sym.name);
      return component ? componentDoc(component) : undefined;
    }
    case "function-component": {
      const fc = project.functionComponent(sym.name);
      return fc ? functionComponentDoc(fc) : undefined;
    }
    case "argument": {
      if (sym.family === "function") {
        const parameter = project.functionComponent(sym.target)?.parameters[sym.key];
        return parameter ? `\`${sym.key}${parameter.type ? `: ${parameter.type}` : ""}\`${parameter.has_default ? "" : " · **required**"}` : undefined;
      }
      if (sym.key === "id") return "The component instance's id: the same id on a later render is the same instance.";
      const component = project.component(sym.target);
      const field = component?.fields[sym.key];
      return component && field ? fieldDoc(sym.key, field, component.name) : undefined;
    }
    case "modifier": {
      const modifier = project.metadata.modifiers?.[sym.name];
      return modifier ? `**${sym.name}** · modifier\n\n${modifier.description}` : undefined;
    }
    case "handler": {
      const found = ownersHandler(project, env, sym.name);
      return found ? handlerDoc(sym.name, found.method, found.owner.name) : undefined;
    }
    case "handler-argument": {
      const parameter = ownersHandler(project, env, sym.handler)?.method.parameters[sym.key];
      return parameter ? `\`${sym.key}${parameter.type ? `: ${parameter.type}` : ""}\` · argument of \`${sym.handler}\`` : undefined;
    }
    case "hook": {
      const hook = project.metadata.hooks?.[sym.name];
      return hook ? `**${sym.name}** · hook\n\n\`${hook.static_path}\`` : undefined;
    }
    case "variable": {
      const found = variableTarget(project, env, sym.path);
      if (!found) return undefined;
      if (found.field) return fieldDoc(found.name, found.field, found.owner.name);
      if (found.property) {
        const type = found.property.type ? `: ${found.property.type}` : "";
        return [`\`${found.name}${type}\` · property of \`${found.owner.name}\``, found.property.docstring].filter(Boolean).join("\n\n");
      }
      return componentDoc(found.owner);
    }
    default:
      return undefined;
  }
}

function lineOf(text: string, offset: number): number {
  return text.slice(0, offset).split("\n").length;
}

interface VariableTarget {
  owner: ComponentMeta;
  name: string;
  field?: ComponentMeta["fields"][string];
  property?: NonNullable<ComponentMeta["properties"]>[string];
}

/** The field or the property a variable of a component template is. `this` alone is the component. */
function variableTarget(project: Project, env: Env, path: string[]): VariableTarget | undefined {
  const name = path[path.length - 1];
  for (const owner of project.owners(env.path)) {
    if (path.length === 1 && name === "this") return { owner, name };
    const field = owner.fields[name];
    if (field) return { owner, name, field };
    const property = owner.properties?.[name];
    if (property) return { owner, name, property };
  }
  return undefined;
}

export function definition(doc: TemplateDoc, offset: number, env: Env): Location | undefined {
  const project = env.project;
  const sym = symbolAt(doc, offset);
  if (!sym || !project) return undefined;
  const at = (path: string | undefined | null, line: number | undefined | null): Location | undefined =>
    path ? { path, line: Math.max(line ?? 1, 1) } : undefined;
  switch (sym.kind) {
    case "tag": {
      const entry = doc.visible?.tags.get(sym.name);
      if (entry) return at(entry.meta.file_path, entry.meta.line_number);
      const opener = doc.blocks.find((block) => block.close === sym.tag || block.middles.includes(sym.tag));
      return opener ? { path: env.path, line: lineOf(doc.text, opener.open.start) } : undefined;
    }
    case "filter": {
      const entry = doc.visible?.filters.get(sym.name);
      return entry ? at(entry.meta.file_path, entry.meta.line_number) : undefined;
    }
    case "library":
      return at(project.libraryFile(sym.name), 1);
    case "loaded-name": {
      const library = project.libraries[sym.library];
      const meta = library?.tags[sym.name] ?? library?.filters[sym.name];
      return meta ? at(meta.file_path, meta.line_number) : undefined;
    }
    case "template": {
      const name = relativeTemplateName(project, env, sym.name);
      return at(name === undefined ? undefined : project.resolveTemplate(name, env.isFile), 1);
    }
    case "component": {
      const component = project.component(sym.name);
      return component ? at(component.file_path, component.line_number) : undefined;
    }
    case "function-component": {
      const fc = project.functionComponent(sym.name);
      return fc ? at(fc.file_path, fc.line_number) : undefined;
    }
    case "argument": {
      if (sym.family === "function") {
        const fc = project.functionComponent(sym.target);
        return fc ? at(fc.file_path, fc.line_number) : undefined;
      }
      const component = project.component(sym.target);
      if (!component || !component.file_path) return undefined;
      return fieldLocation(env, component, sym.key);
    }
    case "handler": {
      const found = ownersHandler(project, env, sym.name);
      return found ? at(found.method.file_path || found.owner.file_path, found.method.line_number) : undefined;
    }
    case "handler-argument": {
      const found = ownersHandler(project, env, sym.handler);
      return found ? at(found.method.file_path || found.owner.file_path, found.method.line_number) : undefined;
    }
    case "hook": {
      const hook = project.metadata.hooks?.[sym.name];
      return hook ? at(hook.file_path, hook.line_number) : undefined;
    }
    case "variable": {
      const found = variableTarget(project, env, sym.path);
      if (!found) return undefined;
      if (found.property) return at(found.property.file_path, found.property.line_number);
      if (found.field) return fieldLocation(env, found.owner, found.name);
      return at(found.owner.file_path, found.owner.line_number);
    }
    default:
      return undefined;
  }
}

function fieldLocation(env: Env, component: ComponentMeta, field: string): Location {
  const source = env.readFile(component.file_path);
  const line = source === undefined ? component.line_number : findFieldLine(source, component.line_number, field);
  return { path: component.file_path, line };
}
