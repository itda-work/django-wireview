// Smoke tests in a real VS Code (test/host/run.mjs starts it). The features'
// logic is tested under node (test/*.test.ts); these check that the extension
// is wired to the editor: the language, the providers, the diagnostics, the
// HTML support and the metadata runner.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vscode = require("vscode");

const repository = process.env.WIREVIEW_HOST_REPOSITORY;
const ITEM = path.join(repository, "examples", "todo", "templates", "todo", "item.html");
const LIST = path.join(repository, "examples", "todo", "templates", "todo", "list.html");

async function eventually(check, what, timeout = 30000) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeout) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error(`timed out waiting for ${what}${last ? `: ${last.message}` : ""}`);
}

async function api() {
  const extension = vscode.extensions.getExtension("itda.django-wireview");
  assert.ok(extension, "the extension is installed");
  return extension.activate();
}

async function ready(uri) {
  const exports = await api();
  return eventually(() => {
    const folder = exports.project(uri);
    return folder && folder.state === "ok" && folder.project ? folder : undefined;
  }, "the metadata");
}

function positionAfter(document, needle) {
  const offset = document.getText().indexOf(needle);
  assert.notEqual(offset, -1, `${needle} is in ${document.uri.fsPath}`);
  return document.positionAt(offset + needle.length);
}

async function labels(uri, position) {
  const list = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", uri, position);
  return list.items.map((item) => (typeof item.label === "string" ? item.label : item.label.label));
}

const tests = {
  async "a template opens as django-html"() {
    const document = await vscode.workspace.openTextDocument(ITEM);
    assert.equal(document.languageId, "django-html");
    const python = await vscode.workspace.openTextDocument(path.join(repository, "examples", "todo", "live.py"));
    assert.equal(python.languageId, "python", "not every file is a template");
  },

  async "the extension activates and reads the metadata"() {
    const folder = await ready(vscode.Uri.file(ITEM));
    assert.ok(folder.project.component("XTodoItem"));
  },

  async "a handler is completed in {% on %}"() {
    const document = await vscode.workspace.openTextDocument(ITEM);
    await ready(document.uri);
    const found = await labels(document.uri, positionAfter(document, "{% on 'click' '"));
    assert.ok(found.includes("toggle_editing"), found.join(", "));
    assert.ok(found.includes("delete"));
  },

  async "a component name goes to its class"() {
    const document = await vscode.workspace.openTextDocument(LIST);
    await ready(document.uri);
    const locations = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", document.uri, positionAfter(document, "{% component 'XTodo"));
    assert.equal(locations.length, 1);
    const target = locations[0].uri ?? locations[0].targetUri;
    assert.equal(path.basename(target.fsPath), "live.py");
    const source = (await vscode.workspace.openTextDocument(target)).lineAt((locations[0].range ?? locations[0].targetRange).start.line).text;
    assert.match(source, /class XTodoItem/);
  },

  async "a wrong handler is reported, and the report goes when it is fixed"() {
    const document = await vscode.workspace.openTextDocument(ITEM);
    await vscode.window.showTextDocument(document);
    await ready(document.uri);
    const at = document.getText().indexOf("'toggle_editing'") + 1;
    const range = new vscode.Range(document.positionAt(at), document.positionAt(at + "toggle_editing".length));
    const edit = new vscode.WorkspaceEdit();
    edit.replace(document.uri, range, "toggle_editting");
    assert.ok(await vscode.workspace.applyEdit(edit));
    try {
      const diagnostics = await eventually(() => {
        const found = vscode.languages.getDiagnostics(document.uri).filter((d) => d.source === "wireview");
        return found.length ? found : undefined;
      }, "the diagnostic");
      assert.deepEqual(
        diagnostics.map((d) => [d.code, document.getText(d.range)]),
        [["unknown-handler", "toggle_editting"]],
      );
    } finally {
      await vscode.commands.executeCommand("workbench.action.files.revert");
    }
    await eventually(() => vscode.languages.getDiagnostics(document.uri).filter((d) => d.source === "wireview").length === 0, "the report to go");
  },

  async "an untitled django-html document is diagnosed"() {
    await ready(vscode.Uri.file(ITEM));
    const document = await vscode.workspace.openTextDocument({ language: "django-html", content: '{% load wireview %}\n{% component "NoSuchThing" %}\n' });
    const diagnostics = await eventually(() => {
      const found = vscode.languages.getDiagnostics(document.uri);
      return found.length ? found : undefined;
    }, "the diagnostic");
    assert.deepEqual(
      diagnostics.map((d) => d.code),
      ["unknown-component"],
    );
  },

  async "HTML tags are completed in django-html"() {
    const document = await vscode.workspace.openTextDocument({ language: "django-html", content: "{% if a %}\n<di\n{% endif %}" });
    const found = await labels(document.uri, new vscode.Position(1, 3));
    assert.ok(found.includes("div"), found.slice(0, 20).join(", "));
    const attributes = await labels(document.uri, new vscode.Position(0, 0));
    assert.ok(Array.isArray(attributes));
    const attribute = await vscode.workspace.openTextDocument({ language: "django-html", content: "<div wire-h></div>" });
    assert.ok((await labels(attribute.uri, new vscode.Position(0, 11))).includes("wire-hook"));
  },

  async "typing: {% and {# close themselves, a block indents and its end goes back out, > closes an HTML tag"() {
    // Each step in an editor of its own, opened with the text before the keystrokes
    const typed = async (content, keys) => {
      const document = await vscode.workspace.openTextDocument({ language: "django-html", content });
      const editor = await vscode.window.showTextDocument(document);
      const end = document.positionAt(content.length);
      editor.selection = new vscode.Selection(end, end);
      for (const ch of keys) await vscode.commands.executeCommand("type", { text: ch });
      return document;
    };
    const close = () => vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor");

    // `{` closes itself with `}`, so `{%` adds only the `%`
    assert.equal((await typed("", "{%")).getText(), "{%%}");
    await close();
    assert.equal((await typed("", "{#")).getText(), "{##}");
    await close();

    const entered = await typed("<div>\n  {% if a %}", "\n");
    assert.equal(entered.lineAt(2).text, "    ", "the line after {% if %} is indented");
    await close();

    const ended = await typed("{% if a %}\n    ", "{% endif ");
    assert.equal(ended.lineAt(1).text, "{% endif %}", "the end tag goes back out");
    await close();

    const html = await typed("{% if a %}", "<div>");
    await eventually(() => html.getText() === "{% if a %}<div></div>", `</div> after <div> (${JSON.stringify(html.getText())})`, 5000);
    await close();
  },

  async "an .html file in a template directory outside **/templates/** opens as django-html"() {
    const folder = await ready(vscode.Uri.file(ITEM));
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "wireview-dirs-"));
    const views = path.join(fs.realpathSync(scratch), "views");
    fs.mkdirSync(views);
    for (const name of ["page.html", "chosen.html", "back.html"]) fs.writeFileSync(path.join(views, name), "{% if a %}<p>{{ a }}</p>{% endif %}\n");
    const metadata = JSON.parse(fs.readFileSync(process.env.WIREVIEW_HOST_METADATA, "utf8"));
    metadata.template_dirs = [...metadata.template_dirs, views];
    const file = path.join(scratch, "metadata.json");
    fs.writeFileSync(file, JSON.stringify(metadata));
    const config = vscode.workspace.getConfiguration("wireview", vscode.Uri.file(ITEM));
    const files = vscode.workspace.getConfiguration("files", vscode.Uri.file(ITEM));
    await files.update("associations", { "**/views/chosen.html": "html" }, vscode.ConfigurationTarget.Workspace);
    await config.update("metadataPath", file, vscode.ConfigurationTarget.Workspace);
    try {
      await eventually(() => (folder.project.metadata.template_dirs || []).includes(views), "the new metadata");
      const page = await vscode.workspace.openTextDocument(path.join(views, "page.html"));
      await eventually(
        () => vscode.workspace.textDocuments.find((d) => d.uri.fsPath === page.uri.fsPath && d.languageId === "django-html"),
        "page.html as django-html",
      );
      const chosen = await vscode.workspace.openTextDocument(path.join(views, "chosen.html"));
      const back = await eventually(async () => {
        const opened = await vscode.workspace.openTextDocument(path.join(views, "back.html"));
        return opened.languageId === "django-html" ? opened : undefined;
      }, "back.html as django-html");
      // The user switches it back: the next pass leaves it
      await vscode.languages.setTextDocumentLanguage(back, "html");
      await vscode.commands.executeCommand("wireview.refreshMetadata");
      await new Promise((done) => setTimeout(done, 500));
      const now = (document) => vscode.workspace.textDocuments.find((d) => d.uri.fsPath === document.uri.fsPath).languageId;
      assert.equal(now(chosen), "html", "files.associations names it html");
      assert.equal(now(back), "html", "switched back by hand");
    } finally {
      await config.update("metadataPath", process.env.WIREVIEW_HOST_METADATA, vscode.ConfigurationTarget.Workspace);
      await files.update("associations", undefined, vscode.ConfigurationTarget.Workspace);
      await vscode.commands.executeCommand("workbench.action.closeAllEditors");
    }
  },

  async "a line the dev server ran SQL on gets its count, and loses it while edited"() {
    const document = await vscode.workspace.openTextDocument(ITEM);
    await vscode.window.showTextDocument(document);
    await ready(document.uri);
    const line = Number(process.env.WIREVIEW_HOST_QUERIED_LINE) - 1;
    const range = new vscode.Range(0, 0, document.lineCount, 0);
    const hints = await eventually(async () => {
      const found = await vscode.commands.executeCommand("vscode.executeInlayHintProvider", document.uri, range);
      return found.length ? found : undefined;
    }, "the render-part SQL hint");
    assert.equal(hints.length, 1);
    assert.equal(hints[0].position.line, line);
    const label = typeof hints[0].label === "string" ? hints[0].label : hints[0].label.map((part) => part.value).join("");
    assert.equal(label, "⚠ 6× same query");

    const edit = new vscode.WorkspaceEdit();
    edit.insert(document.uri, new vscode.Position(0, 0), " ");
    assert.ok(await vscode.workspace.applyEdit(edit));
    try {
      const edited = await vscode.commands.executeCommand("vscode.executeInlayHintProvider", document.uri, range);
      assert.deepEqual(edited, [], "an unsaved file is not the one the server ran");
    } finally {
      await vscode.commands.executeCommand("workbench.action.files.revert");
    }
  },

  async "the metadata command runs when no file is given"() {
    const folder = await ready(vscode.Uri.file(ITEM));
    const config = vscode.workspace.getConfiguration("wireview", vscode.Uri.file(ITEM));
    await config.update("metadataPath", "", vscode.ConfigurationTarget.Workspace);
    await config.update("metadataCommand", ["uv", "run", "python", "manage.py", "wireview_lsp"], vscode.ConfigurationTarget.Workspace);
    try {
      const before = folder.project;
      await vscode.commands.executeCommand("wireview.refreshMetadata");
      assert.equal(folder.state, "ok", folder.detail);
      assert.notEqual(folder.project, before, "read anew");
      assert.match(folder.detail, /manage\.py wireview_lsp/);
      assert.ok(folder.project.component("XTodoItem"));
    } finally {
      await config.update("metadataCommand", undefined, vscode.ConfigurationTarget.Workspace);
      await config.update("metadataPath", process.env.WIREVIEW_HOST_METADATA, vscode.ConfigurationTarget.Workspace);
    }
  },
};

exports.run = async function run() {
  const failures = [];
  for (const [name, test] of Object.entries(tests)) {
    try {
      await test();
      console.log(`  ok    ${name}`);
    } catch (error) {
      console.log(`  FAIL  ${name}\n${error.stack}`);
      failures.push(name);
    }
  }
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  if (failures.length) throw new Error(`${failures.length} of ${Object.keys(tests).length} failed: ${failures.join("; ")}`);
  console.log(`  ${Object.keys(tests).length} passed`);
};
