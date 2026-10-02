import { strict as assert } from "node:assert";
import test from "node:test";

import { blockAt, readLoads, visibleAt } from "../src/core/template.ts";
import { parse, project } from "./fixture.ts";

test("blocks nest, and each knows its middles and its end", () => {
  const doc = parse("{% if a %}{% for x in y %}{% empty %}{% endfor %}{% else %}{% endif %}");
  assert.equal(doc.problems.length, 0);
  const [outer, inner] = doc.blocks;
  assert.equal(outer.open.name, "if");
  assert.equal(outer.close?.name, "endif");
  assert.deepEqual(outer.middles.map((tag) => tag.name), ["else"]);
  assert.equal(inner.parent, outer);
  assert.deepEqual(inner.middles.map((tag) => tag.name), ["empty"]);
});

test("a block nothing closes is unclosed", () => {
  const doc = parse("{% if a %}<p>");
  assert.deepEqual(doc.problems.map((problem) => [problem.kind, problem.tag.name, problem.expected]), [["unclosed", "if", "endif"]]);
});

test("an end that closes an outer block leaves the inner one unclosed", () => {
  const doc = parse("{% for x in y %}{% if a %}{% endfor %}");
  assert.deepEqual(doc.problems.map((problem) => [problem.kind, problem.tag.name]), [["unclosed", "if"]]);
  assert.equal(doc.blocks[0].close?.name, "endfor");
});

test("an end with no block open is unmatched", () => {
  const doc = parse("<p>{% endif %}");
  assert.deepEqual(doc.problems.map((problem) => [problem.kind, problem.tag.name]), [["unmatched-end", "endif"]]);
});

test("an end tag the metadata does not know is left alone", () => {
  // A third-party block whose end the extraction could not read: not a block, its end not an end
  const doc = parse("{% if a %}{% mystery %}{% endmystery %}{% endif %}");
  assert.equal(doc.problems.length, 0);
  assert.equal(doc.blocks.length, 1);
});

test("a tag still being typed is not part of the structure", () => {
  const doc = parse("{% if a %}\n{% endif\n");
  assert.equal(doc.tags.length, 1);
  assert.equal(doc.problems[0].kind, "unclosed");
});

test("load: whole libraries, and names from one", () => {
  const doc = parse("{% load static i18n %}{% load intcomma from humanize %}");
  const loads = readLoads(doc.tags);
  assert.deepEqual(
    loads.map((load) => [load.library, load.names]),
    [
      ["static", null],
      ["i18n", null],
      ["humanize", ["intcomma"]],
    ],
  );
  assert.ok(doc.visible!.tags.has("blocktranslate"));
  assert.ok(doc.visible!.filters.has("intcomma"));
  assert.ok(!doc.visible!.tags.has("component"));
});

test("a later load overrides an earlier one, as Parser.add_library does", () => {
  const library = (text: string) => parse(`${text}{% component "X" %}`).visible!.tags.get("component")?.library;
  assert.equal(library("{% load component from wireview %}{% load thirdparty %}"), "thirdparty");
  assert.equal(library("{% load thirdparty %}{% load component from wireview %}"), "wireview");
  // The same libraries in another order are another answer: the cache keeps the order
  assert.equal(library("{% load wireview thirdparty %}"), "thirdparty");
  assert.equal(library("{% load thirdparty wireview %}"), "wireview");
});

test("a tag sees only the loads before it", () => {
  const text = "{% component 'A' %}{% load wireview %}{% component 'B' %}";
  const doc = parse(text);
  assert.equal(visibleAt(doc, text.indexOf("'A'"))!.tags.has("component"), false);
  assert.equal(visibleAt(doc, text.indexOf("'B'"))!.tags.get("component")?.library, "wireview");
});

test("without the engine's tags nothing is said about structure", () => {
  const bare = { ...project.metadata, template_builtins: { tags: {}, filters: {} } };
  const doc = parse("{% if a %}", new (project.constructor as new (m: typeof bare) => typeof project)(bare));
  assert.equal(doc.visible, undefined);
  assert.equal(doc.problems.length, 0);
});

test("the block at an offset is the innermost whose body holds it", () => {
  const text = "{% if a %}{% for x in y %}X{% endfor %}Y{% endif %}Z";
  const doc = parse(text);
  assert.equal(blockAt(doc, text.indexOf("X"))?.open.name, "for");
  assert.equal(blockAt(doc, text.indexOf("Y"))?.open.name, "if");
  assert.equal(blockAt(doc, text.indexOf("Z")), null);
});
