// Folding ranges for block tags: from each tag of a block to the next one
// (`{% if %}` to `{% else %}`, `{% else %}` to `{% endif %}`), leaving the next one
// visible. Lines are 0-based, as VS Code counts them.
import type { TemplateDoc } from "./template.ts";

export interface Fold {
  startLine: number;
  endLine: number;
}

export function lineStarts(text: string): number[] {
  const starts = [0];
  for (let i = 0; i < text.length; i++) if (text[i] === "\n") starts.push(i + 1);
  return starts;
}

export function lineAt(starts: number[], offset: number): number {
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (starts[middle] <= offset) low = middle;
    else high = middle - 1;
  }
  return low;
}

export function folds(doc: TemplateDoc): Fold[] {
  const starts = lineStarts(doc.text);
  const found: Fold[] = [];
  for (const block of doc.blocks) {
    if (!block.close) continue;
    const marks = [block.open, ...block.middles, block.close];
    for (let i = 0; i + 1 < marks.length; i++) {
      const startLine = lineAt(starts, marks[i].start);
      const endLine = lineAt(starts, marks[i + 1].start) - 1;
      if (endLine > startLine) found.push({ startLine, endLine });
    }
  }
  return found;
}
