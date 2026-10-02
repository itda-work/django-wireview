import { strict as assert } from "node:assert";
import test from "node:test";

import { folds } from "../src/core/folding.ts";
import { pythonLinks, templateLinks } from "../src/core/links.ts";
import { definition, hover } from "../src/core/navigation.ts";
import { findFieldLine, renderedSlots } from "../src/core/source.ts";
import { cursor, DIR, env, FILES, LIST, LIVE_PY, parse, project } from "./fixture.ts";

function hoverAt(source: string, path = LIST) {
  const { text, offset } = cursor(source);
  return hover(parse(text), offset, env(path));
}

function definitionAt(source: string, path = LIST) {
  const { text, offset } = cursor(source);
  return definition(parse(text), offset, env(path));
}

const W = "{% load wireview %}";

test("hover: a component, its argument, a handler, a modifier, a filter, a tag", () => {
  assert.match(hoverAt(`${W}{% component "Ca▮rd" %}`)!.markdown, /\*\*Card\*\* · Component/);
  assert.match(hoverAt(`${W}{% component "TodoList" ti▮tle=x %}`)!.markdown, /title: str.*TodoList[\s\S]*Required/);
  assert.match(hoverAt(`${W}{% on "click" "a▮dd" %}`)!.markdown, /async def add\(text: str\)/);
  assert.match(hoverAt(`${W}{% on "click.pre▮vent" "add" %}`)!.markdown, /preventDefault/);
  assert.match(hoverAt("{{ x|def▮ault:1 }}")!.markdown, /takes an argument/);
  assert.match(hoverAt("{% load i18n %}{% blocktrans▮late %}{% endblocktranslate %}")!.markdown, /load i18n/);
  assert.match(hoverAt("{% if a %}{% end▮if %}")!.markdown, /Part of `{% if %}` on line 1/);
  assert.match(hoverAt("{{ rem▮aining }}")!.markdown, /remaining: int.*TodoList[\s\S]*Items left/);
  assert.match(hoverAt('<i wire-hook="Ch▮art">')!.markdown, /app\/hooks\/chart.js/);
  assert.equal(hoverAt("{{ unknown▮ }}"), undefined);
});

test("definition: the class, the method, the field's line, the hook's line", () => {
  assert.deepEqual(definitionAt(`${W}{% component "Todo▮List" title=1 %}`), { path: LIVE_PY, line: 10 });
  assert.deepEqual(definitionAt(`${W}{% on "click" "toggle▮All" %}`), { path: LIVE_PY, line: 25 });
  assert.deepEqual(definitionAt(`${W}{% component "TodoList" ite▮ms=1 %}`), { path: LIVE_PY, line: 14 });
  assert.deepEqual(definitionAt('<i wire-hook="Cha▮rt">'), { path: "/proj/app/static/app/hooks/chart.js", line: 4 });
  assert.deepEqual(definitionAt("{{ this.ti▮tle }}"), { path: LIVE_PY, line: 13 });
  assert.deepEqual(definitionAt("{{ remai▮ning }}"), { path: LIVE_PY, line: 30 });
});

test("definition: a library, a tag, a template", () => {
  assert.deepEqual(definitionAt("{% load wire▮view %}"), { path: "/site/wireview/templatetags/wireview.py", line: 1 });
  assert.deepEqual(definitionAt(`${W}{% tag_hea▮der %}`), { path: "/site/wireview/templatetags/wireview.py", line: 1 });
  assert.deepEqual(definitionAt('{% extends "ba▮se.html" %}'), { path: `${DIR}/base.html`, line: 1 });
  assert.equal(definitionAt('{% extends "no▮pe.html" %}'), undefined);
  assert.deepEqual(definitionAt("{% if a %}\n{% end▮if %}"), { path: LIST, line: 1 });
});

test("a field's line: the class's own statement, not a method's local of the same name", () => {
  const source = FILES[LIVE_PY];
  assert.equal(findFieldLine(source, 10, "title"), 13);
  assert.equal(findFieldLine(source, 10, "items"), 14);
  // Declared by a base class: the class line
  assert.equal(findFieldLine(source, 10, "count"), 10);
  const multiline = ["class A(", "    Base,", "):", "    n: int = 0"].join("\n");
  assert.equal(findFieldLine(multiline, 1, "n"), 4);
  assert.equal(findFieldLine("class A:\n    x == 1\n", 1, "x"), 1, "a comparison is not a declaration");
});

test("the slots a template renders, with what each passes to let:", () => {
  const slots = renderedSlots(FILES[`${DIR}/card.html`]);
  assert.deepEqual([...slots.keys()], ["header", "footer", ""]);
  assert.deepEqual([...slots.get("footer")!], ["note"]);
});

test("links: extends and include, and template names in Python", () => {
  const text = '{% extends "base.html" %}{% include "nope.html" %}';
  const links = templateLinks(parse(text), env(LIST));
  assert.deepEqual(links.map((link) => [text.slice(link.span.start, link.span.end), link.path]), [["base.html", `${DIR}/base.html`]]);

  const python = 'class A:\n    template_name = "card.html"\n    other = "nope.html"\nrender(r, \'todo/list.html\')';
  const found = pythonLinks(python, project, (path) => path in FILES);
  assert.deepEqual(found.map((link) => python.slice(link.span.start, link.span.end)), ["card.html", "todo/list.html"]);
});

test("folding: each part of a closed block", () => {
  const doc = parse("{% if a %}\n1\n2\n{% else %}\n3\n{% endif %}\n{% for x in y %}\n");
  assert.deepEqual(folds(doc), [
    { startLine: 0, endLine: 2 },
    { startLine: 3, endLine: 4 },
  ]);
});
