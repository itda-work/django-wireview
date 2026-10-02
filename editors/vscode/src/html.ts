// The HTML features a `django-html` file loses when it stops being `html`: tag and
// attribute completion, hover, closing tags, folding, linked editing. The HTML
// language service gets the template with its Django syntax blanked out, so the
// offsets it answers with are the document's own.
import * as vscode from "vscode";
import { getLanguageService, newHTMLDataProvider } from "vscode-html-languageservice";
import type { CompletionItem as HtmlCompletionItem, HTMLDocument, LanguageService, MarkupContent } from "vscode-html-languageservice";
import { TextDocument } from "vscode-languageserver-textdocument";

import htmlData from "../data/html-data.json";
import { maskDjango } from "./core/mask.ts";
import { scan, tokenAt } from "./core/scan.ts";
import type { Token } from "./core/scan.ts";

const service: LanguageService = getLanguageService({
  customDataProviders: [newHTMLDataProvider("wireview", htmlData as Parameters<typeof newHTMLDataProvider>[1])],
});

interface Masked {
  version: number;
  tokens: Token[];
  document: TextDocument;
  html: HTMLDocument;
}

const cache = new WeakMap<vscode.TextDocument, Masked>();

function masked(document: vscode.TextDocument): Masked {
  const cached = cache.get(document);
  if (cached && cached.version === document.version) return cached;
  const text = document.getText();
  const tokens = scan(text);
  const textDocument = TextDocument.create(document.uri.toString(), "html", document.version, maskDjango(text, tokens));
  const entry = { version: document.version, tokens, document: textDocument, html: service.parseHTMLDocument(textDocument) };
  cache.set(document, entry);
  return entry;
}

function enabled(document: vscode.TextDocument): boolean {
  return document.languageId === "django-html" && vscode.workspace.getConfiguration("wireview", document.uri).get("html.enable", true);
}

function range(value: { start: { line: number; character: number }; end: { line: number; character: number } }): vscode.Range {
  return new vscode.Range(value.start.line, value.start.character, value.end.line, value.end.character);
}

function markdown(value: string | MarkupContent | undefined): string | vscode.MarkdownString | undefined {
  if (value === undefined || typeof value === "string") return value;
  return value.kind === "markdown" ? new vscode.MarkdownString(value.value) : value.value;
}

function completionItem(item: HtmlCompletionItem): vscode.CompletionItem {
  const result = new vscode.CompletionItem(item.label, item.kind === undefined ? undefined : item.kind - 1);
  result.detail = item.detail;
  result.documentation = markdown(item.documentation);
  result.sortText = item.sortText;
  result.filterText = item.filterText;
  const snippet = item.insertTextFormat === 2;
  const edit = item.textEdit;
  const text = edit ? edit.newText : (item.insertText ?? item.label);
  result.insertText = snippet ? new vscode.SnippetString(text) : text;
  if (edit) {
    result.range = "range" in edit ? range(edit.range) : { inserting: range(edit.insert), replacing: range(edit.replace) };
  }
  if (item.command) result.command = { title: item.command.title, command: item.command.command, arguments: item.command.arguments };
  return result;
}

/** Whether an offset is in Django syntax, where HTML has nothing to offer. */
function inDjango(entry: Masked, offset: number): boolean {
  return Boolean(tokenAt(entry.tokens, offset)) || entry.tokens.some((token) => token.kind === "comment" && offset > token.start && offset < token.end);
}

export function htmlCompletions(document: vscode.TextDocument, position: vscode.Position): vscode.CompletionItem[] {
  if (!enabled(document)) return [];
  const entry = masked(document);
  if (inDjango(entry, document.offsetAt(position))) return [];
  return service.doComplete(entry.document, position, entry.html).items.map(completionItem);
}

export function htmlHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
  if (!enabled(document)) return undefined;
  const entry = masked(document);
  if (inDjango(entry, document.offsetAt(position))) return undefined;
  const hover = service.doHover(entry.document, position, entry.html);
  if (!hover) return undefined;
  const contents = Array.isArray(hover.contents) ? hover.contents : [hover.contents];
  const parts = contents
    .map((content) => (typeof content === "string" ? content : "kind" in content ? content.value : content.value))
    .map((value) => new vscode.MarkdownString(value));
  return new vscode.Hover(parts, hover.range ? range(hover.range) : undefined);
}

export function htmlFolds(document: vscode.TextDocument): vscode.FoldingRange[] {
  if (!enabled(document)) return [];
  const entry = masked(document);
  return service.getFoldingRanges(entry.document).map((fold) => new vscode.FoldingRange(fold.startLine, fold.endLine));
}

export function htmlLinkedEditing(document: vscode.TextDocument, position: vscode.Position): vscode.LinkedEditingRanges | undefined {
  if (!enabled(document)) return undefined;
  const entry = masked(document);
  const ranges = service.findLinkedEditingRanges(entry.document, position, entry.html);
  return ranges ? new vscode.LinkedEditingRanges(ranges.map(range)) : undefined;
}

/** `</div>` after `<div>` is typed, and the rest of `</` : what the built-in HTML support does on `>` and `/`. */
export function closeTagsOnType(): vscode.Disposable {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return vscode.workspace.onDidChangeTextDocument((event) => {
    const document = event.document;
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document !== document || !enabled(document) || event.contentChanges.length === 0) return;
    if (!vscode.workspace.getConfiguration("html", document.uri).get("autoClosingTags", true)) return;
    const change = event.contentChanges[event.contentChanges.length - 1];
    const typed = change.text;
    if (change.rangeLength !== 0 || (typed !== ">" && typed !== "/")) return;
    const position = change.range.start.translate(0, 1);
    const version = document.version;
    if (timer) clearTimeout(timer);
    // After the change settles, as the HTML extension does: another keystroke cancels it
    timer = setTimeout(() => {
      timer = undefined;
      if (document.version !== version || editor.document !== document) return;
      const selection = editor.selection.active;
      if (!selection.isEqual(position)) return;
      const entry = masked(document);
      if (inDjango(entry, document.offsetAt(position) - 1)) return;
      const text = service.doTagComplete(entry.document, position, entry.html);
      if (text) void editor.insertSnippet(new vscode.SnippetString(text), position);
    }, 100);
  });
}
