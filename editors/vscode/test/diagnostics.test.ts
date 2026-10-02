// Every rule twice: a template it must flag, and the nearest one it must not.
// A rule that flags a template Django renders is worse than no rule.
import { strict as assert } from "node:assert";
import test from "node:test";

import { diagnose } from "../src/core/diagnostics.ts";
import type { Problem } from "../src/core/diagnostics.ts";
import { parseTemplate } from "../src/core/template.ts";
import { COUNTER, DIR, env, LIST, parse, PARTIAL, project } from "./fixture.ts";

function codes(text: string, path = LIST): string[] {
  return diagnose(parse(text), env(path)).map((problem) => problem.code);
}

function problems(text: string, path = LIST): Problem[] {
  return diagnose(parse(text), env(path));
}

const W = "{% load wireview %}";

function pair(code: string, flagged: string, fine: string, path = LIST): void {
  test(`${code}: flags what fails, not what renders`, () => {
    assert.ok(codes(flagged, path).includes(code), `expected ${code} in ${JSON.stringify(codes(flagged, path))} for ${flagged}`);
    assert.deepEqual(codes(fine, path), [], `nothing expected for ${fine}`);
  });
}

pair("unknown-component", `${W}{% component "Nope" %}`, `${W}{% component "Card" %}`);
pair("unknown-component", `${W}{% component_block "app:Nope" %}{% endcomponent %}`, `${W}{% component_block "app.live.Card" %}{% fill header %}{% endfill %}{% endcomponent %}`);
pair("not-a-live-component", `${W}{% live_component "Card" id="c" %}`, `${W}{% live_component "Counter" id="c" %}`);
pair("live-component-needs-id", `${W}{% live_component "Counter" %}`, `${W}{% live_component "Counter" id=row.id %}`);
pair("unknown-function-component", `${W}{% func "nope" %}`, `${W}{% func "badge" text="x" %}`);
pair("unknown-argument", `${W}{% component "Card" titel="x" %}`, `${W}{% component "Card" title="x" id="a" %}`);
pair("unknown-argument", `${W}{% func "badge" text="x" size=2 %}`, `${W}{% func "badge" text="x" tone="ok" %}`);
pair("missing-argument", `${W}{% component "TodoList" %}`, `${W}{% component "TodoList" title=t %}`);
pair("missing-argument", `${W}{% func "badge" tone="x" %}`, `${W}{% func "badge" text=t %}`);
pair("unknown-handler", `${W}<b {% on "click" "nope" %}>`, `${W}<b {% on "click" "add" %}>`);
pair("unknown-handler", `<div wire-viewport-bottom="more">`, `<div wire-viewport-bottom="toggleAll" wire-auto-recover>`);
pair("not-a-handler", `${W}<b {% on "click" "joined" %}>`, `${W}<b {% on "click" "toggleAll" %}>`);
pair("unknown-handler-argument", `${W}<b {% on "click" "add" txt=1 %}>`, `${W}<b {% on "click" "add" text=1 myself=True %}>`);
pair("invalid-event", `${W}<b {% on "1click" "add" %}>`, `${W}<b {% on "my-event:x" "add" %}>`);
pair("unknown-modifier", `${W}<b {% on "click.away" "add" %}>`, `${W}<b {% on "click.prevent.stop" "add" %}>`);
pair("unknown-modifier", `${W}<b {% on "click.inlinejs" "add" %}>`, `${W}<b {% on "keydown.key.Enter" "add" %}>`);
// binding() refuses inlinejs anywhere in the dotted name, before it reads a key's argument
pair("unknown-modifier", `${W}<b {% on "keydown.key.inlinejs" "add" %}>`, `${W}<b {% on "keydown.key.inline" "add" %}>`);
pair("modifier-argument", `${W}<b {% on "input.debounce" "add" %}>`, `${W}<b {% on "input.debounce.300.prevent" "add" %}>`);
pair("modifier-argument", `${W}<b {% on "input.debounce.fast" "add" %}>`, `${W}<b {% on "keydown.key.Escape" "add" %}>`);
pair(
  "missing-slot",
  `${W}{% component_block "Card" %}{% fill footer %}{% endfill %}{% endcomponent %}`,
  `${W}{% component_block "Card" %}{% fill "header" %}{% endfill %}{% endcomponent %}`,
);
pair("missing-slot", `${W}{% func_block "badge" text=t %}{% endfunc %}`, `${W}{% func_block "badge" text=t %}{% fill icon %}{% endfill %}{% endfunc %}`);
pair("unknown-hook", `<canvas wire-hook="Graph">`, `<canvas wire-hook="Chart" id="c">`);
pair("tag-not-loaded", "{% component 'Card' %}", `${W}{% component 'Card' %}`);
pair("tag-not-loaded", "{% blocktranslate %}x{% endblocktranslate %}", "{% load i18n %}{% blocktranslate %}x{% plural %}y{% endblocktranslate %}");
pair("unknown-tag", "{% frobnicate %}", "{% csrf_token %}");
pair("unknown-filter", "{{ x|shout }}", "{{ x|upper }}");
pair("filter-not-loaded", "{{ x|intcomma }}", "{% load intcomma from humanize %}{{ x|intcomma }}");
pair("filter-argument", "{{ x|default }}", '{{ x|default:"-" }}');
pair("filter-argument", "{{ x|upper:1 }}", "{{ x|date }}{{ x|date:'Y' }}");
// {% filter %} reads a filter chain with no value before it: "var|" + its arguments
pair("unknown-filter", "{% filter no_such_filter %}t{% endfilter %}", "{% filter upper|default:'-' %}t{% endfilter %}");
pair("filter-argument", "{% filter default %}t{% endfilter %}", "{% filter date:'Y'|upper %}t{% endfilter %}");
pair("filter-not-permitted", "{% filter upper|safe %}t{% endfilter %}", "{% filter upper %}t{% endfilter %}");
pair("unknown-library", "{% load nope %}", "{% load static %}");
pair("unclosed-block", "{% if a %}", "{% if a %}{% endif %}");
pair("unmatched-end", "{% endfor %}", "{% for a in b %}{% endfor %}");
pair("template-not-found", '{% extends "nope.html" %}', '{% extends "base.html" %}{% include "./counter.html" %}');

test("severity follows what the runtime does", () => {
  const severities = Object.fromEntries(
    problems(`${W}{% component "Nope" %}{% component "Card" titel=1 %}<i wire-hook="Graph">{% frobnicate %}`).map((problem) => [
      problem.code,
      problem.severity,
    ]),
  );
  assert.deepEqual(severities, {
    "unknown-component": "error",
    "unknown-argument": "warning",
    "unknown-hook": "information",
    "unknown-tag": "warning",
  });
});

test("nothing without metadata", () => {
  assert.deepEqual(diagnose(parseTemplate("{% if %}{% component 'Nope' %}", undefined), env(LIST, null)), []);
});

test("a name the template computes is not checked", () => {
  assert.deepEqual(codes(`${W}{% component name %}{% on "click" handler %}{% on event "add" %}<i wire-hook="{{ h }}">`), []);
  assert.deepEqual(codes(`${W}{% component "Nope"|upper %}`), []);
});

test("a tag Django reads as text is not checked", () => {
  assert.deepEqual(codes(`${W}{% component "Nope"\n%}`), []);
});

test("a variable Django reads as text is not checked", () => {
  assert.deepEqual(codes("{{ x|no_such_filter"), [], "still being typed");
  assert.deepEqual(codes("{{ x|no_such_filter\n}}"), [], "closed on another line: text to Django");
  assert.deepEqual(codes("{{ x|no_such_filter }}"), ["unknown-filter"]);
});

test("handlers are checked against the components that draw the template, and only when one does", () => {
  assert.deepEqual(codes(`${W}<b {% on "click" "nope" %}>`, PARTIAL), [], "a partial: whoever includes it");
  assert.deepEqual(codes(`${W}<b {% on "click" "increment" %}>`, COUNTER), []);
  assert.deepEqual(codes(`${W}<b {% on "click" "add" %}>`, COUNTER), ["unknown-handler"]);
});

test("a handler that takes **kwargs takes any argument", () => {
  assert.deepEqual(codes(`${W}<b {% on "click" "anything" whatever=1 %}>`), []);
});

test("a component that reads its own arguments is not told which it lacks", () => {
  assert.deepEqual(codes(`${W}{% component "Flexible" whatever=1 %}`), []);
});

test("required slots are counted only among the block's own fills", () => {
  // _extract_slots reads the block's direct children: a fill inside {% if %} is not seen
  assert.deepEqual(
    codes(`${W}{% component_block "Card" %}{% if a %}{% fill header %}{% endfill %}{% endif %}{% endcomponent %}`),
    ["missing-slot"],
  );
  // A name the tag finds through FQN or app: is not checked by it either
  assert.deepEqual(codes(`${W}{% component_block "app:Card" %}{% endcomponent %}`), []);
});

test("the end of a block whose end the metadata could not read is not an unknown tag", () => {
  assert.deepEqual(codes("{% load thirdparty %}{% mystery %}{% endmystery %}"), []);
  assert.deepEqual(codes("{% else %}"), [], "a middle out of place may belong to such a block");
});

test("a | in a tag that reads its arguments its own way is not a filter", () => {
  assert.deepEqual(codes(`${W}<b {% cond {'x': a | b} %}>`), []);
  assert.deepEqual(codes("{% load thirdparty %}{% mystery a|whatever %}"), []);
  assert.deepEqual(codes("{% if a|shout %}{% endif %}"), ["unknown-filter"]);
});

test("a diagnostic points at the name it is about", () => {
  const text = `${W}{% component "Nope" %}`;
  const [problem] = problems(text);
  assert.equal(text.slice(problem.span.start, problem.span.end), "Nope");
});

test("a template elsewhere is relative to this one's name", () => {
  assert.deepEqual(codes('{% include "../card.html" %}', `${DIR}/todo/list.html`), []);
  assert.deepEqual(codes('{% include "./card.html" %}', `${DIR}/todo/list.html`), ["template-not-found"]);
});

test("the metadata is read from one project", () => {
  assert.ok(project.component("Card"));
});

test("a linked template is named by where it stands in a template directory", () => {
  // The real file is outside every template directory; the loader finds it by the link's name
  const linked = { ...env("/elsewhere/real.html"), documentPath: `${DIR}/todo/list.html` };
  assert.deepEqual(diagnose(parse('{% include "./card.html" %}'), linked).map((p) => p.code), ["template-not-found"]);
  assert.deepEqual(diagnose(parse('{% include "../card.html" %}'), linked).map((p) => p.code), []);
});
