// The start tags of an HTML text and their attributes, read the way a browser's
// tokenizer reads them: what is in a comment, in a script or a style, or in an
// end tag never becomes an attribute. The text given is the template with its
// Django syntax masked (mask.ts), so offsets are the template's.
//
// A few lines of the HTML tokenizer rather than vscode-html-languageservice:
// the core modules import nothing but node's own, so that `make test` can run
// them without the npm packages. Where these lines cannot be sure what the
// browser does next (CDATA in SVG, a script that may be double escaped, HTML
// inside SVG), they stop: no tag after that point is read, so nothing there is
// checked. test/markup.test.ts holds them to the HTML standard's parser.

export interface Attribute {
  /** Lower case, as the DOM compares attribute names in an HTML document. */
  name: string;
  nameStart: number;
  /** Null for an attribute written without `=`. As written: character references are not decoded. */
  value: string | null;
  valueStart: number;
}

export interface StartTag {
  name: string;
  start: number;
  end: number;
  /** The first of each name only, as the DOM keeps it. */
  attributes: Attribute[];
}

/** Elements whose content is text up to their own end tag: no tag, no comment in there. */
const RAW_TEXT = new Set(["script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript"]);
/** HTML start tags that end SVG or MathML content. */
const BREAKOUT = new Set(
  (
    "b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 head hr i img li listing menu meta nobr ol p pre " +
    "ruby s small span strong strike sub sup table tt u ul var"
  ).split(" "),
);
/** SVG and MathML elements whose content is HTML again. */
const INTEGRATION_POINTS = new Set(["foreignobject", "desc", "title", "mi", "mo", "mn", "ms", "mtext", "annotation-xml"]);
const SPACE = /[\t\n\f\r ]/;
const LETTER = /[A-Za-z]/;

interface Tag {
  name: string;
  attributes: Attribute[];
  selfClosing: boolean;
  /** Just past the `>`, or null when the text ends inside the tag. */
  end: number | null;
}

/** A tag's name and attributes from just past `<` or `</`. */
function readTag(html: string, from: number): Tag {
  const length = html.length;
  let at = from;
  while (at < length && !SPACE.test(html[at]) && html[at] !== "/" && html[at] !== ">") at += 1;
  const name = html.slice(from, at).toLowerCase();
  const attributes: Attribute[] = [];
  const names = new Set<string>();
  let selfClosing = false;
  for (;;) {
    selfClosing = false;
    while (at < length && (SPACE.test(html[at]) || html[at] === "/")) {
      selfClosing = html[at] === "/";
      at += 1;
    }
    if (at >= length) return { name, attributes, selfClosing: false, end: null };
    if (html[at] === ">") return { name, attributes, selfClosing, end: at + 1 };
    const nameStart = at;
    // The first character may be "=": the tokenizer takes it into the name
    at += 1;
    while (at < length && !SPACE.test(html[at]) && !"/>=".includes(html[at])) at += 1;
    const attribute = html.slice(nameStart, at).toLowerCase();
    // The DOM keeps the first attribute of a name and drops the others
    const keep = !names.has(attribute);
    names.add(attribute);
    let look = at;
    while (look < length && SPACE.test(html[look])) look += 1;
    if (html[look] !== "=") {
      if (keep) attributes.push({ name: attribute, nameStart, value: null, valueStart: at });
      continue;
    }
    look += 1;
    while (look < length && SPACE.test(html[look])) look += 1;
    const quote = html[look];
    if (quote === '"' || quote === "'") {
      const close = html.indexOf(quote, look + 1);
      if (close === -1) return { name, attributes, selfClosing: false, end: null };
      if (keep) attributes.push({ name: attribute, nameStart, value: html.slice(look + 1, close), valueStart: look + 1 });
      at = close + 1;
    } else {
      let end = look;
      while (end < length && !SPACE.test(html[end]) && html[end] !== ">") end += 1;
      if (keep) attributes.push({ name: attribute, nameStart, value: html.slice(look, end), valueStart: look });
      at = end;
    }
  }
}

/** Where a comment that opens at `open` ends: `-->`, `--!>`, or the abrupt `<!-->` and `<!--->`. */
function commentEnd(html: string, open: number): number {
  const body = open + 4;
  if (html.startsWith(">", body)) return body + 1;
  if (html.startsWith("->", body)) return body + 2;
  const plain = html.indexOf("-->", body);
  const bang = html.indexOf("--!>", body);
  if (plain === -1 && bang === -1) return html.length;
  if (bang === -1 || (plain !== -1 && plain < bang)) return plain + 3;
  return bang + 4;
}

/** Where raw text that starts at `from` ends: at its element's end tag, or null when that is not certain. */
function rawTextEnd(html: string, from: number, name: string): number | null {
  const closing = new RegExp(`</${name}[\\t\\n\\f\\r />]`, "ig");
  closing.lastIndex = from;
  const found = closing.exec(html);
  const end = found ? found.index : html.length;
  // A script that holds "<!--" and then "<script" may be double escaped: its first
  // "</script>" would not end it, and where it ends instead is not followed here
  if (name === "script") {
    const escape = html.indexOf("<!--", from);
    if (escape !== -1 && escape < end && /<script[\t\n\f\r />]/i.test(html.slice(escape, end))) return null;
  }
  return end;
}

export function startTags(html: string): StartTag[] {
  const tags: StartTag[] = [];
  const length = html.length;
  /** The SVG and MathML elements open around the position: their content is not HTML's. */
  let foreign: string[] = [];
  let position = 0;
  while (position < length) {
    const open = html.indexOf("<", position);
    if (open === -1) break;
    if (html.startsWith("<!--", open)) {
      position = commentEnd(html, open);
      continue;
    }
    // In SVG or MathML, CDATA is text up to "]]>", and what that text may hold is not followed here
    if (foreign.length && html.startsWith("<![CDATA[", open)) break;
    const next = html[open + 1] ?? "";
    if (next === "/") {
      const after = html[open + 2] ?? "";
      if (after === ">") {
        position = open + 3;
      } else if (LETTER.test(after)) {
        // An end tag: its attributes are read, and dropped
        const tag = readTag(html, open + 2);
        if (tag.end === null) break;
        position = tag.end;
        if (foreign.length) {
          // It closes the SVG or MathML element of its name; one that is not open may close HTML around them
          const at = foreign.lastIndexOf(tag.name);
          if (at === -1) break;
          foreign = foreign.slice(0, at);
        }
      } else if (after === "") {
        break;
      } else {
        const close = html.indexOf(">", open + 2);
        position = close === -1 ? length : close + 1;
      }
      continue;
    }
    // A doctype, a processing instruction, other markup declarations: a bogus comment up to ">"
    if (next === "!" || next === "?") {
      const close = html.indexOf(">", open + 2);
      position = close === -1 ? length : close + 1;
      continue;
    }
    if (!LETTER.test(next)) {
      position = open + 1;
      continue;
    }
    const tag = readTag(html, open + 1);
    // The text ends inside the tag: the browser makes no tag of it
    if (tag.end === null) break;
    const { name } = tag;
    if (foreign.length) {
      // HTML inside an SVG or MathML integration point is not followed here
      if (INTEGRATION_POINTS.has(foreign[foreign.length - 1])) break;
      const breakout = BREAKOUT.has(name) || (name === "font" && tag.attributes.some((a) => ["color", "face", "size"].includes(a.name)));
      if (breakout) foreign = [];
    }
    tags.push({ name, start: open, end: tag.end, attributes: tag.attributes });
    position = tag.end;
    if (foreign.length) {
      // SVG and MathML elements have no raw text, and close themselves with "/>"
      if (!tag.selfClosing) foreign.push(name);
      continue;
    }
    if ((name === "svg" || name === "math") && !tag.selfClosing) {
      foreign = [name];
      continue;
    }
    if (name === "plaintext") break;
    if (RAW_TEXT.has(name)) {
      const end = rawTextEnd(html, position, name);
      if (end === null) break;
      position = end;
    }
  }
  return tags;
}
