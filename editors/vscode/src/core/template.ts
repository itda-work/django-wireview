// A template read once: its tokens, what it loads and how its block tags nest.
import type { Loads, Project, Visible } from "./project.ts";
import { scan } from "./scan.ts";
import type { TagToken, Token } from "./scan.ts";

export interface Block {
  open: TagToken;
  /** Null while nothing closes it. */
  close: TagToken | null;
  /** `{% else %}`, `{% empty %}` and the like, in order. */
  middles: TagToken[];
  parent: Block | null;
}

export interface StructureProblem {
  kind: "unclosed" | "unmatched-end";
  tag: TagToken;
  /** For an unclosed block: the end tag it waits for. */
  expected?: string;
}

export interface TemplateDoc {
  text: string;
  tokens: Token[];
  /** The tags Django reads as tags: the ones closed on their line. */
  tags: TagToken[];
  loads: Loads;
  /** Undefined without metadata: nothing is known about tags then. */
  visible: Visible | undefined;
  blocks: Block[];
  /** The block each tag stands in. A block's own start, middle and end stand in the block around it. */
  enclosing: Map<TagToken, Block | null>;
  /** The tags that end or continue a block. */
  structural: Set<TagToken>;
  problems: StructureProblem[];
}

export function readLoads(tags: TagToken[]): Loads {
  const loads: Loads = { full: new Set(), partial: new Map() };
  for (const tag of tags) {
    if (tag.name !== "load") continue;
    const names = tag.bits.map((bit) => bit.text);
    if (names.length >= 3 && names[names.length - 2] === "from") {
      const library = names[names.length - 1];
      const picked = loads.partial.get(library) ?? new Set<string>();
      for (const name of names.slice(0, -2)) picked.add(name);
      loads.partial.set(library, picked);
    } else {
      for (const name of names) loads.full.add(name);
    }
  }
  return loads;
}

export function parseTemplate(text: string, project: Project | undefined): TemplateDoc {
  const tokens = scan(text);
  const tags = tokens.filter((token): token is TagToken => token.kind === "tag" && token.closed);
  const loads = readLoads(tags);
  const visible = project?.knowsTags ? project.visible(loads) : undefined;
  const doc: TemplateDoc = {
    text,
    tokens,
    tags,
    loads,
    visible,
    blocks: [],
    enclosing: new Map(),
    structural: new Set(),
    problems: [],
  };
  if (!visible) return doc;

  let top: Block | null = null;
  for (const tag of tags) {
    const entry = visible.tags.get(tag.name);
    if (entry?.meta.end) {
      doc.enclosing.set(tag, top);
      const block: Block = { open: tag, close: null, middles: [], parent: top };
      doc.blocks.push(block);
      top = block;
      continue;
    }
    if (!entry && visible.ends.has(tag.name)) {
      // Django closes the innermost block and fails when that is not the one this ends.
      // Whatever is still open inside the one it does end was never closed.
      let closing: Block | null = top;
      while (closing && visible.tags.get(closing.open.name)?.meta.end !== tag.name) closing = closing.parent;
      if (closing) {
        for (let open: Block | null = top; open && open !== closing; open = open.parent) {
          doc.problems.push({ kind: "unclosed", tag: open.open, expected: visible.tags.get(open.open.name)?.meta.end ?? "" });
        }
        closing.close = tag;
        top = closing.parent;
        doc.structural.add(tag);
        doc.enclosing.set(tag, top);
      } else {
        doc.problems.push({ kind: "unmatched-end", tag });
        doc.structural.add(tag);
        doc.enclosing.set(tag, top);
      }
      continue;
    }
    if (!entry && top && visible.tags.get(top.open.name)?.meta.intermediate.includes(tag.name)) {
      top.middles.push(tag);
      doc.structural.add(tag);
      doc.enclosing.set(tag, top.parent);
      continue;
    }
    doc.enclosing.set(tag, top);
  }
  for (let open: Block | null = top; open; open = open.parent) {
    doc.problems.push({ kind: "unclosed", tag: open.open, expected: visible.tags.get(open.open.name)?.meta.end ?? "" });
  }
  return doc;
}

/** The innermost block whose body holds the offset. */
export function blockAt(doc: TemplateDoc, offset: number): Block | null {
  let innermost: Block | null = null;
  for (const block of doc.blocks) {
    if (block.open.end > offset) break;
    if (block.close && block.close.start < offset) continue;
    innermost = block;
  }
  return innermost;
}

/** The blocks around an offset, innermost first. */
export function blocksAround(doc: TemplateDoc, offset: number): Block[] {
  const chain: Block[] = [];
  for (let block = blockAt(doc, offset); block; block = block.parent) chain.push(block);
  return chain;
}
