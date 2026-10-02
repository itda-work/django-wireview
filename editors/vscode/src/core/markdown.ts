// How a component, a field or a handler reads in a completion's documentation
// and in a hover: Markdown, built from the metadata.
import type { ComponentMeta, FieldMeta, FunctionComponentMeta, MethodMeta, ParameterMeta } from "./metadata.ts";

function cell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function shown(value: unknown): string {
  if (value === null || value === undefined) return "None";
  return typeof value === "string" ? JSON.stringify(value) : String(value);
}

export function fieldsTable(fields: Record<string, FieldMeta>): string {
  const rows = Object.entries(fields);
  if (!rows.length) return "";
  const lines = ["| field | type | default |", "|---|---|---|"];
  for (const [name, field] of rows) {
    lines.push(`| \`${name}\` | \`${cell(field.type)}\` | ${field.required ? "**required**" : `\`${cell(shown(field.default))}\``} |`);
  }
  return lines.join("\n");
}

export function componentDoc(component: ComponentMeta): string {
  const parts = [`**${component.name}** · ${component.kind === "live_component" ? "LiveComponent" : "Component"}`, `\`${component.fqn}\``];
  if (component.docstring) parts.push(component.docstring);
  const table = fieldsTable(component.fields);
  if (table) parts.push(table);
  return parts.join("\n\n");
}

export function parameterList(parameters: Record<string, ParameterMeta>): string {
  return Object.entries(parameters)
    .map(([name, parameter]) => {
      const star = parameter.kind === "VAR_KEYWORD" ? "**" : parameter.kind === "VAR_POSITIONAL" ? "*" : "";
      const type = parameter.type ? `: ${parameter.type}` : "";
      const fallback = parameter.has_default ? ` = ${shown(parameter.default)}` : "";
      return `${star}${name}${type}${fallback}`;
    })
    .join(", ");
}

export function functionComponentDoc(fc: FunctionComponentMeta): string {
  const parts = [`**${fc.name}** · function component`, "```python\n" + `def ${fc.name}(${parameterList(fc.parameters)})` + "\n```"];
  if (fc.docstring) parts.push(fc.docstring);
  return parts.join("\n\n");
}

export function signature(name: string, method: MethodMeta): string {
  return `${method.is_async ? "async " : ""}def ${name}(${parameterList(method.parameters)})`;
}

export function handlerDoc(name: string, method: MethodMeta, owner: string): string {
  const parts = ["```python\n" + signature(name, method) + "\n```", `Defined on \`${owner}\`.`];
  if (method.docstring) parts.push(method.docstring);
  return parts.join("\n\n");
}

export function fieldDoc(name: string, field: FieldMeta, owner: string): string {
  const parts = [`\`${name}: ${field.type}\` · field of \`${owner}\``];
  parts.push(field.required ? "**Required.**" : `Default: \`${shown(field.default)}\``);
  if (!field.in_state) parts.push("Not kept in the signed state (`Meta.exclude_fields`).");
  if (field.description) parts.push(field.description);
  return parts.join("\n\n");
}
