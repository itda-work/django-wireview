// A small project's metadata, written by hand: the shape `manage.py wireview_lsp`
// prints, with only what the tests need.
import type { Env } from "../src/core/env.ts";
import type { ComponentMeta, FilterMeta, LibraryMeta, Metadata, MethodMeta, ParameterMeta, TagMeta } from "../src/core/metadata.ts";
import { Project } from "../src/core/project.ts";
import { parseTemplate } from "../src/core/template.ts";
import type { TemplateDoc } from "../src/core/template.ts";

export const DIR = "/proj/app/templates";
export const LIVE_PY = "/proj/app/live.py";
const DJANGO = "/site/django/template/defaulttags.py";
const FILTERS = "/site/django/template/defaultfilters.py";
const WIREVIEW = "/site/wireview/templatetags/wireview.py";

function tag(end: string | null = null, intermediate: string[] = [], file = DJANGO): TagMeta {
  return { docstring: null, file_path: file, line_number: 1, end, intermediate };
}

function filter(argument: FilterMeta["argument"], file = FILTERS, forbidden = false): FilterMeta {
  return { docstring: null, file_path: file, line_number: 1, argument, forbidden_in_filter_tag: forbidden };
}

function parameter(type: string | null, fallback?: unknown, kind = "POSITIONAL_OR_KEYWORD"): ParameterMeta {
  return { type, default: fallback ?? null, has_default: fallback !== undefined, kind };
}

function method(isHandler: boolean, parameters: Record<string, ParameterMeta> = {}, line = 20): MethodMeta {
  return { is_handler: isHandler, is_async: isHandler, parameters, docstring: null, file_path: LIVE_PY, line_number: line };
}

function component(name: string, extra: Partial<ComponentMeta>): ComponentMeta {
  return {
    name,
    fqn: `app.live.${name}`,
    app_key: `app:${name}`,
    module: "app.live",
    file_path: LIVE_PY,
    line_number: 1,
    kind: "component",
    docstring: null,
    template_name: "",
    template_path: null,
    fields: {},
    accepts_extra_kwargs: false,
    properties: {},
    methods: { joined: method(false), model_dump: method(false) },
    slots: {},
    subscriptions: [],
    subscriptions_is_dynamic: false,
    temporary_assigns: [],
    ...extra,
  };
}

const wireviewLibrary: LibraryMeta = {
  module: "wireview.templatetags.wireview",
  file_path: WIREVIEW,
  tags: {
    component: tag(null, [], WIREVIEW),
    component_block: tag("endcomponent", [], WIREVIEW),
    live_component: tag(null, [], WIREVIEW),
    live_component_block: tag("endlive_component", [], WIREVIEW),
    func: tag(null, [], WIREVIEW),
    func_block: tag("endfunc", [], WIREVIEW),
    on: tag(null, [], WIREVIEW),
    fill: tag("endfill", [], WIREVIEW),
    render_slot: tag(null, [], WIREVIEW),
    tag_header: tag(null, [], WIREVIEW),
    cond: tag(null, [], WIREVIEW),
  },
  filters: { str: filter("none", WIREVIEW) },
};

export function metadata(): Metadata {
  return {
    version: "1.1",
    wireview_version: "1.0.0",
    generated_at: "",
    components: {
      TodoList: component("TodoList", {
        template_name: "todo/list.html",
        template_path: `${DIR}/todo/list.html`,
        line_number: 10,
        fields: {
          title: { type: "str", annotation: null, default: null, required: true, description: null, in_state: true },
          items: { type: "list", annotation: null, default: [], required: false, description: null, in_state: true },
        },
        properties: { remaining: { type: "int", is_async: false, docstring: "Items left.", file_path: LIVE_PY, line_number: 30 } },
        methods: {
          joined: method(false),
          model_dump: method(false),
          add: method(true, { text: parameter("str") }, 21),
          toggleAll: method(true, {}, 25),
          anything: method(true, { kwargs: parameter(null, undefined, "VAR_KEYWORD") }, 27),
        },
      }),
      Counter: component("Counter", {
        kind: "live_component",
        template_name: "todo/counter.html",
        template_path: `${DIR}/todo/counter.html`,
        fields: { count: { type: "int", annotation: null, default: 0, required: false, description: null, in_state: true } },
        methods: { joined: method(false), increment: method(true, { by: parameter("int", 1) }) },
      }),
      Card: component("Card", {
        template_name: "card.html",
        template_path: `${DIR}/card.html`,
        fields: { title: { type: "str", annotation: null, default: "", required: false, description: null, in_state: true } },
        slots: { header: { required: true }, footer: {} },
      }),
      Flexible: component("Flexible", {
        fields: { n: { type: "int", annotation: null, default: null, required: true, description: null, in_state: true } },
        accepts_extra_kwargs: true,
      }),
    },
    function_components: {
      badge: {
        name: "badge",
        fqn: "app.ui.badge",
        module: "app.ui",
        file_path: "/proj/app/ui.py",
        line_number: 3,
        docstring: "A badge.",
        template_name: "",
        template_path: null,
        parameters: { text: parameter("str"), tone: parameter("str", "info") },
        slots: { icon: { required: true } },
      },
    },
    hooks: { Chart: { static_path: "app/hooks/chart.js", file_path: "/proj/app/static/app/hooks/chart.js", line_number: 4 } },
    modifiers: {
      prevent: { description: "Calls event.preventDefault()", has_argument: false, argument: null },
      stop: { description: "Calls event.stopPropagation()", has_argument: false, argument: null },
      debounce: { description: "Debounce", has_argument: true, argument: "number" },
      throttle: { description: "Throttle", has_argument: true, argument: "number" },
      key: { description: "A key", has_argument: true, argument: "text" },
      key_code: { description: "A key code", has_argument: true, argument: "number" },
      enter: { description: "Enter", has_argument: false, argument: null },
    },
    template_dirs: [DIR],
    template_builtins: {
      tags: {
        if: tag("endif", ["elif", "else"]),
        for: tag("endfor", ["empty"]),
        with: tag("endwith"),
        block: tag("endblock"),
        comment: tag("endcomment"),
        verbatim: tag("endverbatim"),
        filter: tag("endfilter"),
        load: tag(),
        extends: tag(),
        include: tag(),
        url: tag(),
        csrf_token: tag(),
      },
      filters: { upper: filter("none"), length: filter("none"), default: filter("required"), date: filter("optional"), safe: filter("none", FILTERS, true), escape: filter("none", FILTERS, true) },
    },
    template_libraries: {
      wireview: wireviewLibrary,
      static: { module: "django.templatetags.static", file_path: "/site/django/templatetags/static.py", tags: { static: tag() }, filters: {} },
      i18n: {
        module: "django.templatetags.i18n",
        tags: { blocktranslate: tag("endblocktranslate", ["plural"]), translate: tag() },
        filters: { language_name: filter("none") },
      },
      humanize: { module: "django.contrib.humanize.templatetags.humanize", tags: {}, filters: { intcomma: filter("none") } },
      thirdparty: {
        module: "thirdparty.tags",
        // `component`: another library's tag of the name, as django-components has
        tags: { mystery: tag(null, [], "/site/thirdparty/tags.py"), component: tag(null, [], "/site/thirdparty/tags.py") },
        filters: {},
      },
    },
  };
}

export const project = new Project(metadata());

export const FILES: Record<string, string> = {
  [`${DIR}/card.html`]: '<div {% tag_header %}>{% render_slot "header" %}{% render_slot "footer" note=x %}{% render_slot %}</div>',
  [`${DIR}/base.html`]: "<html>{% block body %}{% endblock %}</html>",
  [`${DIR}/todo/list.html`]: "",
  [`${DIR}/todo/counter.html`]: "",
  [LIVE_PY]: [
    "from wireview import Component",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "class TodoList(Component):",
    '    """A list."""',
    "",
    "    title: str",
    "    items: list = []",
    "",
    "    def helper(self):",
    "        title = 1",
    "",
    "",
    "",
  ].join("\n"),
};

/** `null`: a workspace without metadata. */
export function env(path: string, withProject: Project | null = project): Env {
  return {
    project: withProject ?? undefined,
    path,
    readFile: (file) => FILES[file],
    isFile: (file) => file in FILES,
    templateNames: () => Object.keys(FILES).filter((file) => file.startsWith(`${DIR}/`)).map((file) => file.slice(DIR.length + 1)),
  };
}

export const LIST = `${DIR}/todo/list.html`;
export const COUNTER = `${DIR}/todo/counter.html`;
export const PARTIAL = `${DIR}/partials/row.html`;

/** A template with `▮` where the cursor is: the text without it, and the offset. */
export function cursor(source: string): { text: string; offset: number } {
  const offset = source.indexOf("▮");
  if (offset === -1) throw new Error("no ▮ in the source");
  return { text: source.slice(0, offset) + source.slice(offset + 1), offset };
}

export function parse(text: string, withProject: Project | undefined = project): TemplateDoc {
  return parseTemplate(text, withProject);
}
