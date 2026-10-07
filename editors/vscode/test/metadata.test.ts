// Metadata 2.0 names the framework's methods on a component and describes them
// once at the top (#162); 1.1 listed every method on every component. The
// extension reads both, and must answer the same about either.
import { strict as assert } from "node:assert";
import test from "node:test";

import { complete } from "../src/core/completion.ts";
import { diagnose } from "../src/core/diagnostics.ts";
import { expandMethods } from "../src/core/metadata.ts";
import { definition, hover } from "../src/core/navigation.ts";
import { Project } from "../src/core/project.ts";
import { COUNTER, cursor, DIR, env, legacyMetadata, LIST, metadata, parse, PARTIAL, PYDANTIC_MAIN, WIREVIEW_COMPONENT } from "./fixture.ts";

const W = "{% load wireview %}";
const current = new Project(metadata());
const legacy = new Project(legacyMetadata());

function answers(project: Project, source: string, path: string) {
  const { text, offset } = cursor(source);
  const doc = parse(text, project);
  const at = env(path, project);
  return {
    problems: diagnose(doc, at),
    hover: hover(doc, offset, at),
    definition: definition(doc, offset, at),
    completions: complete(doc, offset, at).map((item) => [item.label, item.detail, item.documentation]),
  };
}

test("2.0 and 1.1 of the same project answer alike", () => {
  const sources: [string, string][] = [
    [`${W}<b {% on "click" "mod▮el_dump" %}>`, LIST],
    [`${W}<b {% on "click" "join▮ed" %}>`, LIST],
    [`${W}<b {% on "click" "join▮ed" %}>`, PARTIAL],
    [`${W}<b {% on "click" "▮" %}>`, LIST],
    [`${W}<b {% on "click" "add" ▮ %}>`, LIST],
    [`${W}<b {% on "click" "incr▮ement" by=2 %}>`, COUNTER],
    [`${W}<b {% on "click" "▮" %}>`, PARTIAL],
    [`${W}<b {% on "click" "no▮pe" %}>`, LIST],
    [`${W}<b {% on "click" "model_dump" mo▮de="json" %}>`, LIST],
    [`${W}<b {% on "click" "model_dump" ▮ %}>`, LIST],
    [`${W}<b {% on "click" "join▮ed" %}>`, `${DIR}/card.html`],
  ];
  for (const [source, path] of sources) {
    assert.deepEqual(answers(current, source, path), answers(legacy, source, path), source);
  }
});

test("a framework method a component inherits is not a handler, and says where it is", () => {
  const { problems, hover: shown, definition: target } = answers(current, `${W}<b {% on "click" "mod▮el_dump" %}>`, LIST);
  assert.deepEqual(
    problems.map((problem) => problem.code),
    ["not-a-handler"],
    "unknown-handler would say the component has no such method",
  );
  assert.equal(
    shown!.markdown,
    '```python\ndef model_dump(mode: str = "python")\n```\n\nDefined on `TodoList`.\n\nGenerate a dictionary representation of the model.',
  );
  assert.deepEqual(target, { path: PYDANTIC_MAIN, line: 400 });
  const argument = answers(current, `${W}<b {% on "click" "model_dump" mo▮de="json" %}>`, LIST);
  assert.equal(argument.hover!.markdown, "`mode: str` · argument of `model_dump`");
  assert.deepEqual(argument.definition, { path: PYDANTIC_MAIN, line: 400 });
});

test("an inherited method keeps what the top says of it, its parameters' kind and default too", () => {
  for (const project of [current, legacy]) {
    for (const name of ["TodoList", "Counter", "Card", "Flexible"]) {
      const method = project.component(name)!.methods.model_dump;
      assert.deepEqual(method.parameters, { mode: { type: "str", default: "python", has_default: true, kind: "KEYWORD_ONLY" } }, name);
      assert.deepEqual(
        [method.is_handler, method.is_async, method.docstring, method.file_path, method.line_number],
        [false, false, "Generate a dictionary representation of the model.", PYDANTIC_MAIN, 400],
        name,
      );
    }
    const joined = project.component("Card")!.methods.joined;
    assert.deepEqual([joined.file_path, joined.line_number, joined.docstring], [WIREVIEW_COMPONENT, 120, "Called when the component joins."]);
  }
});

test("a component's own override of a framework name is its own", () => {
  // TodoList overrides joined; Card inherits it
  assert.deepEqual(answers(current, `${W}<b {% on "click" "join▮ed" %}>`, LIST).definition?.path, "/proj/app/live.py");
  const card = current.component("Card")!;
  assert.equal(card.methods.joined.file_path, WIREVIEW_COMPONENT);
  assert.equal(card.methods.joined.is_handler, false);
});

test("expanding leaves 1.x as it is and the input unchanged", () => {
  const old = legacyMetadata();
  assert.equal(expandMethods(old), old);
  const input = metadata();
  const before = JSON.stringify(input);
  const expanded = expandMethods(input);
  assert.equal(JSON.stringify(input), before);
  // One object for every component that inherits it
  assert.equal(expanded.components.Card.methods.model_dump, expanded.components.TodoList.methods.model_dump);
});

test("a name the top does not describe is left out rather than made up", () => {
  const input = metadata();
  input.components.Card.inherited_methods = { "pydantic.main.BaseModel": ["model_dump", "no_such_method"] };
  const card = expandMethods(input).components.Card;
  assert.deepEqual(Object.keys(card.methods), ["model_dump"]);
});
