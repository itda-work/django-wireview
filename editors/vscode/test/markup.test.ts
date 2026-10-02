// The start tags a browser's tokenizer would make, and nothing else.
import { strict as assert } from "node:assert";
import test from "node:test";

import { parseFragment } from "parse5";

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
  // A tag the text ends in is no tag
  assert.deepEqual(attributes('<b a="1'), []);
  assert.deepEqual(attributes('<b a="1">'), [["a", "1"]]);
});

// What parse5 (the HTML standard's parser) puts in the DOM. The tokenizer may see less
// than the DOM where it cannot be sure (it then stops), never more; where it is sure,
// it sees the same. A value with a character reference is left to symbols.ts.
const CERTAIN = [
  '<div title="a>b" wire-hook="X">',
  "<div wire-hook=X>",
  '<script>x</ScRiPt><div wire-hook="X">',
  '<script>a<!--b</script><b wire-hook="X">',
  '<!--><div wire-hook="X">',
  '<!---><div wire-hook="X">',
  '<!----><div wire-hook="X">',
  '<!-- x --!><div wire-hook="X">',
  '<!-- x ---><div wire-hook="X">',
  '<!-- x -- ><div wire-hook="Y"> --><div wire-hook="X">',
  '<!-- <div wire-hook="Y"> -->',
  '<textarea><div wire-hook="Y"></textarea><title><div wire-hook="Y"></title>',
  '<noscript><div wire-hook="Y"></noscript><div wire-hook="X">',
  '<div wire-hook="X" wire-hook="Y" WIRE-HOOK="Z">',
  '<div wire-hook="X"',
  '<!DOCTYPE html "a>b"><b wire-hook="X">',
  '<?xml version="1.0"?><b wire-hook="X">',
  '</i a=">" wire-hook="Y"><b wire-hook="X">',
  '</ x wire-hook="Y"><b wire-hook="X">',
  '</><b wire-hook="X">',
  '<![CDATA[ a ]]><b wire-hook="X">',
  '<svg><path d="1"><circle r="2"/></svg><div wire-hook="X">',
  '<svg/><div wire-hook="X">',
  '<svg><style><!--</style><div wire-hook="Y">--></style></svg><div wire-hook="X">',
  '<svg><script>"</script>"</svg><div wire-hook="X">',
  '<svg><title>t</title><g wire-hook="X"></g></svg>',
  '<svg><g><div wire-hook="X"><script><b wire-hook="Y"></script>',
  '<svg><svg></svg><g wire-hook="X"></g></svg><textarea><b wire-hook="Y"></textarea>',
  '<math><mi>x</mi></math><b wire-hook="X">',
  // An end tag no SVG element of its name is open for: it closes HTML around the SVG, or is dropped
  '<p><svg><circle r="1"></p><div wire-hook="X">',
  '<p><svg><circle r="1"></br><div wire-hook="X">',
  '<div><svg class="a"><svg class="b"><path/></svg></div><div wire-hook="X">',
  '<svg></div><b wire-hook="X">',
  '<div><svg></div><textarea><b wire-hook="Y"></textarea>',
];
const UNSURE = [
  '<svg><![CDATA[ > <div wire-hook="Y"> ]]></svg><div wire-hook="X">',
  '<script><!--<script></script><div wire-hook="Y">--></script><div wire-hook="X">',
  '<svg><foreignObject><div wire-hook="X"></div></foreignObject></svg>',
  '<math><mi><div wire-hook="X"></mi></math>',
  // HTML again inside an integration point
  '<svg><foreignObject><textarea><b wire-hook="Y"></textarea></foreignObject></svg>',
  '<math><mi><script>"<b wire-hook="Y">"</script></mi></math>',
  // An end tag the DOM drops leaves the SVG open, and the tokenizer reads on as HTML:
  // it sees less in what HTML takes for raw text, and nothing past CDATA
  '<svg></div><textarea><b wire-hook="X"></textarea>',
  '<svg></div><![CDATA[ > <div wire-hook="Y"> ]]><div wire-hook="Z">',
];

function inDom(html: string): string[] {
  const found: string[] = [];
  const walk = (node: { attrs?: { name: string; value: string }[]; childNodes?: unknown[]; content?: unknown }) => {
    for (const attribute of node.attrs ?? []) if (attribute.name.startsWith("wire-")) found.push(`${attribute.name}=${attribute.value}`);
    for (const child of node.childNodes ?? []) walk(child as never);
  };
  walk(parseFragment(html) as never);
  return found.sort();
}

function seen(html: string): string[] {
  return attributes(html)
    .filter(([name, value]) => name.startsWith("wire-") && value !== null)
    .map(([name, value]) => `${name}=${value}`)
    .sort();
}

test("the same attributes as the HTML parser's DOM, where the tokenizer is sure", () => {
  for (const html of CERTAIN) assert.deepEqual(seen(html), inDom(html), html);
});

test("never an attribute the DOM does not have, where the tokenizer is not sure", () => {
  for (const html of UNSURE) {
    const dom = inDom(html);
    for (const attribute of seen(html)) assert.ok(dom.includes(attribute), `${attribute} is not in the DOM of ${html}`);
  }
});
