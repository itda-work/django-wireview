// The VS Code side: registration, and turning offsets into positions. What to
// offer, what a name is and what is wrong all come from src/core.
import { readFileSync, realpathSync } from "node:fs";

import * as vscode from "vscode";

import tagSnippets from "../data/tag-snippets.json";
import { complete } from "./core/completion.ts";
import type { Completion, CompletionKind } from "./core/completion.ts";
import { diagnose } from "./core/diagnostics.ts";
import type { Severity } from "./core/diagnostics.ts";
import { ownTemplateName } from "./core/env.ts";
import type { Env } from "./core/env.ts";
import { folds } from "./core/folding.ts";
import { pythonLinks, templateLinks } from "./core/links.ts";
import { definition, hover } from "./core/navigation.ts";
import type { Span } from "./core/scan.ts";
import { parseTemplate } from "./core/template.ts";
import type { TemplateDoc } from "./core/template.ts";
import { FolderProject, isFile, SOURCE_SETTINGS } from "./folders.ts";
import { closeTagsOnType, htmlCompletions, htmlFolds, htmlHover, htmlLinkedEditing } from "./html.ts";

const TEMPLATES: vscode.DocumentSelector = [
  { language: "django-html", scheme: "file" },
  { language: "django-html", scheme: "untitled" },
  { language: "html", scheme: "file" },
];
const DJANGO_HTML: vscode.DocumentSelector = [
  { language: "django-html", scheme: "file" },
  { language: "django-html", scheme: "untitled" },
];

const KINDS: Record<CompletionKind, vscode.CompletionItemKind> = {
  tag: vscode.CompletionItemKind.Keyword,
  filter: vscode.CompletionItemKind.Function,
  library: vscode.CompletionItemKind.Module,
  template: vscode.CompletionItemKind.File,
  component: vscode.CompletionItemKind.Class,
  function: vscode.CompletionItemKind.Function,
  argument: vscode.CompletionItemKind.Property,
  event: vscode.CompletionItemKind.Event,
  modifier: vscode.CompletionItemKind.EnumMember,
  value: vscode.CompletionItemKind.Value,
  handler: vscode.CompletionItemKind.Method,
  slot: vscode.CompletionItemKind.Field,
  hook: vscode.CompletionItemKind.Reference,
  variable: vscode.CompletionItemKind.Variable,
};

const SEVERITIES: Record<Severity, vscode.DiagnosticSeverity> = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  information: vscode.DiagnosticSeverity.Information,
};

const folders = new Map<string, FolderProject>();

function folderOf(document: vscode.TextDocument): FolderProject | undefined {
  const folder = vscode.workspace.getWorkspaceFolder(document.uri) ?? vscode.workspace.workspaceFolders?.[0];
  return folder ? folders.get(folder.uri.toString()) : undefined;
}

/** The path the metadata would name this document by: Path.resolve() follows links. */
function realPath(document: vscode.TextDocument): string {
  if (document.uri.scheme !== "file") return document.uri.toString();
  try {
    return realpathSync(document.uri.fsPath);
  } catch {
    return document.uri.fsPath;
  }
}

function envOf(document: vscode.TextDocument): Env {
  const folder = folderOf(document);
  const path = realPath(document);
  return {
    project: folder?.project,
    path,
    documentPath: document.uri.scheme === "file" && path !== document.uri.fsPath ? document.uri.fsPath : undefined,
    readFile: (path) => {
      const open = vscode.workspace.textDocuments.find((candidate) => candidate.uri.scheme === "file" && candidate.uri.fsPath === path);
      if (open) return open.getText();
      try {
        return readFileSync(path, "utf8");
      } catch {
        return undefined;
      }
    },
    isFile,
    templateNames: () => folder?.templateNames() ?? [],
  };
}

const parsed = new WeakMap<vscode.TextDocument, { version: number; project: unknown; doc: TemplateDoc }>();

function templateOf(document: vscode.TextDocument, env: Env): TemplateDoc {
  const cached = parsed.get(document);
  if (cached && cached.version === document.version && cached.project === env.project) return cached.doc;
  const doc = parseTemplate(document.getText(), env.project);
  parsed.set(document, { version: document.version, project: env.project, doc });
  return doc;
}

function toRange(document: vscode.TextDocument, span: Span): vscode.Range {
  return new vscode.Range(document.positionAt(span.start), document.positionAt(span.end));
}

function completionItem(document: vscode.TextDocument, item: Completion): vscode.CompletionItem {
  const result = new vscode.CompletionItem(item.label, KINDS[item.kind]);
  result.range = toRange(document, item.range);
  result.insertText = item.snippet ? new vscode.SnippetString(item.insert) : item.insert;
  result.detail = item.detail;
  if (item.documentation) result.documentation = new vscode.MarkdownString(item.documentation);
  result.sortText = item.sortText;
  result.filterText = item.filterText;
  if (item.edits) result.additionalTextEdits = item.edits.map((edit) => new vscode.TextEdit(toRange(document, edit.span), edit.text));
  if (item.retrigger) result.command = { title: "Suggest", command: "editor.action.triggerSuggest" };
  return result;
}

/** Whether to say anything about a document: an `html` file only when it is in a template directory. */
function diagnosable(document: vscode.TextDocument, env: Env): boolean {
  if (!env.project) return false;
  if (!vscode.workspace.getConfiguration("wireview", document.uri).get("diagnostics.enable", true)) return false;
  if (document.languageId === "django-html") return true;
  if (document.languageId !== "html" || document.uri.scheme !== "file") return false;
  return ownTemplateName(env.project, env) !== undefined;
}

/** What the extension hands to whoever asks for its exports: the host tests. */
export interface Api {
  /** The project a document's folder has, once its metadata is read. */
  project(uri: vscode.Uri): FolderProject | undefined;
}

export function activate(context: vscode.ExtensionContext): Api {
  const output = vscode.window.createOutputChannel("Django Wireview");
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);
  status.command = "wireview.showOutput";
  const diagnostics = vscode.languages.createDiagnosticCollection("wireview");
  context.subscriptions.push(output, status, diagnostics);

  const pending = new Map<string, ReturnType<typeof setTimeout>>();
  const check = (document: vscode.TextDocument) => {
    if (!vscode.languages.match(TEMPLATES, document)) return;
    const env = envOf(document);
    if (!diagnosable(document, env)) {
      diagnostics.delete(document.uri);
      return;
    }
    const doc = templateOf(document, env);
    diagnostics.set(
      document.uri,
      diagnose(doc, env).map((problem) => {
        const diagnostic = new vscode.Diagnostic(toRange(document, problem.span), problem.message, SEVERITIES[problem.severity]);
        diagnostic.code = problem.code;
        diagnostic.source = "wireview";
        return diagnostic;
      }),
    );
  };
  const checkSoon = (document: vscode.TextDocument) => {
    const key = document.uri.toString();
    clearTimeout(pending.get(key));
    pending.set(
      key,
      setTimeout(() => {
        pending.delete(key);
        check(document);
      }, 250),
    );
  };
  const checkAll = () => vscode.workspace.textDocuments.forEach(check);

  const updateStatus = () => {
    const document = vscode.window.activeTextEditor?.document;
    const folder = document ? folderOf(document) : folders.values().next().value;
    if (!folder || folder.state === "off" || folder.state === "idle") {
      status.hide();
      return;
    }
    if (folder.state === "restricted") {
      status.text = "$(shield) Wireview: Restricted Mode";
      status.tooltip = folder.detail;
      status.show();
      return;
    }
    const count = folder.project?.components.length ?? 0;
    status.text =
      folder.state === "running"
        ? "$(sync~spin) Wireview"
        : folder.state === "failed"
          ? `$(warning) Wireview${folder.project ? ` · ${count}` : ""}`
          : `$(symbol-class) Wireview · ${count}`;
    status.tooltip = `${folder.folder.name}: ${folder.detail}`;
    status.show();
  };

  const storage = (context.storageUri ?? context.globalStorageUri).fsPath;
  const addFolder = (folder: vscode.WorkspaceFolder) => {
    const project = new FolderProject(folder, storage, output, () => {
      updateStatus();
      checkAll();
    });
    folders.set(folder.uri.toString(), project);
    void project.start();
  };
  for (const folder of vscode.workspace.workspaceFolders ?? []) addFolder(folder);
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders((event) => {
      for (const folder of event.removed) {
        folders.get(folder.uri.toString())?.dispose();
        folders.delete(folder.uri.toString());
      }
      for (const folder of event.added) addFolder(folder);
    }),
    { dispose: () => folders.forEach((folder) => folder.dispose()) },
  );

  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument(check),
    vscode.workspace.onDidChangeTextDocument((event) => checkSoon(event.document)),
    vscode.workspace.onDidCloseTextDocument((document) => diagnostics.delete(document.uri)),
    vscode.workspace.onDidSaveTextDocument((document) => {
      if (document.languageId === "python") folderOf(document)?.pythonSaved();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (!event.affectsConfiguration("wireview")) return;
      for (const folder of folders.values()) {
        // Another source: what the old one was doing no longer counts
        if (SOURCE_SETTINGS.some((key) => event.affectsConfiguration(`wireview.${key}`, folder.folder.uri))) void folder.configure();
      }
      checkAll();
    }),
    // Restricted Mode ran nothing and read no metadata: now it can
    vscode.workspace.onDidGrantWorkspaceTrust(() => folders.forEach((folder) => void folder.configure())),
    vscode.window.onDidChangeActiveTextEditor(updateStatus),
  );
  const templates = vscode.workspace.createFileSystemWatcher("**/*.html", false, true, false);
  templates.onDidCreate(() => folders.forEach((folder) => folder.templatesChanged()));
  templates.onDidDelete(() => folders.forEach((folder) => folder.templatesChanged()));
  context.subscriptions.push(templates);

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      TEMPLATES,
      {
        provideCompletionItems(document, position) {
          const env = envOf(document);
          const items = complete(templateOf(document, env), document.offsetAt(position), env, { tagSnippets }).map((item) =>
            completionItem(document, item),
          );
          return [...items, ...htmlCompletions(document, position)];
        },
      },
      '"',
      "'",
      " ",
      ".",
      "|",
      "%",
      "{",
      "=",
      ":",
      "<",
    ),
    vscode.languages.registerHoverProvider(TEMPLATES, {
      provideHover(document, position) {
        const env = envOf(document);
        const found = hover(templateOf(document, env), document.offsetAt(position), env);
        if (found) return new vscode.Hover(new vscode.MarkdownString(found.markdown), toRange(document, found.span));
        return htmlHover(document, position);
      },
    }),
    vscode.languages.registerDefinitionProvider(TEMPLATES, {
      provideDefinition(document, position) {
        const env = envOf(document);
        const found = definition(templateOf(document, env), document.offsetAt(position), env);
        return found ? new vscode.Location(vscode.Uri.file(found.path), new vscode.Position(found.line - 1, 0)) : undefined;
      },
    }),
    vscode.languages.registerDocumentLinkProvider(TEMPLATES, {
      provideDocumentLinks(document) {
        const env = envOf(document);
        return templateLinks(templateOf(document, env), env).map((link) => new vscode.DocumentLink(toRange(document, link.span), vscode.Uri.file(link.path)));
      },
    }),
    vscode.languages.registerDocumentLinkProvider(
      { language: "python", scheme: "file" },
      {
        provideDocumentLinks(document) {
          const project = folderOf(document)?.project;
          if (!project) return [];
          return pythonLinks(document.getText(), project, isFile).map(
            (link) => new vscode.DocumentLink(toRange(document, link.span), vscode.Uri.file(link.path)),
          );
        },
      },
    ),
    vscode.languages.registerFoldingRangeProvider(DJANGO_HTML, {
      provideFoldingRanges(document) {
        const env = envOf(document);
        const own = folds(templateOf(document, env)).map((fold) => new vscode.FoldingRange(fold.startLine, fold.endLine));
        return [...own, ...htmlFolds(document)];
      },
    }),
    vscode.languages.registerLinkedEditingRangeProvider(DJANGO_HTML, {
      provideLinkedEditingRanges: (document, position) => htmlLinkedEditing(document, position),
    }),
    closeTagsOnType(),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("wireview.refreshMetadata", async () => {
      const document = vscode.window.activeTextEditor?.document;
      const folder = document ? folderOf(document) : undefined;
      await Promise.all((folder ? [folder] : [...folders.values()]).map((target) => target.refresh()));
    }),
    vscode.commands.registerCommand("wireview.showOutput", () => output.show(true)),
    vscode.commands.registerCommand("wireview.goToComponent", async () => {
      const document = vscode.window.activeTextEditor?.document;
      const folder = (document && folderOf(document)) || [...folders.values()].find((candidate) => candidate.project);
      const project = folder?.project;
      if (!project) {
        void vscode.window.showWarningMessage("No django-wireview metadata yet: see the Django Wireview output.");
        return;
      }
      const picked = await vscode.window.showQuickPick(
        project.components.map((component) => ({
          label: component.name,
          description: component.kind === "live_component" ? "LiveComponent" : "Component",
          detail: component.fqn,
          component,
        })),
        { placeHolder: "Component", matchOnDetail: true },
      );
      if (!picked?.component.file_path) return;
      const line = Math.max(picked.component.line_number - 1, 0);
      await vscode.window.showTextDocument(vscode.Uri.file(picked.component.file_path), {
        selection: new vscode.Range(line, 0, line, 0),
      });
    }),
  );

  checkAll();
  updateStatus();
  return {
    project: (uri) => {
      const folder = vscode.workspace.getWorkspaceFolder(uri);
      return folder ? folders.get(folder.uri.toString()) : undefined;
    },
  };
}

export function deactivate(): void {
  folders.clear();
}
