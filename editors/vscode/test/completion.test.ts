import { strict as assert } from "node:assert";
import test from "node:test";

import { complete } from "../src/core/completion.ts";
import type { Completion } from "../src/core/completion.ts";
import { COUNTER, cursor, DIR, env, LIST, parse, PARTIAL } from "./fixture.ts";

const SNIPPETS = { if: "if ${1:condition}", component: 'component "$1"' };

function at(source: string, path = LIST): { items: Completion[]; text: string; offset: number } {
  const { text, offset } = cursor(source);
  return { items: complete(parse(text), offset, env(path), { tagSnippets: SNIPPETS }), text, offset };
}

function labels(source: string, path = LIST): string[] {
  return at(source, path).items.map((item) => item.label);
}

function applied(source: string, label: string, path = LIST): string {
  const { items, text } = at(source, path);
  const item = items.find((candidate) => candidate.label === label);
  assert.ok(item, `${label} not offered: ${items.map((candidate) => candidate.label).join(", ")}`);
  let result = text.slice(0, item.range.start) + item.insert + text.slice(item.range.end);
  for (const edit of [...(item.edits ?? [])].sort((a, b) => b.span.start - a.span.start)) {
    if (edit.span.start >= item.range.end) continue;
    result = result.slice(0, edit.span.start) + edit.text + result.slice(edit.span.end);
  }
  return result;
}

const W = "{% load wireview %}";

test("nothing in a comment", () => {
  assert.deepEqual(labels("{# {% ▮ #}"), []);
  assert.deepEqual(labels("{% comment %}{% ▮ %}{% endcomment %}"), []);
});

test("tag names: what the template sees, and what the block it is in waits for first", () => {
  const { items } = at("{% if a %}{% ▮ %}");
  const sorted = [...items].sort((a, b) => (a.sortText ?? "").localeCompare(b.sortText ?? ""));
  assert.deepEqual(sorted.slice(0, 3).map((item) => item.label), ["endif", "elif", "else"]);
  assert.ok(items.some((item) => item.label === "for"));
  assert.ok(!items.some((item) => item.label === "blocktranslate"), "not loaded");
});

test("a block tag typed alone goes in whole, with its end, over the %} that is there", () => {
  assert.equal(applied("{% i▮ %}", "if"), "{% if ${1:condition} %}\n\t$0\n{% endif %}");
  assert.equal(applied("{%▮%}", "for"), "{% for %}\n\t$0\n{% endfor %}");
  assert.equal(applied("{% ▮", "csrf_token"), "{% csrf_token %}$0");
});

test("a tag with arguments already: only its name changes", () => {
  assert.equal(applied("{% i▮ a %}", "if"), "{% if a %}");
});

test("wireview's tags are offered before the library is loaded, and load it", () => {
  assert.equal(applied("{% extends 'base.html' %}\n{% comp▮ %}", "component"), "{% extends 'base.html' %}\n{% load wireview %}\n{% component \"$1\" %}$0");
  assert.equal(applied("<p>{% ▮ %}", "tag_header"), "{% load wireview %}\n<p>{% tag_header %}$0");
  assert.ok(!at(`${W}{% ▮ %}`).items.some((item) => item.edits), "loaded: no edit");
});

test("load: the libraries not loaded yet", () => {
  const offered = labels("{% load static %}{% load ▮ %}");
  assert.ok(offered.includes("i18n") && offered.includes("wireview"));
  assert.ok(!offered.includes("static"));
});

test("extends and include: template names, in quotes or into them", () => {
  assert.ok(labels('{% extends "▮" %}').includes("base.html"));
  assert.equal(applied('{% include "ba▮" %}', "base.html"), '{% include "base.html" %}');
  assert.equal(applied("{% include ▮ %}", "card.html"), '{% include "card.html" %}');
  assert.ok(!labels('{% include "▮" %}').includes("todo/list.html"), "not the template itself");
});

test("filters after | , with : for one that needs an argument", () => {
  assert.ok(labels("{{ x|▮ }}").includes("upper"));
  assert.equal(applied("{{ x|def▮ }}", "default"), "{{ x|default: }}");
  assert.ok(labels("{% if x|▮ %}").includes("length"));
  assert.ok(!labels("{{ x|▮ }}").includes("intcomma"), "not loaded");
});

test("component names: by kind", () => {
  const plain = labels(`${W}{% component "▮" %}`);
  assert.ok(plain.includes("Card") && plain.includes("TodoList"));
  assert.ok(!plain.includes("Counter"));
  assert.deepEqual(labels(`${W}{% live_component "▮" %}`), ["Counter"]);
  assert.deepEqual(labels(`${W}{% func "▮" %}`), ["badge"]);
  assert.equal(applied(`${W}{% component_block ▮ %}`, "Card"), `${W}{% component_block "Card" %}`);
});

test("component arguments: fields not given yet, required first, and id", () => {
  const { items } = at(`${W}{% component "TodoList" items=x ▮ %}`);
  const sorted = [...items].sort((a, b) => (a.sortText ?? "").localeCompare(b.sortText ?? ""));
  assert.deepEqual(sorted.map((item) => item.label), ["title=", "id="]);
  assert.equal(sorted[0].insert, "title=");
  const live = at(`${W}{% live_component "Counter" ▮ %}`).items;
  assert.equal([...live].sort((a, b) => (a.sortText ?? "").localeCompare(b.sortText ?? ""))[0].label, "id=");
  assert.deepEqual(labels(`${W}{% func "badge" ▮ %}`).sort(), ["text=", "tone="]);
});

test("after key= : variables", () => {
  assert.ok(labels(`${W}{% component "Card" title=▮ %}`).includes("title"));
  assert.ok(labels(`${W}{% component "Card" title=▮ %}`).includes("this"));
});

test("on: events, then modifiers, then a modifier's argument", () => {
  assert.ok(labels(`${W}{% on "▮" %}`).includes("click"));
  assert.equal(applied(`${W}{% on ▮ %}`, "click"), `${W}{% on "click" %}`);
  const modifiers = labels(`${W}{% on "click.prevent.▮" %}`);
  assert.ok(modifiers.includes("stop") && modifiers.includes("debounce"));
  assert.ok(!modifiers.includes("prevent"), "already there");
  assert.deepEqual(labels(`${W}{% on "input.debounce.▮" %}`), ["150", "300", "500", "1000"]);
  assert.ok(labels(`${W}{% on "keydown.key.▮" %}`).includes("Escape"));
  assert.ok(labels(`${W}{% on "keydown.key.Escape.▮" %}`).includes("prevent"), "the argument is eaten");
  assert.equal(applied(`${W}{% on "click.pre▮vent" "add" %}`, "stop"), `${W}{% on "click.stop" "add" %}`);
});

test("on: the handlers of the components that draw the template", () => {
  assert.deepEqual(labels(`${W}{% on "click" "▮" %}`).sort(), ["add", "anything", "toggleAll"]);
  assert.deepEqual(labels(`${W}{% on "click" "▮" %}`, COUNTER), ["increment"]);
  // A partial: every component's, last
  const { items } = at(`${W}{% on "click" "▮" %}`, PARTIAL);
  assert.ok(items.some((item) => item.label === "increment" && item.detail?.startsWith("Counter")));
  assert.ok(items.every((item) => item.sortText?.startsWith("9")));
});

test("on: the handler's parameters and myself", () => {
  assert.deepEqual(labels(`${W}{% on "click" "add" ▮ %}`), ["text=", "myself=True"]);
  assert.deepEqual(labels(`${W}{% on "click" "add" text=1 ▮ %}`), ["myself=True"]);
});

test("fill: the slots of the component the block names, and what render_slot passes", () => {
  const slots = labels(`${W}{% component_block "Card" %}{% fill ▮ %}{% endcomponent %}`);
  assert.deepEqual(slots.sort(), ["footer", "header"]);
  assert.deepEqual(labels(`${W}{% component_block "Card" %}{% fill footer ▮ %}{% endcomponent %}`), ["let:note"]);
  assert.deepEqual(labels(`${W}{% fill ▮ %}`), [], "no block, no slots");
});

test("render_slot: the slots the component declares", () => {
  assert.deepEqual(labels(`${W}{% render_slot "▮" %}`, `${DIR}/card.html`).sort(), ["footer", "header"]);
});

test("variables: fields, properties, the loop's and the block's names", () => {
  const names = labels(`${W}{% for row, n in items %}{% with total=3 %}{{ ▮ }}{% endwith %}{% endfor %}`);
  for (const name of ["title", "items", "remaining", "this", "row", "n", "forloop", "total"]) assert.ok(names.includes(name), name);
  assert.deepEqual(labels("{{ this.▮ }}").sort(), ["items", "remaining", "title"]);
  assert.deepEqual(labels("{{ row.▮ }}"), []);
  assert.ok(labels('{% url "x" as link %}{{ ▮ }}').includes("link"));
  assert.ok(labels(`${W}{% component_block "Card" %}{% fill footer let:note %}{{ ▮ }}{% endfill %}{% endcomponent %}`).includes("note"));
  assert.ok(labels("{% if ▮ %}").includes("title"));
  assert.deepEqual(labels('{{ "▮" }}'), [], "in a string");
});

test("wire-hook: the hooks the hook files register", () => {
  assert.deepEqual(labels('<canvas wire-hook="▮">'), ["Chart"]);
  assert.deepEqual(labels('<canvas wire-hook="Chart ▮">'), []);
});

test("wire-viewport-*: handlers", () => {
  assert.deepEqual(labels('<div wire-viewport-bottom="▮">').sort(), ["add", "anything", "toggleAll"]);
});

test("without metadata nothing but what the text shows", () => {
  const { text, offset } = cursor("{% ▮ %}");
  assert.deepEqual(complete(parse(text, undefined), offset, env(LIST, null)), []);
});
