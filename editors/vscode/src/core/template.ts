// A template read once: its tokens, what it loads and how its block tags nest.
import type { Load, Project, TagEntry, Visible } from "./project.ts";
import { scan } from "./scan.ts";
import type { TagToken, Token } from "./scan.ts";

export interface Block {
  open: TagToken;
  /** What the opening tag is where it stands. */
  entry: TagEntry;
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
  /** In the order they stand. */
  loads: LoadTag[];
  /** What the end of the template sees. Undefined without metadata: nothing is known about tags then. */
  visible: Visible | undefined;
  /** What a tag sees after the first n loads: index n. */
  steps: Visible[];
  blocks: Block[];
  /** The block each tag stands in. A block's own start, middle and end stand in the block around it. */
  enclosing: Map<TagToken, Block | null>;
  /** The tags that end or continue a block. */
  structural: Set<TagToken>;
  problems: StructureProblem[];
}

/** A library one `{% load %}` brings in, and the tag that does. */
export interface LoadTag extends Load {
  tag: TagToken;
}

export function readLoads(tags: TagToken[]): LoadTag[] {
  const loads: LoadTag[] = [];
  for (const tag of tags) {
    if (tag.name !== "load") continue;
    const names = tag.bits.map((bit) => bit.text);
    if (names.length >= 3 && names[names.length - 2] === "from") {
      loads.push({ library: names[names.length - 1], names: names.slice(0, -2), tag });
    } else {
      for (const library of names) loads.push({ library, names: null, tag });
    }
  }
  return loads;
}

/**
 * What the template sees at an offset: the builtins and the loads before it. Django
 * parses in order and a `{% load %}` adds to the parser from there on, so a tag
 * before its load is one Django does not know.
 */
export function visibleAt(doc: TemplateDoc, offset: number): Visible | undefined {
  if (!doc.visible) return undefined;
  let before = 0;
  while (before < doc.loads.length && doc.loads[before].tag.start < offset) before += 1;
  return doc.steps[before];
}

/** What a tag is where it stands, if anything there knows its name. */
export function tagEntry(doc: TemplateDoc, tag: TagToken): TagEntry | undefined {
  return visibleAt(doc, tag.start)?.tags.get(tag.name);
}

/** Whether a library is loaded, whole, before the offset. */
export function loadedBefore(doc: TemplateDoc, library: string, offset: number): boolean {
  return doc.loads.some((load) => load.library === library && load.names === null && load.tag.start < offset);
}

export function parseTemplate(text: string, project: Project | undefined): TemplateDoc {
  const tokens = scan(text);
  const tags = tokens.filter((token): token is TagToken => token.kind === "tag" && token.closed);
  const loads = readLoads(tags);
  const known = project?.knowsTags ? project : undefined;
  const steps = known ? loads.map((_, count) => known.visible(loads.slice(0, count))) : [];
  const visible = known?.visible(loads);
  if (visible) steps.push(visible);
  const doc: TemplateDoc = {
    text,
    tokens,
    tags,
    loads,
    visible,
    steps,
    blocks: [],
    enclosing: new Map(),
    structural: new Set(),
    problems: [],
  };
  if (!visible) return doc;

  let top: Block | null = null;
  for (const tag of tags) {
    const here = visibleAt(doc, tag.start)!;
    const entry = here.tags.get(tag.name);
    if (entry?.meta.end) {
      doc.enclosing.set(tag, top);
      const block: Block = { open: tag, entry, close: null, middles: [], parent: top };
      doc.blocks.push(block);
      top = block;
      continue;
    }
    // An end is matched by the block that waits for it (parse_until), loaded or not
    let closing: Block | null = top;
    while (closing && closing.entry.meta.end !== tag.name) closing = closing.parent;
    if (!entry && (closing || visible.ends.has(tag.name))) {
      // Django closes the innermost block and fails when that is not the one this ends.
      // Whatever is still open inside the one it does end was never closed.
      if (closing) {
        for (let open: Block | null = top; open && open !== closing; open = open.parent) {
          doc.problems.push({ kind: "unclosed", tag: open.open, expected: open.entry.meta.end ?? "" });
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
    if (!entry && top && top.entry.meta.intermediate.includes(tag.name)) {
      top.middles.push(tag);
      doc.structural.add(tag);
      doc.enclosing.set(tag, top.parent);
      continue;
    }
    doc.enclosing.set(tag, top);
  }
  for (let open: Block | null = top; open; open = open.parent) {
    doc.problems.push({ kind: "unclosed", tag: open.open, expected: open.entry.meta.end ?? "" });
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
