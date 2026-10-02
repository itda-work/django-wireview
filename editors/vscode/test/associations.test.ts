import { strict as assert } from "node:assert";
import test from "node:test";

import { associatedWithHtml, globToRegExp, opensAsTemplate } from "../src/core/associations.ts";

test("globs as files.associations writes them", () => {
  assert.ok(globToRegExp("*.html").test("page.HTML"));
  assert.ok(!globToRegExp("*.html").test("a/page.html"));
  assert.ok(globToRegExp("**/views/*.html").test("/w/site/views/page.html"));
  assert.ok(globToRegExp("**/views/*.html").test("views/page.html"));
  assert.ok(!globToRegExp("**/views/*.html").test("/w/views/sub/page.html"));
  assert.ok(globToRegExp("**/views/**").test("/w/views/sub/page.html"));
  assert.ok(globToRegExp("*.{htm,html}").test("x.htm"));
  assert.ok(globToRegExp("page?.html").test("page1.html"));
  assert.ok(globToRegExp("[ab].html").test("b.html"));
  assert.ok(!globToRegExp("[!ab].html").test("b.html"));
});

test("an association to html is the user's: by name, by path, or from the folder", () => {
  const path = "/w/site/views/page.html";
  assert.ok(associatedWithHtml({ "page.html": "html" }, path, "/w"));
  assert.ok(associatedWithHtml({ "**/views/*.html": "html" }, path, "/w"));
  assert.ok(associatedWithHtml({ "site/views/*.html": "html" }, path, "/w"), "relative to the folder");
  assert.ok(associatedWithHtml({ "/w/site/**": "html" }, path, "/w"));
  assert.ok(!associatedWithHtml({ "**/views/*.html": "django-html" }, path, "/w"), "another language is not a choice of html");
  assert.ok(!associatedWithHtml({ "*.txt": "html", "other/*.html": "html" }, path, "/w"));
  assert.ok(associatedWithHtml({ "**/views/*.html": "html" }, "C:\\w\\site\\views\\page.html", "C:\\w"), "Windows paths");
});

test("a pattern that is no glob is passed over, not the others", () => {
  const path = "/w/site/views/page.html";
  assert.ok(!associatedWithHtml({ "[z-a].html": "html" }, path, "/w"));
  assert.ok(associatedWithHtml({ "[z-a].html": "html", "**/views/*.html": "html" }, path, "/w"));
  assert.ok(opensAsTemplate({ languageId: "html", scheme: "file", path, inTemplateDirectory: true, switchedBefore: false }, { "[z-a]/*": "html" }, "/w"));
});

test("an html file in a template directory opens as django-html, unless the user chose otherwise", () => {
  const document = { languageId: "html", scheme: "file", path: "/w/site/views/page.html", inTemplateDirectory: true, switchedBefore: false };
  assert.ok(opensAsTemplate(document, {}, "/w"));
  assert.ok(!opensAsTemplate({ ...document, inTemplateDirectory: false }, {}, "/w"), "not where the loaders look");
  assert.ok(!opensAsTemplate({ ...document, switchedBefore: true }, {}, "/w"), "switched back by hand");
  assert.ok(!opensAsTemplate({ ...document, languageId: "django-html" }, {}, "/w"));
  assert.ok(!opensAsTemplate({ ...document, languageId: "jinja-html" }, {}, "/w"), "another language the user picked");
  assert.ok(!opensAsTemplate({ ...document, scheme: "untitled" }, {}, "/w"));
  assert.ok(!opensAsTemplate(document, { "**/views/*.html": "html" }, "/w"), "files.associations says html");
});
