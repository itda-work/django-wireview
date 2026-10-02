// What a feature reads out of another file's text: the line a field is declared
// on, the slots a component's template renders.
import { kwargOf, scan } from "./scan.ts";
import type { TagToken } from "./scan.ts";
import { argumentBits, plainLiteral } from "./symbols.ts";

/**
 * The 1-based line a class declares a field on, or the class's own line.
 *
 * The metadata has no line for a field: pydantic does not keep one. So the
 * class body is searched for `name: ...` or `name = ...`, from the class line
 * down to the first line indented no deeper than the class. A field a base
 * class declares is not in this body; the class line is the answer then.
 */
export function findFieldLine(source: string, classLine: number, name: string): number {
  const lines = source.split(/\r?\n/);
  if (lines[classLine - 1] === undefined) return classLine;
  const classIndent = indentOf(lines[classLine - 1]);
  // The header may run over several lines (and start at a decorator): it ends with the colon
  let index = classLine - 1;
  while (index < lines.length && !/:\s*(?:#.*)?$/.test(lines[index])) index++;
  const declares = new RegExp(`^\\s+${name}\\s*(?::|=(?!=))`);
  let bodyIndent = -1;
  for (index += 1; index < lines.length; index++) {
    const line = lines[index];
    if (!line.trim() || line.trimStart().startsWith("#")) continue;
    const indent = indentOf(line);
    if (indent <= classIndent) break;
    if (bodyIndent === -1) bodyIndent = indent;
    // The class's own statements only, not a method's locals
    if (indent === bodyIndent && declares.test(line)) return index + 1;
  }
  return classLine;
}

function indentOf(line: string): number {
  return line.length - line.trimStart().length;
}

/** `{% render_slot "name" key=value %}` in a template: each slot name, with the keys it passes to `let:`. */
export function renderedSlots(templateText: string): Map<string, Set<string>> {
  const slots = new Map<string, Set<string>>();
  for (const token of scan(templateText)) {
    if (token.kind !== "tag" || !token.closed || token.name !== "render_slot") continue;
    const tag: TagToken = token;
    const first = tag.bits[0];
    const literal = plainLiteral(first);
    // `{% render_slot %}` and `{% render_slot item=x %}` draw the default slot
    const name = literal ? literal.value : !first || kwargOf(first) ? "" : undefined;
    if (name === undefined) continue;
    const keys = slots.get(name) ?? new Set<string>();
    for (const bit of argumentBits(tag).slice(literal ? 1 : 0)) {
      const kwarg = kwargOf(bit);
      if (kwarg) keys.add(kwarg.key);
    }
    slots.set(name, keys);
  }
  return slots;
}
