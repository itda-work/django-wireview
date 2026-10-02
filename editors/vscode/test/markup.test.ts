// The start tags a browser's tokenizer would make, and nothing else.
import { strict as assert } from "node:assert";
import test from "node:test";

import { startTags } from "../src/core/markup.ts";

function attributes(html: string): [string, string | null][] {
  return startTags(html).flatMap((tag) => tag.attributes.map((a): [string, string | null] => [a.name, a.value]));
}

test("attributes in every spelling a browser accepts", () => {
  assert.deepEqual(attributes(`<div a="1" b='2' c=3 d e = "5"/><br/>`), [
    ["a", "1"],
    ["b", "2"],
    ["c", "3"],
    ["d", null],
    ["e", "5"],
  ]);
  assert.deepEqual(attributes('<DIV Data-X="y">'), [["data-x", "y"]]);
});

test("a value's offset is where it stands in the text", () => {
  const html = '<p>x</p><div wire-hook="Chart">';
  const [attribute] = startTags(html)[1].attributes;
  assert.equal(html.slice(attribute.valueStart, attribute.valueStart + attribute.value!.length), "Chart");
});

test("no attribute in a comment, a raw text element, an end tag or a doctype", () => {
  assert.deepEqual(attributes('<!DOCTYPE html a="1"><!-- <b c="2"> --></i d="3">'), []);
  assert.deepEqual(attributes(`<script>let s = '<b e="4">';</script><style>b[f="5"]{}</style>`), []);
  assert.deepEqual(attributes('<textarea><b g="6"></textarea><title><b h="7"></TITLE ><i j="8">'), [["j", "8"]]);
});

test("a < that opens no tag is text", () => {
  assert.deepEqual(attributes('a < b <3 <i k="9">'), [["k", "9"]]);
});

test("text that is never closed ends the scan", () => {
  assert.deepEqual(attributes('<!-- <b a="1">'), []);
  assert.deepEqual(attributes('<script><b a="1">'), []);
  assert.deepEqual(attributes('<b a="1'), [["a", "1"]]);
});
