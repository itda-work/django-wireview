// What to offer at the cursor in a template. The position decides: a tag's name,
// an argument of a known tag, a filter after `|`, a variable, an attribute value.
import { ownTemplateName } from "./env.ts";
import type { Env } from "./env.ts";
import { componentDoc, fieldDoc, functionComponentDoc, handlerDoc, signature } from "./markdown.ts";
import type { ComponentMeta, FunctionComponentMeta, MethodMeta } from "./metadata.ts";
import { maskDjango } from "./mask.ts";
import type { Project, TagEntry } from "./project.ts";
import { inComment, kwargOf, literalOf, tokenAt, withoutStrings } from "./scan.ts";
import type { Bit, Span, TagToken, VariableToken } from "./scan.ts";
import { renderedSlots } from "./source.ts";
import { COMPONENT_TAGS, FUNCTION_TAGS, LIVE_COMPONENT_TAGS, plainLiteral, SLOT_HOSTS } from "./symbols.ts";
import { blockAt, blocksAround } from "./template.ts";
import type { Block, TemplateDoc } from "./template.ts";

export type CompletionKind =
  | "tag"
  | "filter"
  | "library"
  | "template"
  | "component"
  | "function"
  | "argument"
  | "event"
  | "modifier"
  | "value"
  | "handler"
  | "slot"
  | "hook"
  | "variable";

export interface Completion {
  label: string;
  kind: CompletionKind;
  /** Replaced by `insert`. */
  range: Span;
  insert: string;
  /** `insert` is a snippet: `$1`, `${1:name}`, `$0`. */
  snippet?: boolean;
  detail?: string;
  /** Markdown. */
  documentation?: string;
  sortText?: string;
  filterText?: string;
  /** Other edits made with this one: `{% load wireview %}`. */
  edits?: { span: Span; text: string }[];
  /** Ask for completions again once inserted: the next argument follows. */
  retrigger?: boolean;
}

export interface CompletionOptions {
  /** Tag name -> what follows `{% ` when it is inserted, as a snippet (data/tag-snippets.json). */
  tagSnippets?: Record<string, string>;
}

/** Suggestions only: any name is a valid event. */
export const DOM_EVENTS = [
  "click",
  "submit",
  "input",
  "change",
  "keydown",
  "keyup",
  "focus",
  "blur",
  "dblclick",
  "mouseenter",
  "mouseleave",
  "mousedown",
  "mouseup",
  "contextmenu",
  "scroll",
  "paste",
  "dragstart",
  "drop",
];

const ARGUMENT_VALUES: Record<string, string[]> = {
  number: ["150", "300", "500", "1000"],
  key: ["Enter", "Escape", "Tab", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"],
  key_code: ["13", "27", "32"],
};

/** Variables every component template has. */
const COMPONENT_VARIABLES: Record<string, string> = {
  this: "The component instance.",
  id: "The component's id.",
  slots: "The slots the template that drew this component filled.",
  user: "The user of the request or of the connection.",
};

export function complete(doc: TemplateDoc, offset: number, env: Env, options: CompletionOptions = {}): Completion[] {
  if (inComment(doc.tokens, offset)) return [];
  const token = tokenAt(doc.tokens, offset);
  if (!token) return attributeCompletions(doc, offset, env);
  if (token.kind === "variable") return variableTokenCompletions(doc, token, offset, env);
  return tagCompletions(doc, token, offset, env, options);
}

// Tags

function tagCompletions(doc: TemplateDoc, tag: TagToken, offset: number, env: Env, options: CompletionOptions): Completion[] {
  if (!tag.nameSpan || offset <= tag.nameSpan.end) {
    if (tag.nameSpan && offset < tag.nameSpan.start) return [];
    return tagNameCompletions(doc, tag, offset, env, options);
  }
  const project = env.project;
  const bit = tag.bits.find((candidate) => candidate.start <= offset && offset <= candidate.end);
  const index = bit ? tag.bits.indexOf(bit) : tag.bits.filter((candidate) => candidate.end < offset).length;
  const prefix = bit ? doc.text.slice(bit.start, offset) : "";

  const filter = /\|\s*(\w*)$/.exec(withoutStrings(prefix));
  if (filter) return filterCompletions(doc, project, { start: offset - filter[1].length, end: wordEnd(doc.text, offset) });

  const name = tag.name;
  if (!project) return [];
  if (name === "load") {
    if (tag.bits.some((candidate) => candidate.text === "from")) return [];
    const loaded = new Set(tag.bits.filter((candidate) => candidate !== bit).map((candidate) => candidate.text));
    const range = bit ? { start: bit.start, end: bit.end } : { start: offset, end: offset };
    return Object.entries(project.libraries)
      .filter(([library]) => !loaded.has(library) && !doc.loads.full.has(library))
      .map(([library, meta]) => ({ label: library, kind: "library", range, insert: library, detail: meta.module }));
  }
  if ((name === "extends" || name === "include") && index === 0) {
    const own = ownTemplateName(project, env);
    return env
      .templateNames()
      .filter((template) => template !== own)
      .map((template) => literalCompletion(bit, offset, template, { kind: "template" }));
  }
  if (COMPONENT_TAGS.has(name) || LIVE_COMPONENT_TAGS.has(name)) {
    if (index === 0) {
      const kind = LIVE_COMPONENT_TAGS.has(name) ? "live_component" : "component";
      return project.components
        .filter((component) => component.kind === kind)
        .map((component) =>
          literalCompletion(bit, offset, component.name, {
            kind: "component",
            detail: component.fqn,
            documentation: componentDoc(component),
            retrigger: false,
          }),
        );
    }
    if (isValuePosition(prefix)) return variableCompletions(doc, offset, env, valueRange(doc, offset, prefix));
    const component = project.component(plainLiteral(tag.bits[0])?.value ?? "");
    return component ? componentArgumentCompletions(tag, bit, offset, component, LIVE_COMPONENT_TAGS.has(name)) : [];
  }
  if (FUNCTION_TAGS.has(name)) {
    if (index === 0) {
      return project.functionComponents.map((fc) =>
        literalCompletion(bit, offset, fc.name, { kind: "function", detail: fc.fqn, documentation: functionComponentDoc(fc) }),
      );
    }
    if (isValuePosition(prefix)) return variableCompletions(doc, offset, env, valueRange(doc, offset, prefix));
    const fc = project.functionComponent(plainLiteral(tag.bits[0])?.value ?? "");
    return fc ? functionArgumentCompletions(tag, bit, offset, fc) : [];
  }
  if (name === "on") return onCompletions(doc, tag, bit, index, offset, prefix, env);
  if (name === "fill") return fillCompletions(doc, tag, bit, index, offset, env);
  if (name === "render_slot" && index === 0) {
    const owners = project.owners(env.path);
    const slots = new Set<string>([...owners.flatMap((owner) => Object.keys(owner.slots ?? {}))]);
    for (const fc of project.functionOwners(env.path)) for (const slot of Object.keys(fc.slots ?? {})) slots.add(slot);
    return [...slots].map((slot) => literalCompletion(bit, offset, slot, { kind: "slot" }));
  }
  if (isExpressionPosition(tag, index, prefix)) {
    return variableCompletions(doc, offset, env, valueRange(doc, offset, prefix));
  }
  return [];
}

function wordEnd(text: string, offset: number): number {
  let end = offset;
  while (end < text.length && /\w/.test(text[end])) end += 1;
  return end;
}

function isValuePosition(prefix: string): boolean {
  return /^[A-Za-z_]\w*=/.test(prefix);
}

/** Where a variable name may stand in a tag that is not one of wireview's. */
function isExpressionPosition(tag: TagToken, index: number, prefix: string): boolean {
  if (isValuePosition(prefix)) return true;
  if (inString(prefix)) return false;
  switch (tag.name) {
    case "if":
    case "elif":
    case "firstof":
    case "cycle":
      return true;
    case "for":
      return tag.bits.slice(0, index).some((bit) => bit.text === "in");
    case "with":
      return index === 0 && tag.bits[1]?.text === "as";
    default:
      return false;
  }
}

/** The range a variable name being typed replaces: its last dotted segment. */
function valueRange(doc: TemplateDoc, offset: number, prefix: string): { range: Span; path: string[] } {
  const word = /[\w.]*$/.exec(prefix.replace(/^[A-Za-z_]\w*=/, ""))?.[0] ?? "";
  const path = word.split(".");
  const last = path[path.length - 1];
  return { range: { start: offset - last.length, end: wordEnd(doc.text, offset) }, path: path.slice(0, -1) };
}

/** A quoted name: inside the quotes when they are there, with them when they are not. */
function literalCompletion(
  bit: Bit | undefined,
  offset: number,
  value: string,
  extra: Partial<Completion> & { kind: CompletionKind },
): Completion {
  const literal = literalOf(bit);
  if (bit && literal) {
    const end = literal.terminated ? literal.end : bit.end;
    return { label: value, range: { start: literal.start, end }, insert: value, ...extra };
  }
  const range = bit ? { start: bit.start, end: bit.end } : { start: offset, end: offset };
  return { label: value, range, insert: `"${value}"`, filterText: `"${value}"`, ...extra };
}

function tagNameCompletions(doc: TemplateDoc, tag: TagToken, offset: number, env: Env, options: CompletionOptions): Completion[] {
  const project = env.project;
  const visible = doc.visible;
  if (!project || !visible) return [];
  const nameRange = tag.nameSpan ? { start: tag.nameSpan.start, end: tag.nameSpan.end } : { start: offset, end: offset };
  // Only the name so far: the whole tag can go in, its end tag too
  const empty = tag.bits.length === 0;
  let fullRange = nameRange;
  if (empty) {
    const tail = doc.text.slice(tag.contentEnd, tag.end);
    const end = tag.closed || /^[\s}%#]*$/.test(tail) ? tag.end : tag.contentEnd;
    fullRange = { start: nameRange.start, end };
  }
  const space = doc.text[nameRange.start - 1] === "%" ? " " : "";
  const snippets = options.tagSnippets ?? {};

  const make = (name: string, end: string | null, extra: Partial<Completion>): Completion => {
    if (!empty) return { label: name, kind: "tag", range: nameRange, insert: name, ...extra };
    const opening = snippets[name] ?? escapeSnippet(name);
    const body = end ? `${opening} %}\n\t$0\n{% ${end} %}` : opening.includes("$0") ? `${opening} %}` : `${opening} %}$0`;
    // `component "$1"`: the first stop is a name to pick, so ask for the names there
    const retrigger = /^[^$]*"\$1"/.test(opening);
    return { label: name, kind: "tag", range: fullRange, insert: space + body, snippet: true, filterText: name, retrigger, ...extra };
  };

  const items: Completion[] = [];
  const seen = new Set<string>();
  // What the block the cursor is in waits for
  const block = blockAt(doc, tag.start);
  if (block) {
    const entry = visible.tags.get(block.open.name);
    for (const name of [...(entry?.meta.intermediate ?? []), entry?.meta.end].filter((name): name is string => Boolean(name))) {
      seen.add(name);
      items.push(make(name, null, { detail: `{% ${block.open.name} %}`, sortText: `0${name === entry?.meta.end ? "0" : "1"}${name}` }));
    }
  }
  for (const entry of visible.tags.values()) {
    if (seen.has(entry.name)) continue;
    seen.add(entry.name);
    items.push(make(entry.name, entry.meta.end, { detail: entry.library ?? "builtin", documentation: entry.meta.docstring ?? undefined, sortText: `1${entry.name}` }));
  }
  const wireview = project.libraries.wireview;
  if (wireview && !doc.loads.full.has("wireview")) {
    const load = loadEdit(doc);
    for (const [name, meta] of Object.entries(wireview.tags)) {
      if (seen.has(name)) continue;
      const entry: TagEntry = { name, meta, library: "wireview" };
      items.push(
        make(name, entry.meta.end, {
          detail: "wireview (adds {% load wireview %})",
          documentation: meta.docstring ?? undefined,
          sortText: `2${name}`,
          edits: [load],
        }),
      );
    }
  }
  return items;
}

function escapeSnippet(text: string): string {
  return text.replace(/[$}\\]/g, "\\$&");
}

/** Where `{% load wireview %}` goes: after `{% extends %}` and the other loads, or at the top. */
function loadEdit(doc: TemplateDoc): { span: Span; text: string } {
  let last: TagToken | undefined;
  for (const tag of doc.tags) if (tag.name === "extends" || tag.name === "load") last = tag;
  if (last) return { span: { start: last.end, end: last.end }, text: "\n{% load wireview %}" };
  return { span: { start: 0, end: 0 }, text: "{% load wireview %}\n" };
}

// Filters

function filterCompletions(doc: TemplateDoc, project: Project | undefined, range: Span): Completion[] {
  if (!project || !doc.visible) return [];
  return [...doc.visible.filters.values()].map((filter) => ({
    label: filter.name,
    kind: "filter" as const,
    range,
    insert: filter.meta.argument === "required" ? `${filter.name}:` : filter.name,
    detail: filter.library ?? "builtin",
    documentation: filter.meta.docstring ?? undefined,
    retrigger: filter.meta.argument === "required",
  }));
}

// Components

function usedKeys(tag: TagToken, except: Bit | undefined): Set<string> {
  const keys = new Set<string>();
  for (const bit of tag.bits) {
    if (bit === except) continue;
    const kwarg = kwargOf(bit);
    if (kwarg) keys.add(kwarg.key);
  }
  return keys;
}

function keyRange(bit: Bit | undefined, offset: number): Span {
  return bit ? { start: bit.start, end: bit.end } : { start: offset, end: offset };
}

function componentArgumentCompletions(tag: TagToken, bit: Bit | undefined, offset: number, component: ComponentMeta, live: boolean): Completion[] {
  if (bit && /["']/.test(bit.text)) return [];
  const used = usedKeys(tag, bit);
  const range = keyRange(bit, offset);
  const items: Completion[] = [];
  if (!used.has("id")) {
    items.push({
      label: "id=",
      kind: "argument",
      range,
      insert: "id=",
      detail: live ? "required" : "the instance's id",
      sortText: live ? "0id" : "2id",
      retrigger: true,
    });
  }
  for (const [name, field] of Object.entries(component.fields)) {
    if (used.has(name)) continue;
    items.push({
      label: `${name}=`,
      kind: "argument",
      range,
      insert: `${name}=`,
      detail: `${field.type}${field.required ? " (required)" : ""}`,
      documentation: fieldDoc(name, field, component.name),
      sortText: `${field.required ? "0" : "1"}${name}`,
      retrigger: true,
    });
  }
  return items;
}

function functionArgumentCompletions(tag: TagToken, bit: Bit | undefined, offset: number, fc: FunctionComponentMeta): Completion[] {
  if (bit && /["']/.test(bit.text)) return [];
  const used = usedKeys(tag, bit);
  const range = keyRange(bit, offset);
  return Object.entries(fc.parameters)
    .filter(([name, parameter]) => !used.has(name) && parameter.kind !== "VAR_KEYWORD" && parameter.kind !== "VAR_POSITIONAL")
    .map(([name, parameter]) => ({
      label: `${name}=`,
      kind: "argument" as const,
      range,
      insert: `${name}=`,
      detail: `${parameter.type ?? ""}${parameter.has_default ? "" : " (required)"}`.trim(),
      sortText: `${parameter.has_default ? "1" : "0"}${name}`,
      retrigger: true,
    }));
}

// {% on %}

function handlersOf(components: ComponentMeta[]): { name: string; method: MethodMeta; owner: ComponentMeta }[] {
  const found: { name: string; method: MethodMeta; owner: ComponentMeta }[] = [];
  for (const owner of components) {
    for (const [name, method] of Object.entries(owner.methods)) if (method.is_handler) found.push({ name, method, owner });
  }
  return found;
}

function handlerCompletions(env: Env, project: Project, make: (name: string, extra: Partial<Completion> & { kind: CompletionKind }) => Completion): Completion[] {
  const owners = project.owners(env.path);
  if (owners.length) {
    const seen = new Set<string>();
    return handlersOf(owners)
      .filter(({ name }) => !seen.has(name) && Boolean(seen.add(name)))
      .map(({ name, method, owner }) =>
        make(name, { kind: "handler", detail: signature(name, method), documentation: handlerDoc(name, method, owner.name) }),
      );
  }
  // A partial or a stream item: drawn by a component this file does not name
  return handlersOf(project.components).map(({ name, method, owner }) =>
    make(name, {
      kind: "handler",
      detail: `${owner.name} · ${signature(name, method)}`,
      documentation: handlerDoc(name, method, owner.name),
      sortText: `9${name}`,
    }),
  );
}

function onCompletions(doc: TemplateDoc, tag: TagToken, bit: Bit | undefined, index: number, offset: number, prefix: string, env: Env): Completion[] {
  const project = env.project!;
  if (index === 0) return eventCompletions(doc, bit, offset, project);
  if (index === 1) return handlerCompletions(env, project, (name, extra) => literalCompletion(bit, offset, name, extra));
  if (isValuePosition(prefix)) return variableCompletions(doc, offset, env, valueRange(doc, offset, prefix));
  if (bit && /["']/.test(bit.text)) return [];
  const handler = plainLiteral(tag.bits[1])?.value;
  const used = usedKeys(tag, bit);
  const range = keyRange(bit, offset);
  const items: Completion[] = [];
  if (handler) {
    const owners = project.owners(env.path);
    const methods = (owners.length ? owners : project.components).map((owner) => owner.methods[handler]).filter((method) => method?.is_handler);
    const seen = new Set<string>();
    for (const method of methods) {
      for (const [name, parameter] of Object.entries(method.parameters)) {
        if (used.has(name) || seen.has(name) || parameter.kind === "VAR_KEYWORD" || parameter.kind === "VAR_POSITIONAL") continue;
        seen.add(name);
        items.push({ label: `${name}=`, kind: "argument", range, insert: `${name}=`, detail: parameter.type ?? undefined, sortText: `0${name}`, retrigger: true });
      }
    }
  }
  if (!used.has("myself")) {
    items.push({
      label: "myself=True",
      kind: "argument",
      range,
      insert: "myself=True",
      detail: "send the event to this LiveComponent, not its parent",
      sortText: "1myself",
    });
  }
  return items;
}

function eventCompletions(doc: TemplateDoc, bit: Bit | undefined, offset: number, project: Project): Completion[] {
  const literal = literalOf(bit);
  if (!bit || !literal) {
    return DOM_EVENTS.map((event) => literalCompletion(bit, offset, event, { kind: "event", sortText: `${DOM_EVENTS.indexOf(event)}`.padStart(3, "0") }));
  }
  const inside = doc.text.slice(literal.start, offset);
  const segments = inside.split(".");
  const current = segments[segments.length - 1];
  const literalEnd = literal.terminated ? literal.end : bit.end;
  let end = offset;
  while (end < literalEnd && doc.text[end] !== ".") end += 1;
  const range = { start: offset - current.length, end };
  if (segments.length === 1) {
    return DOM_EVENTS.map((event, order) => ({ label: event, kind: "event" as const, range, insert: event, sortText: String(order).padStart(3, "0") }));
  }
  const modifiers = project.metadata.modifiers ?? {};
  // As binding() walks them: a modifier that takes an argument eats the next segment
  let pending: string | null = null;
  const used = new Set<string>();
  for (const segment of segments.slice(1, -1)) {
    if (pending) {
      pending = null;
      continue;
    }
    used.add(segment);
    if (modifiers[segment]?.argument || modifiers[segment]?.has_argument) pending = segment;
  }
  if (pending) {
    const kind = modifiers[pending].argument;
    const values = ARGUMENT_VALUES[pending] ?? ARGUMENT_VALUES[kind ?? ""] ?? [];
    return values.map((value, order) => ({ label: value, kind: "value" as const, range, insert: value, detail: pending!, sortText: String(order).padStart(3, "0") }));
  }
  return Object.entries(modifiers)
    .filter(([name]) => !used.has(name))
    .map(([name, modifier]) => ({
      label: name,
      kind: "modifier" as const,
      range,
      insert: modifier.argument || modifier.has_argument ? `${name}.` : name,
      detail: modifier.argument ? `takes ${modifier.argument === "number" ? "a number" : "a value"}` : undefined,
      documentation: modifier.description,
      retrigger: Boolean(modifier.argument || modifier.has_argument),
    }));
}

// {% fill %}

/** The component or the function component a `*_block` names. */
function blockTarget(project: Project, block: Block): ComponentMeta | FunctionComponentMeta | undefined {
  const name = plainLiteral(block.open.bits[0])?.value;
  if (!name) return undefined;
  return block.open.name === "func_block" ? project.functionComponent(name) : project.component(name);
}

function slotsOf(env: Env, target: ComponentMeta | FunctionComponentMeta): Map<string, Set<string>> {
  const slots = new Map<string, Set<string>>();
  for (const name of Object.keys(target.slots ?? {})) slots.set(name, new Set());
  const text = target.template_path ? env.readFile(target.template_path) : undefined;
  if (text) {
    for (const [name, keys] of renderedSlots(text)) {
      if (!name) continue;
      const known = slots.get(name) ?? new Set<string>();
      for (const key of keys) known.add(key);
      slots.set(name, known);
    }
  }
  return slots;
}

function fillCompletions(doc: TemplateDoc, tag: TagToken, bit: Bit | undefined, index: number, offset: number, env: Env): Completion[] {
  const project = env.project!;
  const host = blocksAround(doc, tag.start).find((block) => SLOT_HOSTS.has(block.open.name) && block.open !== tag);
  if (!host) return [];
  const target = blockTarget(project, host);
  if (!target) return [];
  const slots = slotsOf(env, target);
  const range = keyRange(bit, offset);
  if (index === 0) {
    return [...slots.keys()].map((name) => {
      const config = target.slots?.[name];
      return {
        label: name,
        kind: "slot" as const,
        range,
        insert: name,
        detail: config?.required ? "required" : undefined,
        documentation: config?.doc,
        sortText: `${config?.required ? "0" : "1"}${name}`,
      };
    });
  }
  const slotBit = tag.bits[0];
  const slot = slotBit ? (plainLiteral(slotBit)?.value ?? slotBit.text) : "";
  const used = new Set(tag.bits.filter((candidate) => candidate !== bit).map((candidate) => candidate.text));
  return [...(slots.get(slot) ?? [])]
    .filter((key) => !used.has(`let:${key}`))
    .map((key) => ({ label: `let:${key}`, kind: "variable" as const, range, insert: `let:${key}`, detail: `{% render_slot "${slot}" ${key}=... %}` }));
}

// Variables

function variableTokenCompletions(doc: TemplateDoc, token: VariableToken, offset: number, env: Env): Completion[] {
  const prefix = doc.text.slice(token.contentStart, offset);
  const filter = /\|\s*(\w*)$/.exec(withoutStrings(prefix));
  if (filter) return filterCompletions(doc, env.project, { start: offset - filter[1].length, end: wordEnd(doc.text, offset) });
  if (inString(prefix)) return [];
  return variableCompletions(doc, offset, env, valueRange(doc, offset, prefix));
}

/** Whether text ends inside a quoted string it opened. */
function inString(text: string): boolean {
  return /["']/.test(text.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, ""));
}

function variableCompletions(doc: TemplateDoc, offset: number, env: Env, where: { range: Span; path: string[] }): Completion[] {
  const project = env.project;
  const owners = project ? project.owners(env.path) : [];
  const functionOwners = project ? project.functionOwners(env.path) : [];
  const { range, path } = where;
  const items: Completion[] = [];
  const add = (label: string, detail?: string, documentation?: string, sortText = `1${label}`) => {
    if (!items.some((item) => item.label === label)) items.push({ label, kind: "variable", range, insert: label, detail, documentation, sortText });
  };
  const ownFields = () => {
    for (const owner of owners) {
      for (const [name, field] of Object.entries(owner.fields)) add(name, field.type, fieldDoc(name, field, owner.name), `0${name}`);
      for (const [name, property] of Object.entries(owner.properties ?? {})) {
        add(name, property.type ?? "property", property.docstring ?? undefined, `0${name}`);
      }
    }
  };
  if (path.length) {
    if (path.length === 1 && path[0] === "this") ownFields();
    return items;
  }
  ownFields();
  for (const fc of functionOwners) for (const [name, parameter] of Object.entries(fc.parameters)) add(name, parameter.type ?? undefined, undefined, `0${name}`);
  if (owners.length) for (const [name, doc] of Object.entries(COMPONENT_VARIABLES)) add(name, undefined, doc, `2${name}`);
  for (const name of scopeNames(doc, offset)) add(name.name, name.detail, undefined, `0${name.name}`);
  return items;
}

/** The names the tags around an offset bind: loop variables, `with`, `let:`, and `as` before it. */
export function scopeNames(doc: TemplateDoc, offset: number): { name: string; detail: string }[] {
  const names: { name: string; detail: string }[] = [];
  for (const block of blocksAround(doc, offset)) {
    const bits = block.open.bits;
    if (block.open.name === "for") {
      const at = bits.findIndex((bit) => bit.text === "in");
      for (const bit of bits.slice(0, at === -1 ? 0 : at)) {
        for (const name of bit.text.split(",")) if (name) names.push({ name, detail: "{% for %}" });
      }
      names.push({ name: "forloop", detail: "{% for %}" });
    } else if (block.open.name === "with") {
      if (bits[1]?.text === "as" && bits[2]) names.push({ name: bits[2].text, detail: "{% with %}" });
      for (const bit of bits) {
        const kwarg = kwargOf(bit);
        if (kwarg) names.push({ name: kwarg.key, detail: "{% with %}" });
      }
    } else if (block.open.name === "fill") {
      for (const bit of bits.slice(1)) if (bit.text.startsWith("let:")) names.push({ name: bit.text.slice(4), detail: "{% fill %}" });
    }
  }
  for (const tag of doc.tags) {
    if (tag.end > offset) break;
    const bits = tag.bits;
    if (tag.name !== "with" && bits.length >= 2 && bits[bits.length - 2].text === "as") {
      names.push({ name: bits[bits.length - 1].text, detail: `{% ${tag.name} %}` });
    }
  }
  return names.filter(({ name }) => /^[A-Za-z_]\w*$/.test(name));
}

// Attributes

const ATTRIBUTE_BEFORE = /(?<![\w-])(wire-hook|wire-viewport-top|wire-viewport-bottom|wire-auto-recover)\s*=\s*(["'])([^"']*)$/;

function attributeCompletions(doc: TemplateDoc, offset: number, env: Env): Completion[] {
  const project = env.project;
  if (!project) return [];
  const lineStart = doc.text.lastIndexOf("\n", offset - 1) + 1;
  const masked = maskDjango(doc.text, doc.tokens).slice(lineStart, offset);
  const match = ATTRIBUTE_BEFORE.exec(masked);
  if (!match) return [];
  const word = /\S*$/.exec(match[3])?.[0] ?? "";
  let end = offset;
  while (end < doc.text.length && /[^\s"']/.test(doc.text[end])) end += 1;
  const range = { start: offset - word.length, end };
  if (match[1] === "wire-hook") {
    const used = new Set(match[3].split(/\s+/));
    return Object.entries(project.metadata.hooks ?? {})
      .filter(([name]) => !used.has(name) || name === word)
      .map(([name, hook]) => ({ label: name, kind: "hook" as const, range, insert: name, detail: hook.static_path }));
  }
  return handlerCompletions(env, project, (name, extra) => ({ label: name, range, insert: name, ...extra }));
}
