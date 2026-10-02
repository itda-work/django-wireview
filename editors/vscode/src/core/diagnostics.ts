// Problems in a template: each one an error that Django or django-wireview would
// raise when it parses or renders the template, said before it does.
//
// The rule for every check here: when it cannot be sure, it says nothing. A
// false alarm costs more than a missed one, because a user who sees one turns the
// diagnostics off. So only quoted names are checked (a variable could hold
// anything), a tag Django reads as text is left alone, and nothing is said
// without the project's metadata.
import { ownTemplateName } from "./env.ts";
import type { Env } from "./env.ts";
import type { ComponentMeta, FunctionComponentMeta, MethodMeta, ParameterMeta } from "./metadata.ts";
import { isWireviewTag } from "./project.ts";
import type { Project } from "./project.ts";
import type { Span, TagToken } from "./scan.ts";
import { kwargOf } from "./scan.ts";
import { argumentBits, plainLiteral, SLOT_HOSTS, symbols } from "./symbols.ts";
import type { Sym } from "./symbols.ts";
import type { TemplateDoc } from "./template.ts";

export type Severity = "error" | "warning" | "information";

export interface Problem {
  code: string;
  severity: Severity;
  message: string;
  span: Span;
}

/** `wire-on-<this>`: what `event_transpiler.binding()` accepts. */
const BINDING_NAME = /^[A-Za-z][A-Za-z0-9_:-]*(\.[A-Za-z0-9_:-]+)*$/;
/** Names a component takes that are not its fields: `id`, and what the framework fills in. */
const FRAMEWORK_ARGUMENTS = new Set(["id", "user", "session"]);
/** Tags whose arguments Django reads with its filter syntax; another library's tag may read `|` its own way. */
const DJANGO_FILE = /[\\/](?:django|wireview)[\\/]/;
const NOT_FILTERED = new Set(["cond", "class"]);
/** What `{% filter %}` refuses: the autoescape tag does their work. */
const NOT_IN_FILTER_TAG = new Set(["escape", "safe"]);

export function diagnose(doc: TemplateDoc, env: Env): Problem[] {
  const project = env.project;
  if (!project) return [];
  const problems: Problem[] = [];
  const owners = project.owners(env.path);
  const report = (code: string, severity: Severity, span: Span, message: string) =>
    problems.push({ code, severity, span, message });

  if (doc.visible) {
    checkTags(doc, project, report);
    for (const problem of doc.problems) {
      const span = problem.tag.nameSpan ?? problem.tag;
      if (problem.kind === "unclosed") {
        report("unclosed-block", "error", span, `'${problem.tag.name}' is not closed: '{% ${problem.expected} %}' is missing.`);
      } else {
        report("unmatched-end", "error", span, `'${problem.tag.name}' closes no block that is open here.`);
      }
    }
  }

  for (const sym of symbols(doc)) {
    // A tag Django reads as text: what it holds is text too
    if ("tag" in sym && sym.tag && !sym.tag.closed) continue;
    switch (sym.kind) {
      case "filter":
        // A variable Django reads as text holds no filter either
        if (doc.visible && sym.closed) checkFilter(doc, project, sym, report);
        break;
      case "library":
        if (project.knowsTags && !project.libraries[sym.name]) {
          const known = Object.keys(project.libraries).sort().join(", ");
          report("unknown-library", "error", sym.span, `'${sym.name}' is not a registered tag library. The libraries are: ${known}.`);
        }
        break;
      case "template":
        checkTemplate(project, env, sym, report);
        break;
      case "component":
        if (wireviewTag(doc, sym.tag)) checkComponent(doc, project, sym, report);
        break;
      case "function-component":
        if (wireviewTag(doc, sym.tag)) checkFunctionComponent(doc, project, sym, report);
        break;
      case "event":
        if (wireviewTag(doc, sym.tag)) checkEvent(project, sym, report);
        break;
      case "handler":
        if (owners.length && (!sym.tag || wireviewTag(doc, sym.tag))) checkHandler(owners, sym, report);
        break;
      case "handler-argument":
        if (owners.length && wireviewTag(doc, sym.tag)) checkHandlerArgument(owners, sym, report);
        break;
      case "hook":
        if (!project.metadata.hooks?.[sym.name]) {
          report(
            "unknown-hook",
            "information",
            sym.span,
            `No hook file registers '${sym.name}'. A hook registered elsewhere is fine; one in static/<app_label>/hooks/*.js is loaded for you.`,
          );
        }
        break;
    }
  }
  return problems;
}

type Report = (code: string, severity: Severity, span: Span, message: string) => void;

function wireviewTag(doc: TemplateDoc, tag: TagToken): boolean {
  return isWireviewTag(doc.visible?.tags.get(tag.name));
}

function checkTags(doc: TemplateDoc, project: Project, report: Report): void {
  const visible = doc.visible!;
  for (const tag of doc.tags) {
    if (!tag.name || !tag.nameSpan) continue;
    if (visible.tags.has(tag.name) || doc.structural.has(tag)) continue;
    // An end or a middle out of its place: maybe in a block whose end tag the metadata could not read
    if (visible.ends.has(tag.name) || visible.intermediates.has(tag.name)) continue;
    const libraries = project.librariesWithTag(tag.name);
    if (libraries.length) {
      const load = libraries.map((library) => `{% load ${library} %}`).join(" or ");
      report("tag-not-loaded", "error", tag.nameSpan, `'${tag.name}' is in a library this template does not load: add ${load}.`);
      continue;
    }
    // The end of a block whose end tag the metadata could not read
    if (tag.name.startsWith("end")) continue;
    report("unknown-tag", "warning", tag.nameSpan, `'${tag.name}' is not a tag Django knows here: no library registers it.`);
  }
}

function checkFilter(doc: TemplateDoc, project: Project, sym: Extract<Sym, { kind: "filter" }>, report: Report): void {
  if (sym.tag) {
    const entry = doc.visible!.tags.get(sym.tag.name);
    if (!entry || NOT_FILTERED.has(sym.tag.name) || !DJANGO_FILE.test(entry.meta.file_path)) return;
  }
  if (sym.tag?.name === "filter" && NOT_IN_FILTER_TAG.has(sym.name)) {
    report("filter-not-permitted", "error", sym.span, `{% filter %} does not take '${sym.name}': use {% autoescape %} instead.`);
    return;
  }
  const filter = doc.visible!.filters.get(sym.name);
  if (!filter) {
    const libraries = project.librariesWithFilter(sym.name);
    if (libraries.length) {
      const load = libraries.map((library) => `{% load ${library} %}`).join(" or ");
      report("filter-not-loaded", "error", sym.span, `The filter '${sym.name}' is in a library this template does not load: add ${load}.`);
    } else {
      report("unknown-filter", "error", sym.span, `'${sym.name}' is not a filter Django knows here: no library registers it.`);
    }
    return;
  }
  if (filter.meta.argument === "required" && !sym.hasArgument) {
    report("filter-argument", "error", sym.span, `The filter '${sym.name}' needs an argument: '${sym.name}:value'.`);
  } else if (filter.meta.argument === "none" && sym.hasArgument) {
    report("filter-argument", "error", sym.span, `The filter '${sym.name}' takes no argument.`);
  }
}

function checkTemplate(project: Project, env: Env, sym: Extract<Sym, { kind: "template" }>, report: Report): void {
  const name = relativeTemplateName(project, env, sym.name);
  if (name === undefined) return;
  if (!project.metadata.template_dirs?.length || project.resolveTemplate(name, env.isFile)) return;
  report(
    "template-not-found",
    "warning",
    sym.span,
    `No template directory has '${sym.name}'. (A loader that does not read the file system could still find it.)`,
  );
}

/** A template name as Django resolves it: `./x.html` and `../x.html` are relative to this template's own name. */
export function relativeTemplateName(project: Project, env: Env, name: string): string | undefined {
  if (!name.startsWith("./") && !name.startsWith("../")) return name;
  const own = ownTemplateName(project, env);
  if (own === undefined) return undefined;
  const parts = own.split("/").slice(0, -1);
  for (const part of name.split("/")) {
    if (part === ".") continue;
    if (part === "..") {
      if (!parts.length) return undefined;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.join("/");
}

function passedKeys(tag: TagToken, skip: number): Map<string, Span> {
  const keys = new Map<string, Span>();
  for (const bit of argumentBits(tag).slice(skip)) {
    const kwarg = kwargOf(bit);
    if (kwarg) keys.set(kwarg.key, kwarg.keySpan);
  }
  return keys;
}

function checkComponent(doc: TemplateDoc, project: Project, sym: Extract<Sym, { kind: "component" }>, report: Report): void {
  const component = project.component(sym.name);
  if (!component) {
    report("unknown-component", "error", sym.span, `No component is registered as '${sym.name}'.`);
    return;
  }
  const keys = passedKeys(sym.tag, 1);
  if (sym.live) {
    if (component.kind !== "live_component") {
      report(
        "not-a-live-component",
        "error",
        sym.span,
        `'${sym.name}' is a Component, not a LiveComponent: render it with {% component %}.`,
      );
      return;
    }
    if (!keys.has("id")) {
      report("live-component-needs-id", "error", sym.tag.nameSpan ?? sym.span, `{% ${sym.tag.name} %} needs an id: id="...".`);
    }
  }
  if (!component.accepts_extra_kwargs) checkArguments(component.name, component.fields, keys, sym, report);
  if (SLOT_HOSTS.has(sym.tag.name) && project.metadata.components?.[sym.name] === component) {
    // The tag checks required slots only for a name it finds as written (Component._all)
    checkSlots(doc, sym.tag, sym.span, sym.name, component.slots, report);
  }
}

function checkArguments(
  name: string,
  fields: ComponentMeta["fields"],
  keys: Map<string, Span>,
  sym: Extract<Sym, { kind: "component" }>,
  report: Report,
): void {
  for (const [key, span] of keys) {
    if (fields[key] || FRAMEWORK_ARGUMENTS.has(key)) continue;
    report("unknown-argument", "warning", span, `'${name}' has no field '${key}': the value is dropped.`);
  }
  const missing = Object.entries(fields)
    .filter(([key, field]) => field.required && !keys.has(key))
    .map(([key]) => key);
  if (missing.length) {
    const list = missing.map((key) => `'${key}'`).join(", ");
    report(
      "missing-argument",
      "warning",
      sym.span,
      `'${name}' requires ${list}. Unless an instance with this id already holds ${missing.length > 1 ? "them" : "it"}, rendering fails.`,
    );
  }
}

function checkFunctionComponent(doc: TemplateDoc, project: Project, sym: Extract<Sym, { kind: "function-component" }>, report: Report): void {
  const fc = project.functionComponent(sym.name);
  if (!fc) {
    report("unknown-function-component", "error", sym.span, `No function component is registered as '${sym.name}'.`);
    return;
  }
  const keys = passedKeys(sym.tag, 1);
  const parameters = Object.entries(fc.parameters).filter(([, parameter]) => !isVariadic(parameter));
  const takesAny = Object.values(fc.parameters).some((parameter) => parameter.kind === "VAR_KEYWORD");
  if (!takesAny) {
    for (const [key, span] of keys) {
      if (!fc.parameters[key]) report("unknown-argument", "warning", span, `'${fc.name}' has no parameter '${key}'.`);
    }
  }
  const missing = parameters.filter(([key, parameter]) => !parameter.has_default && !keys.has(key)).map(([key]) => key);
  if (missing.length) {
    report("missing-argument", "warning", sym.span, `'${fc.name}' requires ${missing.map((key) => `'${key}'`).join(", ")}.`);
  }
  if (sym.tag.name === "func_block") checkSlots(doc, sym.tag, sym.span, fc.name, fc.slots, report);
}

function isVariadic(parameter: ParameterMeta): boolean {
  return parameter.kind === "VAR_KEYWORD" || parameter.kind === "VAR_POSITIONAL";
}

/** Required slots no `{% fill %}` of the block fills. Only the block's own fills count, as `_extract_slots` reads them. */
function checkSlots(
  doc: TemplateDoc,
  open: TagToken,
  span: Span,
  name: string,
  slots: ComponentMeta["slots"] | FunctionComponentMeta["slots"],
  report: Report,
): void {
  const block = doc.blocks.find((candidate) => candidate.open === open);
  if (!block || !block.close) return;
  const filled = new Set<string>();
  for (const inner of doc.blocks) {
    if (inner.open.name !== "fill" || inner.parent !== block) continue;
    const bit = inner.open.bits[0];
    if (bit) filled.add(plainLiteral(bit)?.value ?? bit.text);
  }
  for (const [slot, config] of Object.entries(slots ?? {})) {
    if (config?.required && !filled.has(slot)) {
      report("missing-slot", "error", span, `'${name}' requires the slot '${slot}': add {% fill ${slot} %}...{% endfill %}.`);
    }
  }
}

function checkEvent(project: Project, sym: Extract<Sym, { kind: "event" }>, report: Report): void {
  const text = sym.text;
  if (!BINDING_NAME.test(text)) {
    report(
      "invalid-event",
      "error",
      sym.span,
      `'${text}' is not an event binding: use letters, digits, '_', ':' and '-', with modifiers after dots, like 'keyup.enter'.`,
    );
    return;
  }
  const modifiers = project.metadata.modifiers ?? {};
  if (!Object.keys(modifiers).length) return;
  // binding()'s walk: a modifier that takes an argument eats the segment after it
  const segments: { text: string; span: Span }[] = [];
  let offset = sym.span.start;
  for (const segment of text.split(".")) {
    segments.push({ text: segment, span: { start: offset, end: offset + segment.length } });
    offset += segment.length + 1;
  }
  // binding() refuses it anywhere after the event, before it walks the modifiers: even as a key's argument
  const inline = segments.findIndex((segment, index) => index > 0 && segment.text === "inlinejs");
  if (inline !== -1) {
    report("unknown-modifier", "error", segments[inline].span, "The inlinejs modifier is not supported: use a JS() command chain, or a hook.");
    return;
  }
  for (let index = 1; index < segments.length; index++) {
    const { text: name, span } = segments[index];
    const modifier = modifiers[name];
    if (!modifier) {
      report(
        "unknown-modifier",
        "error",
        span,
        `'${name}' is not a modifier. A key by its name is key.<name>, like keydown.key.Escape. The modifiers are: ${Object.keys(modifiers).join(", ")}.`,
      );
      return;
    }
    const kind = modifier.argument ?? (modifier.has_argument ? "text" : null);
    if (!kind) continue;
    const argument = segments[index + 1];
    if (!argument) {
      report("modifier-argument", "error", span, `'${name}' needs an argument after it, like ${exampleOf(name)}.`);
      return;
    }
    if (kind === "number" && !/^[0-9]+$/.test(argument.text)) {
      report("modifier-argument", "error", argument.span, `'${name}' takes a whole number, and '${argument.text}' is not one: like ${exampleOf(name)}.`);
      return;
    }
    index += 1;
  }
}

function exampleOf(modifier: string): string {
  return { key: "keydown.key.Escape", key_code: "keydown.key_code.27" }[modifier] ?? `input.${modifier}.300`;
}

function checkHandler(owners: ComponentMeta[], sym: Extract<Sym, { kind: "handler" }>, report: Report): void {
  const found = owners.map((owner) => owner.methods[sym.name]).filter((method): method is MethodMeta => Boolean(method));
  const names = owners.map((owner) => owner.name).join(", ");
  if (!found.length) {
    report("unknown-handler", "error", sym.span, `${names} has no method '${sym.name}'.`);
  } else if (!found.some((method) => method.is_handler)) {
    report(
      "not-a-handler",
      "error",
      sym.span,
      `'${sym.name}' is not an event handler of ${names}: the framework owns the name, or it starts with '_'.`,
    );
  }
}

function checkHandlerArgument(owners: ComponentMeta[], sym: Extract<Sym, { kind: "handler-argument" }>, report: Report): void {
  const handlers = owners.map((owner) => owner.methods[sym.handler]).filter((method) => method?.is_handler);
  if (!handlers.length) return;
  const accepts = handlers.some(
    (method) =>
      Object.values(method.parameters).some((parameter) => parameter.kind === "VAR_KEYWORD") ||
      (sym.key in method.parameters && !isVariadic(method.parameters[sym.key])),
  );
  if (!accepts) {
    report("unknown-handler-argument", "warning", sym.span, `'${sym.handler}' takes no argument '${sym.key}': the event is refused.`);
  }
}
