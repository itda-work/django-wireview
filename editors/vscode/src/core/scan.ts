// Reading a Django template the way Django's lexer does: `{% tag %}`, `{{ variable }}`
// and `{# comment #}`, each on one line. Offsets are into the document's text.
//
// Nothing here knows VS Code: `node --test` runs these modules as they are.

export interface Span {
  start: number;
  end: number;
}

/** One whitespace-separated piece of a tag, as `Token.split_contents()` cuts it. */
export interface Bit extends Span {
  text: string;
}

export interface TagToken extends Span {
  kind: "tag";
  /** What stands between the delimiters. */
  contentStart: number;
  contentEnd: number;
  /** False while `%}` is not on the line. Django reads such a tag as text; the editor is still typing it. */
  closed: boolean;
  name: string;
  nameSpan: Span | null;
  /** The pieces after the name. */
  bits: Bit[];
}

export interface VariableToken extends Span {
  kind: "variable";
  contentStart: number;
  contentEnd: number;
  closed: boolean;
}

export interface CommentToken extends Span {
  kind: "comment";
  /** What the span is: a `{# #}` comment, or the body of `{% comment %}` or of `{% verbatim %}`. */
  region: "comment" | "verbatim";
}

export type Token = TagToken | VariableToken | CommentToken;

const OPENER = /\{%|\{\{|\{#/g;
const CLOSER: Record<string, string> = { "{%": "%}", "{{": "}}", "{#": "#}" };
// django.utils.text.smart_split: quoted strings keep their spaces
const BIT = /[^\s'"]*(?:(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')[^\s'"]*)+|\S+/g;

function endOfLine(text: string, from: number): number {
  const newline = text.indexOf("\n", from);
  let end = newline === -1 ? text.length : newline;
  if (end > from && text[end - 1] === "\r") end -= 1;
  return end;
}

export function splitBits(text: string, offset: number): Bit[] {
  const bits: Bit[] = [];
  BIT.lastIndex = 0;
  for (let match = BIT.exec(text); match; match = BIT.exec(text)) {
    bits.push({ text: match[0], start: offset + match.index, end: offset + match.index + match[0].length });
  }
  return bits;
}

export function scan(text: string): Token[] {
  const tokens: Token[] = [];
  let position = 0;
  for (;;) {
    OPENER.lastIndex = position;
    const match = OPENER.exec(text);
    if (!match) break;
    const start = match.index;
    const opener = match[0];
    const lineEnd = endOfLine(text, start);
    const closeAt = text.indexOf(CLOSER[opener], start + 2);
    const closed = closeAt !== -1 && closeAt + 2 <= lineEnd;

    let contentEnd: number;
    let end: number;
    if (closed) {
      contentEnd = closeAt;
      end = closeAt + 2;
    } else {
      // Text to Django. It runs to the next opener, which Django would still read
      OPENER.lastIndex = start + 2;
      const next = OPENER.exec(text);
      end = next && next.index < lineEnd ? next.index : lineEnd;
      contentEnd = end;
      // What the editor closed on its own while the tag is being typed: `{% name }`
      while (contentEnd > start + 2 && "}%#".includes(text[contentEnd - 1])) contentEnd -= 1;
    }
    position = Math.max(end, start + 2);

    if (opener === "{#") {
      tokens.push({ kind: "comment", start, end, region: "comment" });
      continue;
    }
    if (opener === "{{") {
      tokens.push({ kind: "variable", start, end, contentStart: start + 2, contentEnd, closed });
      continue;
    }

    const bits = splitBits(text.slice(start + 2, contentEnd), start + 2);
    const first = bits.shift();
    const tag: TagToken = {
      kind: "tag",
      start,
      end,
      contentStart: start + 2,
      contentEnd,
      closed,
      name: first ? first.text : "",
      nameSpan: first ? { start: first.start, end: first.end } : null,
      bits,
    };
    tokens.push(tag);

    if (closed && (tag.name === "comment" || tag.name === "verbatim")) {
      // Nothing between here and the end tag is a tag
      const suffix = tag.name === "verbatim" && bits.length ? `\\s+${escapeRegExp(bits[0].text)}` : "";
      const ending = new RegExp(`\\{%\\s*end${tag.name}${suffix}\\s*%\\}`, "g");
      ending.lastIndex = end;
      const found = ending.exec(text);
      const until = found ? found.index : text.length;
      if (until > end) tokens.push({ kind: "comment", start: end, end: until, region: tag.name === "verbatim" ? "verbatim" : "comment" });
      position = until;
    }
  }
  return tokens;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The tag or the variable the offset is inside of: after its opening delimiter, up to its closing one. */
export function tokenAt(tokens: Token[], offset: number): TagToken | VariableToken | undefined {
  for (const token of tokens) {
    if (token.kind === "comment") continue;
    if (offset >= token.contentStart && offset <= token.contentEnd) return token;
    if (token.start > offset) break;
  }
  return undefined;
}

export function inComment(tokens: Token[], offset: number): boolean {
  return tokens.some((token) => token.kind === "comment" && offset > token.start && offset < token.end);
}

/** A quoted string at the start of a bit. The span is what is between the quotes. */
export interface Literal extends Span {
  value: string;
  /** False while the closing quote is not typed yet. */
  terminated: boolean;
  /** What follows the closing quote in the same bit: a filter, usually. */
  rest: string;
}

const LITERAL = /^(["'])((?:\\.|(?!\1)[^\\])*)(\1)?/s;

export function literalOf(bit: Bit | undefined): Literal | undefined {
  if (!bit) return undefined;
  const match = LITERAL.exec(bit.text);
  if (!match) return undefined;
  const start = bit.start + 1;
  return {
    value: match[2],
    start,
    end: start + match[2].length,
    terminated: Boolean(match[3]),
    rest: bit.text.slice(match[0].length),
  };
}

/** `name=value` in a tag. */
export interface Kwarg {
  key: string;
  keySpan: Span;
  value: Bit;
}

const KWARG = /^([A-Za-z_]\w*)=/;

export function kwargOf(bit: Bit): Kwarg | undefined {
  const match = KWARG.exec(bit.text);
  if (!match) return undefined;
  const valueStart = bit.start + match[0].length;
  return {
    key: match[1],
    keySpan: { start: bit.start, end: bit.start + match[1].length },
    value: { text: bit.text.slice(match[0].length), start: valueStart, end: bit.end },
  };
}

export function kwargsOf(bits: Bit[]): Kwarg[] {
  const found: Kwarg[] = [];
  for (const bit of bits) {
    const kwarg = kwargOf(bit);
    if (kwarg) found.push(kwarg);
  }
  return found;
}

/** The text with every string literal blanked out, same length: what is left is syntax. */
export function withoutStrings(text: string): string {
  return text.replace(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/g, (found) => found[0] + " ".repeat(found.length - 2) + found[0]);
}
