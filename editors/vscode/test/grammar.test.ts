// The TextMate grammar, tokenized the way VS Code does (vscode-textmate with the
// Oniguruma engine). `text.html.basic` is a small stand-in written here: the
// test does not depend on a VS Code download, only on where the HTML grammar
// leaves Django syntax -- text, attribute strings, script bodies.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

import type * as Oniguruma from "vscode-oniguruma";
import type * as Textmate from "vscode-textmate";

const require = createRequire(import.meta.url);
// CommonJS packages: their named exports are not visible to an ES import
const oniguruma = require("vscode-oniguruma") as typeof Oniguruma;
const textmate = require("vscode-textmate") as typeof Textmate;
const here = new URL("..", import.meta.url);

const HTML_STUB = {
  scopeName: "text.html.basic",
  patterns: [
    {
      begin: "(<)(script)\\b",
      end: "(</)(script)(>)",
      name: "meta.embedded.block.html",
      patterns: [{ begin: ">", end: "(?=</script)", contentName: "source.js", patterns: [] }],
    },
    {
      begin: "(</?)([\\w-]+)",
      end: "/?>",
      name: "meta.tag.html",
      patterns: [
        { match: "[\\w-]+", name: "entity.other.attribute-name.html" },
        { begin: '"', end: '"', name: "string.quoted.double.html" },
        { begin: "'", end: "'", name: "string.quoted.single.html" },
      ],
    },
  ],
};

async function loadGrammar(): Promise<Textmate.IGrammar> {
  const wasm = readFileSync(require.resolve("vscode-oniguruma/release/onig.wasm"));
  await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
  const registry = new textmate.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (patterns: string[]) => new oniguruma.OnigScanner(patterns),
      createOnigString: (text: string) => new oniguruma.OnigString(text),
    }),
    loadGrammar: async (scopeName: string) => {
      if (scopeName === "text.html.django") {
        const source = readFileSync(new URL("syntaxes/django-html.tmLanguage.json", here), "utf8");
        return textmate.parseRawGrammar(source, "django-html.tmLanguage.json");
      }
      if (scopeName === "text.html.basic") return textmate.parseRawGrammar(JSON.stringify(HTML_STUB), "html.json");
      return null;
    },
  });
  const grammar = await registry.loadGrammar("text.html.django");
  assert.ok(grammar);
  return grammar;
}

const grammarPromise = loadGrammar();

/** Each piece of each line with its scopes, innermost last. */
async function tokenize(text: string): Promise<{ text: string; scopes: string[] }[]> {
  const grammar = await grammarPromise;
  let state = textmate.INITIAL;
  const out: { text: string; scopes: string[] }[] = [];
  for (const line of text.split("\n")) {
    const result = grammar.tokenizeLine(line, state);
    for (const token of result.tokens) out.push({ text: line.slice(token.startIndex, token.endIndex), scopes: token.scopes });
    state = result.ruleStack;
  }
  return out;
}

/** Whether a token is in Django syntax: a scope of this grammar's own other than the root. */
function inDjango(scopes: string[]): boolean {
  return scopes.slice(1).some((scope) => scope.endsWith(".django"));
}

async function scopesOf(text: string, piece: string): Promise<string[]> {
  const tokens = await tokenize(text);
  const found = tokens.find((token) => token.text === piece);
  assert.ok(found, `no token ${JSON.stringify(piece)} in ${JSON.stringify(tokens.map((token) => token.text))}`);
  return found.scopes;
}

test("a tag in text: its delimiters, its name, its arguments", async () => {
  const text = '<p>{% if items|length > 1 %}</p>';
  assert.ok((await scopesOf(text, "{%")).includes("punctuation.section.embedded.begin.django"));
  assert.ok((await scopesOf(text, "if")).includes("keyword.control.django"));
  assert.ok((await scopesOf(text, "length")).includes("support.function.filter.django"));
  assert.ok((await scopesOf(text, "%}")).includes("punctuation.section.embedded.end.django"));
  assert.ok((await scopesOf(text, "</p")).includes("meta.tag.html"), "HTML goes on after the tag");
});

test("Django syntax inside an attribute string", async () => {
  const text = '<a class="btn {{ cls|default:"x" }}" {% on "click" "add" %}>';
  const scopes = await scopesOf(text, "cls");
  assert.ok(scopes.includes("meta.variable.django"));
  assert.ok(scopes.includes("string.quoted.double.html"), "still in the attribute");
  assert.ok((await scopesOf(text, "default")).includes("support.function.filter.django"));
  assert.ok((await scopesOf(text, "on")).includes("keyword.control.django"));
  assert.ok((await scopesOf(text, "click")).includes("string.quoted.double.django"));
});

test("Django syntax inside a script", async () => {
  const text = "<script>const n = {{ count }};</script>";
  assert.ok((await scopesOf(text, "count")).includes("meta.variable.django"));
});

test("comments: {# #} and {% comment %} over lines", async () => {
  assert.ok((await scopesOf("{# a note #}", " a note ")).includes("comment.block.django"));
  const tokens = await tokenize("{% comment %}\n{% if x %}\n{% endcomment %}<p>");
  const inside = tokens.find((token) => token.text.includes("if x"))!;
  assert.ok(inside.scopes.includes("comment.block.django"));
  assert.ok(!inside.scopes.includes("keyword.control.django"));
  const after = tokens.find((token) => token.text === "<p")!;
  assert.ok(!after.scopes.includes("comment.block.django"), "the comment ends");
});

test("verbatim: what it holds is not Django syntax", async () => {
  const tokens = await tokenize("{% verbatim %}{{ raw }}{% endverbatim %}{{ x }}");
  const raw = tokens.find((token) => token.text.includes("raw"))!;
  assert.ok(!raw.scopes.includes("meta.variable.django"));
  assert.ok((await scopesOf("{% verbatim %}{{ raw }}{% endverbatim %}{{ x }}", "x")).includes("meta.variable.django"));
});

test("a tag left open does not spill onto the next line", async () => {
  const tokens = await tokenize("{% if a\n<p>text</p>");
  const p = tokens.find((token) => token.text === "text")!;
  assert.ok(!inDjango(p.scopes));
  const variable = await tokenize("{{ a\n<b>");
  assert.ok(!inDjango(variable.find((token) => token.text === "<b")!.scopes));
});

test("an unterminated string in a tag ends with the line", async () => {
  const tokens = await tokenize('{% on "click %}\n<p>');
  assert.ok(!inDjango(tokens.find((token) => token.text === "<p")!.scopes));
});

test("let: and key= in a tag", async () => {
  const text = "{% fill row let:item %}{% component 'X' count=3 %}";
  assert.ok((await scopesOf(text, "item")).includes("variable.parameter.django"));
  assert.ok((await scopesOf(text, "count")).includes("variable.parameter.django"));
});
