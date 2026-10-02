// The start tags of an HTML text and their attributes, read the way a browser's
// tokenizer reads them: what is in a comment, in a script or a style, or in an
// end tag never becomes an attribute. The text given is the template with its
// Django syntax masked (mask.ts), so offsets are the template's.
//
// A few lines of the HTML tokenizer rather than vscode-html-languageservice:
// the core modules import nothing but node's own, so that `make test` can run
// them without the npm packages.

export interface Attribute {
  /** Lower case, as the DOM compares attribute names in an HTML document. */
  name: string;
  nameStart: number;
  /** Null for an attribute written without `=`. */
  value: string | null;
  valueStart: number;
}

export interface StartTag {
  name: string;
  start: number;
  end: number;
  attributes: Attribute[];
}

/** Elements whose content is text up to their own end tag: no tag, no comment in there. */
const RAW_TEXT = new Set(["script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes"]);
const SPACE = /[\t\n\f\r ]/;

export function startTags(html: string): StartTag[] {
  const tags: StartTag[] = [];
  const length = html.length;
  let position = 0;
  while (position < length) {
    const open = html.indexOf("<", position);
    if (open === -1) break;
    if (html.startsWith("<!--", open)) {
      const close = html.indexOf("-->", open + 4);
      position = close === -1 ? length : close + 3;
      continue;
    }
    const next = html[open + 1] ?? "";
    // A doctype, a processing instruction or an end tag: what it holds is no attribute
    if (next === "!" || next === "?" || next === "/") {
      const close = html.indexOf(">", open + 2);
      position = close === -1 ? length : close + 1;
      continue;
    }
    if (!/[A-Za-z]/.test(next)) {
      position = open + 1;
      continue;
    }
    let at = open + 1;
    while (at < length && !SPACE.test(html[at]) && html[at] !== "/" && html[at] !== ">") at += 1;
    const name = html.slice(open + 1, at).toLowerCase();
    const attributes: Attribute[] = [];
    for (;;) {
      while (at < length && (SPACE.test(html[at]) || html[at] === "/")) at += 1;
      if (at >= length || html[at] === ">") break;
      const nameStart = at;
      // The first character may be "=": the tokenizer takes it into the name
      at += 1;
      while (at < length && !SPACE.test(html[at]) && !"/>=".includes(html[at])) at += 1;
      const attribute = html.slice(nameStart, at).toLowerCase();
      let look = at;
      while (look < length && SPACE.test(html[look])) look += 1;
      if (html[look] !== "=") {
        attributes.push({ name: attribute, nameStart, value: null, valueStart: at });
        continue;
      }
      look += 1;
      while (look < length && SPACE.test(html[look])) look += 1;
      const quote = html[look];
      if (quote === '"' || quote === "'") {
        const close = html.indexOf(quote, look + 1);
        const end = close === -1 ? length : close;
        attributes.push({ name: attribute, nameStart, value: html.slice(look + 1, end), valueStart: look + 1 });
        at = close === -1 ? length : close + 1;
      } else {
        let end = look;
        while (end < length && !SPACE.test(html[end]) && html[end] !== ">") end += 1;
        attributes.push({ name: attribute, nameStart, value: html.slice(look, end), valueStart: look });
        at = end;
      }
    }
    const end = Math.min(at + 1, length);
    tags.push({ name, start: open, end, attributes });
    position = end;
    if (name === "plaintext") break;
    if (RAW_TEXT.has(name)) {
      const closing = new RegExp(`</${name}[\\t\\n\\f\\r />]`, "ig");
      closing.lastIndex = position;
      const found = closing.exec(html);
      position = found ? found.index : length;
    }
  }
  return tags;
}
