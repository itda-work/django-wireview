// The extension in Restricted Mode (test/host/run.mjs opens the workspace untrusted
// for this suite): the language and the HTML support work, nothing of the project
// is read and nothing runs.
const assert = require("node:assert/strict");
const path = require("node:path");
const vscode = require("vscode");

const repository = process.env.WIREVIEW_HOST_REPOSITORY;
const ITEM = path.join(repository, "examples", "todo", "templates", "todo", "item.html");

const tests = {
  async "the workspace is not trusted, and the extension says so"() {
    assert.equal(vscode.workspace.isTrusted, false);
    const extension = vscode.extensions.getExtension("itda.django-wireview");
    const exports = await extension.activate();
    // Long enough for a run or a read to have landed
    await new Promise((done) => setTimeout(done, 2000));
    const folder = exports.project(vscode.Uri.file(ITEM));
    assert.equal(folder.state, "restricted", folder.detail);
    assert.equal(folder.project, undefined, "the metadata file the workspace names is not read");
    await vscode.commands.executeCommand("wireview.refreshMetadata");
    assert.equal(folder.project, undefined, "nor on Refresh");
  },

  async "a template still opens as django-html, with HTML completion"() {
    const document = await vscode.workspace.openTextDocument(ITEM);
    assert.equal(document.languageId, "django-html");
    const untitled = await vscode.workspace.openTextDocument({ language: "django-html", content: "{% if a %}\n<di\n{% endif %}" });
    const list = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", untitled.uri, new vscode.Position(1, 3));
    assert.ok(list.items.some((item) => (typeof item.label === "string" ? item.label : item.label.label) === "div"));
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
