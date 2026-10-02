// What names a template mentions, and where: tags, filters, libraries, template
// paths, components and their arguments, events, handlers, slots, hooks and
// variables. Hover, go to definition and the diagnostics all read this one list,
// so they agree on what a piece of text is.
import { startTags } from "./markup.ts";
import { maskDjango } from "./mask.ts";
import { kwargOf, literalOf, withoutStrings } from "./scan.ts";
import type { Bit, Span, TagToken, Token, VariableToken } from "./scan.ts";
import type { Block, TemplateDoc } from "./template.ts";

export const COMPONENT_TAGS = new Set(["component", "component_block"]);
export const LIVE_COMPONENT_TAGS = new Set(["live_component", "live_component_block"]);
export const FUNCTION_TAGS = new Set(["func", "func_block"]);
/** The block tags whose body holds `{% fill %}`s for the component they name. */
export const SLOT_HOSTS = new Set(["component_block", "live_component_block", "func_block"]);

/** Attributes whose value names a handler of the component that draws them. */
export const HANDLER_ATTRIBUTES = new Set(["wire-viewport-top", "wire-viewport-bottom", "wire-auto-recover"]);

/** Tags whose arguments are not Django expressions: a `|` there is not a filter. */
const NOT_EXPRESSIONS = new Set(["cond", "class", "comment", "verbatim", "load", "templatetag", "lorem", "now", "filter"]);
/** Tags whose every argument is an expression; the others only in `key=value`. */
const EXPRESSION_TAGS = new Set(["if", "elif", "firstof"]);
const KEYWORDS = new Set(["and", "or", "not", "in", "is", "as", "with", "only", "True", "False", "None", "reversed", "by"]);

export type Sym =
  | { kind: "tag"; span: Span; name: string; tag: TagToken }
  | { kind: "filter"; span: Span; name: string; hasArgument: boolean; tag: TagToken | null; closed: boolean }
  | { kind: "library"; span: Span; name: string; tag: TagToken }
  | { kind: "loaded-name"; span: Span; name: string; library: string; tag: TagToken }
  | { kind: "template"; span: Span; name: string; tag: TagToken }
  | { kind: "component"; span: Span; name: string; tag: TagToken; live: boolean }
  | { kind: "function-component"; span: Span; name: string; tag: TagToken }
  | { kind: "argument"; span: Span; key: string; tag: TagToken; target: string; family: "component" | "function" }
  | { kind: "event"; span: Span; text: string; tag: TagToken }
  | { kind: "modifier"; span: Span; name: string; tag: TagToken }
  | { kind: "handler"; span: Span; name: string; tag: TagToken | null }
  | { kind: "handler-argument"; span: Span; key: string; handler: string; tag: TagToken }
  | { kind: "slot"; span: Span; name: string; tag: TagToken; host: TagToken | null }
  | { kind: "hook"; span: Span; name: string }
  | { kind: "variable"; span: Span; path: string[] };

/** The literal a bit is, when it is nothing but a quoted string: a name Django will look up as written. */
export function plainLiteral(bit: Bit | undefined): { value: string; span: Span } | undefined {
  const literal = literalOf(bit);
  if (!literal || !literal.terminated || literal.rest) return undefined;
  return { value: literal.value, span: { start: literal.start, end: literal.end } };
}

/** The bits that are arguments to a simple tag: up to an `as name` at the end. */
export function argumentBits(tag: TagToken): Bit[] {
  const bits = tag.bits;
  if (bits.length >= 2 && bits[bits.length - 2].text === "as") return bits.slice(0, -2);
  return bits;
}

/** The `*_block` a tag stands in, nearest first: whose fills it is. */
export function hostBlock(doc: TemplateDoc, tag: TagToken): Block | null {
  for (let block = doc.enclosing.get(tag) ?? null; block; block = block.parent) {
    if (SLOT_HOSTS.has(block.open.name)) return block;
  }
  return null;
}

const symbolCache = new WeakMap<TemplateDoc, Sym[]>();

export function symbols(doc: TemplateDoc): Sym[] {
  const cached = symbolCache.get(doc);
  if (cached) return cached;
  const found: Sym[] = [];
  for (const token of doc.tokens) {
    if (token.kind === "tag") tagSymbols(doc, token, found);
    else if (token.kind === "variable") variableSymbols(doc.text, token, found);
  }
  attributeSymbols(doc.text, doc.tokens, found);
  found.sort((a, b) => a.span.start - b.span.start);
  symbolCache.set(doc, found);
  return found;
}

/** The symbol under an offset: the innermost one whose span holds it, ends included. */
export function symbolAt(doc: TemplateDoc, offset: number): Sym | undefined {
  let best: Sym | undefined;
  for (const sym of symbols(doc)) {
    if (sym.span.start > offset) break;
    if (offset <= sym.span.end && (!best || sym.span.end - sym.span.start <= best.span.end - best.span.start)) best = sym;
  }
  return best;
}

function tagSymbols(doc: TemplateDoc, tag: TagToken, found: Sym[]): void {
  if (!tag.nameSpan) return;
  found.push({ kind: "tag", span: tag.nameSpan, name: tag.name, tag });
  const name = tag.name;
  if (!NOT_EXPRESSIONS.has(name)) expressionSymbols(doc.text, tag, found);

  if (name === "filter") {
    // do_filter compiles "var|" + its arguments: a chain whose first filter has no bar before it
    if (tag.bits.length) filterSymbols(doc.text, tag.bits[0].start, tag.contentEnd, tag, tag.closed, found, true);
    return;
  }
  if (name === "load") {
    const bits = tag.bits;
    if (bits.length >= 3 && bits[bits.length - 2].text === "from") {
      const library = bits[bits.length - 1];
      found.push({ kind: "library", span: library, name: library.text, tag });
      for (const bit of bits.slice(0, -2)) found.push({ kind: "loaded-name", span: bit, name: bit.text, library: library.text, tag });
    } else {
      for (const bit of bits) found.push({ kind: "library", span: bit, name: bit.text, tag });
    }
    return;
  }
  if (name === "extends" || name === "include") {
    const path = plainLiteral(tag.bits[0]);
    if (path) found.push({ kind: "template", span: path.span, name: path.value, tag });
    return;
  }
  if (COMPONENT_TAGS.has(name) || LIVE_COMPONENT_TAGS.has(name) || FUNCTION_TAGS.has(name)) {
    const target = plainLiteral(tag.bits[0]);
    if (!target) return;
    const family = FUNCTION_TAGS.has(name) ? "function" : "component";
    if (family === "function") found.push({ kind: "function-component", span: target.span, name: target.value, tag });
    else found.push({ kind: "component", span: target.span, name: target.value, tag, live: LIVE_COMPONENT_TAGS.has(name) });
    for (const bit of argumentBits(tag).slice(1)) {
      const kwarg = kwargOf(bit);
      if (kwarg) found.push({ kind: "argument", span: kwarg.keySpan, key: kwarg.key, tag, target: target.value, family });
    }
    return;
  }
  if (name === "on") {
    eventSymbols(tag, found);
    const handler = plainLiteral(tag.bits[1]);
    if (!handler) return;
    found.push({ kind: "handler", span: handler.span, name: handler.value, tag });
    for (const bit of argumentBits(tag).slice(2)) {
      const kwarg = kwargOf(bit);
      if (kwarg && kwarg.key !== "myself") {
        found.push({ kind: "handler-argument", span: kwarg.keySpan, key: kwarg.key, handler: handler.value, tag });
      }
    }
    return;
  }
  if (name === "fill") {
    const bit = tag.bits[0];
    if (!bit) return;
    // Quoted or not: the tag takes the quotes off
    const literal = plainLiteral(bit);
    const span = literal ? literal.span : { start: bit.start, end: bit.end };
    const host = tag.closed ? hostBlock(doc, tag) : null;
    found.push({ kind: "slot", span, name: literal ? literal.value : bit.text, tag, host: host?.open ?? null });
    return;
  }
  if (name === "render_slot") {
    const slot = plainLiteral(tag.bits[0]);
    if (slot) found.push({ kind: "slot", span: slot.span, name: slot.value, tag, host: null });
  }
}

function eventSymbols(tag: TagToken, found: Sym[]): void {
  const literal = plainLiteral(tag.bits[0]);
  if (!literal) return;
  found.push({ kind: "event", span: literal.span, text: literal.value, tag });
  let offset = literal.span.start;
  const segments = literal.value.split(".");
  offset += segments[0].length + 1;
  for (const segment of segments.slice(1)) {
    found.push({ kind: "modifier", span: { start: offset, end: offset + segment.length }, name: segment, tag });
    offset += segment.length + 1;
  }
}

/** Filters and variables in a tag's arguments. */
function expressionSymbols(text: string, tag: TagToken, found: Sym[]): void {
  filterSymbols(text, tag.contentStart, tag.contentEnd, tag, tag.closed, found);
  const bits = tag.bits;
  if (EXPRESSION_TAGS.has(tag.name)) {
    variablesIn(text, bits[0]?.start ?? tag.contentEnd, tag.contentEnd, found);
    return;
  }
  if (tag.name === "for") {
    const at = bits.findIndex((bit) => bit.text === "in");
    if (at !== -1 && bits[at + 1]) variablesIn(text, bits[at + 1].start, tag.contentEnd, found);
    return;
  }
  if (tag.name === "with" && bits[1]?.text === "as") variablesIn(text, bits[0].start, bits[0].end, found);
  for (const bit of bits) {
    const kwarg = kwargOf(bit);
    if (kwarg) variablesIn(text, kwarg.value.start, kwarg.value.end, found);
  }
}

function variableSymbols(text: string, token: VariableToken, found: Sym[]): void {
  filterSymbols(text, token.contentStart, token.contentEnd, null, token.closed, found);
  variablesIn(text, token.contentStart, token.contentEnd, found);
}

// `|name` with an argument when `:` follows at once, as FilterExpression reads it
const FILTER = /\|\s*([A-Za-z_]\w*)(:)?/g;

/** `closed`: whether Django reads the tag or variable they stand in, or takes it for text. */
function filterSymbols(
  text: string,
  start: number,
  end: number,
  tag: TagToken | null,
  closed: boolean,
  found: Sym[],
  chain = false,
): void {
  // A bare chain reads as if a bar stood before it; offsets stay the text's
  const lead = chain ? 1 : 0;
  const source = withoutStrings((chain ? "|" : "") + text.slice(start, end));
  FILTER.lastIndex = 0;
  for (let match = FILTER.exec(source); match; match = FILTER.exec(source)) {
    const nameStart = start - lead + match.index + match[0].indexOf(match[1]);
    found.push({
      kind: "filter",
      span: { start: nameStart, end: nameStart + match[1].length },
      name: match[1],
      hasArgument: Boolean(match[2]),
      tag,
      closed,
    });
  }
}

const NAME = /[A-Za-z_]\w*(?:\.\w+)*/g;

function variablesIn(text: string, start: number, end: number, found: Sym[]): void {
  if (end <= start) return;
  const source = withoutStrings(text.slice(start, end));
  NAME.lastIndex = 0;
  for (let match = NAME.exec(source); match; match = NAME.exec(source)) {
    const before = source.slice(0, match.index).trimEnd();
    // A filter's name, a number's tail, an attribute of something else, a keyword
    if (before.endsWith("|") || /[\w.]$/.test(source.slice(0, match.index))) continue;
    const path = match[0].split(".");
    if (KEYWORDS.has(path[0]) || /^\d/.test(path[0])) continue;
    // `key=` in a tag: the key is not a variable (`a==b` compares one)
    if (/^=(?!=)/.test(source.slice(match.index + match[0].length))) continue;
    const at = start + match.index;
    found.push({ kind: "variable", span: { start: at, end: at + path[0].length }, path: [path[0]] });
    if (path[0] === "this" && path[1]) {
      const second = at + path[0].length + 1;
      found.push({ kind: "variable", span: { start: second, end: second + path[1].length }, path: ["this", path[1]] });
    }
  }
}

const NAMED_ATTRIBUTES = new Set(["wire-hook", ...HANDLER_ATTRIBUTES]);

/** `wire-hook` and the attributes that name a handler: on the start tags of the HTML around the Django syntax. */
export function attributeValues(text: string, tokens: Token[]): { name: string; value: string; start: number }[] {
  const values: { name: string; value: string; start: number }[] = [];
  for (const tag of startTags(maskDjango(text, tokens))) {
    for (const attribute of tag.attributes) {
      if (attribute.value === null || !NAMED_ATTRIBUTES.has(attribute.name)) continue;
      // The value as written: what the mask blanked out is a value the template computes
      const start = attribute.valueStart;
      values.push({ name: attribute.name, value: text.slice(start, start + attribute.value.length), start });
    }
  }
  return values;
}

function attributeSymbols(text: string, tokens: Token[], found: Sym[]): void {
  for (const { name, value, start } of attributeValues(text, tokens)) {
    // A value a template computes is not a name to check
    if (value.includes("{")) continue;
    if (name === "wire-hook") {
      const word = /\S+/g;
      for (let match = word.exec(value); match; match = word.exec(value)) {
        found.push({ kind: "hook", span: { start: start + match.index, end: start + match.index + match[0].length }, name: match[0] });
      }
    } else if (value.trim()) {
      const leading = value.length - value.trimStart().length;
      const handler = value.trim();
      found.push({ kind: "handler", span: { start: start + leading, end: start + leading + handler.length }, name: handler, tag: null });
    }
  }
}
