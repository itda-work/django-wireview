import { strict as assert } from "node:assert";
import test from "node:test";

import { maskDjango } from "../src/core/mask.ts";
import { inComment, kwargOf, literalOf, scan, tokenAt } from "../src/core/scan.ts";
import type { TagToken } from "../src/core/scan.ts";

const tags = (text: string) => scan(text).filter((token): token is TagToken => token.kind === "tag");

test("tags, variables and comments on one line are each a token", () => {
  const tokens = scan('<p>{% if a %}{{ b|upper }}{# note #}{% endif %}</p>');
  assert.deepEqual(
    tokens.map((token) => token.kind),
    ["tag", "variable", "comment", "tag"],
  );
  assert.deepEqual(
    tags('{% if a %}{% endif %}').map((tag) => tag.name),
    ["if", "endif"],
  );
});

test("a quoted argument keeps its spaces, as split_contents() does", () => {
  const [tag] = tags('{% on "click" "add" text="two words" %}');
  assert.deepEqual(
    tag.bits.map((bit) => bit.text),
    ['"click"', '"add"', 'text="two words"'],
  );
  const kwarg = kwargOf(tag.bits[2])!;
  assert.equal(kwarg.key, "text");
  assert.equal(kwarg.value.text, '"two words"');
});

test("a tag is one line: without %} on its line it is text, and stops at the next opener", () => {
  const text = "{% if a\n%}{{ b }}";
  const [tag, variable] = scan(text);
  assert.equal(tag.kind, "tag");
  assert.equal((tag as TagToken).closed, false);
  assert.equal(tag.end, text.indexOf("\n"));
  assert.equal(variable.kind, "variable");

  const [typing, next] = scan("{% comp {{ x }}");
  assert.equal((typing as TagToken).closed, false);
  assert.equal(typing.end, 8);
  assert.equal(next.kind, "variable");
});

test("what the editor closed while a tag is typed is not part of it", () => {
  // Typing `{` puts `}` after the cursor
  const [tag] = tags("{% compo }");
  assert.equal(tag.closed, false);
  assert.equal(tag.name, "compo");
  assert.equal(tag.bits.length, 0);
});

test("nothing in a comment or verbatim block is a tag", () => {
  const text = "{% comment %}{% if %}{{ x }}{% endcomment %}{% verbatim %}{{ y }}{% endverbatim %}{% if b %}";
  const names = tags(text).map((tag) => tag.name);
  assert.deepEqual(names, ["comment", "endcomment", "verbatim", "endverbatim", "if"]);
  const regions = scan(text).filter((token) => token.kind === "comment");
  assert.deepEqual(
    regions.map((token) => token.kind === "comment" && token.region),
    ["comment", "verbatim"],
  );
  assert.ok(inComment(scan(text), text.indexOf("{% if %}") + 3));
  assert.ok(!inComment(scan(text), text.length - 2));
});

test("a named verbatim block ends only at its own end tag", () => {
  const text = "{% verbatim one %}{% endverbatim %}{% endverbatim one %}{% if a %}";
  assert.deepEqual(
    tags(text).map((tag) => tag.name),
    ["verbatim", "endverbatim", "if"],
  );
});

test("the token at an offset: inside its delimiters only", () => {
  const text = "ab{% if x %}cd";
  const tokens = scan(text);
  assert.equal(tokenAt(tokens, 1), undefined);
  assert.equal(tokenAt(tokens, 2), undefined, "before {% is outside");
  assert.equal(tokenAt(tokens, 5)?.kind, "tag");
  assert.equal(tokenAt(tokens, 13), undefined);
});

test("a literal: its value, whether it is closed, what follows it", () => {
  const [tag] = tags(`{% x "abc"|upper 'd e' "open %}`);
  const first = literalOf(tag.bits[0])!;
  assert.deepEqual([first.value, first.terminated, first.rest], ["abc", true, "|upper"]);
  const second = literalOf(tag.bits[1])!;
  assert.deepEqual([second.value, second.terminated], ["d e", true]);
  assert.equal(literalOf(tag.bits[2])!.terminated, false);
});

test("masking keeps every offset and line, and hides only Django syntax", () => {
  const text = '<a href="{% url "x" %}">{{ y }}</a>\n{# z #}<b>{% verbatim %}<i>{% endverbatim %}';
  const masked = maskDjango(text, scan(text));
  assert.equal(masked.length, text.length);
  assert.equal(masked.split("\n").length, text.split("\n").length);
  assert.equal(masked.indexOf("</a>"), text.indexOf("</a>"));
  assert.ok(!/[{}%#]/.test(masked));
  assert.ok(masked.includes("<i>"), "a verbatim body is HTML");
});

test("masking counts UTF-16 units, as offsets do", () => {
  const text = "{{ '😀' }}<p>";
  const masked = maskDjango(text, scan(text));
  assert.equal(masked.length, text.length);
  assert.equal(masked.indexOf("<p>"), text.indexOf("<p>"));
});
