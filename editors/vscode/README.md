# Django Wireview

> 한국어: [README.ko.md](./README.ko.md)

Everything you need to edit Django templates, in one extension — no other Django extension required.
In a [django-wireview](https://github.com/itda-work/django-wireview) project it also understands your
components, handlers and slots, so a misspelled name is flagged in the editor before you ever render the page.

## Install

The extension ID is `itda.django-wireview`.

- **VS Code**: install it from the [Visual Studio Marketplace](https://marketplace.visualstudio.com/items?itemName=itda.django-wireview),
  search for "Django Wireview" in the Extensions view, or run `code --install-extension itda.django-wireview`.
- **Cursor, VSCodium and other editors that use Open VSX**: install it from
  [Open VSX](https://open-vsx.org/extension/itda/django-wireview).
- Every version's `.vsix` is also attached to its GitHub release (tag `vscode-v<version>`), for machines that
  cannot reach either registry.

## Requirements

- VS Code 1.100 or later.
- For the project-aware features (Django's tags and filters, components, handlers, diagnostics): a Django project with
  django-wireview 1.0 or later installed, and a Python interpreter that can run its `manage.py`. The extension runs
  `python manage.py wireview_lsp` and reads metadata format 1.1 or a later 1.x (django-wireview 1.0 to 1.2) or 2.x
  (django-wireview 1.3 and later). If the format is too old, the status bar and the output channel ask you to upgrade
  django-wireview; if it is too new, they ask you to upgrade the extension.
- Without django-wireview — in any Django project — syntax highlighting, the HTML features and the snippets still work.

## Features

**A Django template language.** Files matching `**/templates/**/*.html` open as `django-html`. Once the project's
metadata has been read, an `.html` file in any directory the template engine searches is switched to `django-html` as
well, even when the directory is not called `templates` (`DIRS` in `TEMPLATES`, including directories set through
environment variables; `wireview.associateTemplateDirs`). `{% %}`, `{{ }}` and `{# #}` are highlighted in text, in
attribute values and inside `<script>`; nothing inside `{% comment %}` or `{% verbatim %}` is treated as a tag.
`Ctrl+/` comments with `{# #}`. Typing `{%` adds `%}` and typing `{#` adds `#}`; the line after a block tag is indented,
and `{% endif %}` or `{% else %}` is outdented again.

**HTML keeps working.** Switching the language away from `html` does not cost you anything: tag and attribute
completion, hover, automatic closing tags, linked editing of tag pairs and folding all still work
(`vscode-html-languageservice`). Emmet is enabled too, and the `wire-*` attributes such as `wire-hook` appear in the
attribute list.

**Django's tags and filters.** The extension does not ship its own list: it reads them from the project's template
engine, so you get the built-ins of the Django version you have installed and every library you can `{% load %}`,
third-party ones included.

- Completion: tag names (a block tag is inserted together with its end tag, and the `endif` or `else` the enclosing
  block is waiting for comes first), libraries in `{% load %}`, filters after `|`, template paths in `{% extends %}` and
  `{% include %}`, and variables (fields, and the names `{% for %}` and `{% with %}` introduce).
- Hover and go to definition: a tag or filter takes you to the Python function that registers it, a library to its
  module, a template path to the file.
- Folding for every block tag (from `{% if %}` to `{% else %}`, and from there to `{% endif %}`).

**django-wireview.**

- Completion: the names and arguments of `{% component %}`, `{% live_component %}` and `{% func %}` (and their `_block`
  forms); events, modifiers, handlers and handler arguments in `{% on %}`; slots and `let:` in `{% fill %}`;
  `{% render_slot %}`; `wire-hook`; `wire-viewport-*`.
- Hover and go to definition: component → class, argument → field declaration, handler → method, hook → the line of the
  JS file that registers it, variable → field or property. In Python files, strings such as
  `template_name = "todo/list.html"` become links to the template.
- Snippets for templates (`wv-template`, `wv-component-block`, `wv-form`, `wv-upload`, …) and for Python
  (`wv-component`, `wv-handler`, `wv-function-component`, `wv-test`, …).

**Diagnostics.** The extension reports only what Django or django-wireview would raise when reading or rendering the
template, and stays silent when it is not sure: a name passed in a variable is not checked, and without metadata
nothing is reported.

| Code | Severity | What |
|------|----------|------|
| `unknown-component`, `not-a-live-component`, `live-component-needs-id`, `unknown-function-component` | Error | An unknown component, a regular component in `live_component`, a LiveComponent without an `id` |
| `unknown-argument`, `missing-argument` | Warning | An argument that is not a field, a required field that is not passed |
| `unknown-handler`, `not-a-handler` | Error | A handler the component rendering the template does not have, a method the client cannot call |
| `unknown-handler-argument` | Warning | An argument the handler does not take |
| `invalid-event`, `unknown-modifier`, `modifier-argument` | Error | The event name and modifiers of `{% on %}` |
| `missing-slot` | Error | A required slot that is not filled |
| `unknown-hook` | Information | A `wire-hook` name no hook file registers |
| `tag-not-loaded`, `filter-not-loaded` | Error | A tag or filter used without its `{% load %}`, or before it |
| `unknown-tag` | Warning | A tag no library defines |
| `unknown-filter`, `unknown-library` | Error | A filter or library that does not exist |
| `filter-argument`, `filter-not-permitted` | Error | The wrong number of filter arguments; `escape` or `safe` in `{% filter %}`, which Django refuses (whichever function was registered last under that name) |
| `unclosed-block`, `unmatched-end` | Error | A block that is never closed, an end tag with no opening tag |
| `template-not-found` | Warning | An `{% extends %}` or `{% include %}` path that is not in any template directory |

**SQL per line, from the dev server.** In development (`DEBUG`), django-wireview newer than 1.3.0 writes which template
line or property ran each SQL statement of a render into `.wireview/render-queries/` beside `manage.py`. The extension
reads it and puts the count at the end of the line, as an inlay hint: `6 queries`, `⚠ 6× same query` for an N+1, or
`1 query per render` on a property's `def` line. The tooltip shows the statements, the component and how long ago the
render was. For each component class the latest render counts, so once you fix an N+1 and the page is drawn again the
number goes away, whether the component was drawn on its own or inside another one.

A number is shown only where it is certain to belong to that line:

- the file is saved — while you edit, its numbers are hidden;
- for a template, the source the server's running template was compiled from is this very file (a SHA-256 the server
  writes). A dev server still running a cached compile of an older file shows nothing for it until it renders the new
  one, which django-wireview's template reload does right away;
- for a property, the file is unchanged since the server loaded the module (its `mtime` and size, to the nanosecond),
  and its `def` is on that line. The body that ran is not checked;
- the render was at most `wireview.renderQueries.maxAge` minutes ago (a component still being drawn is written again
  every minute).

One process writing at a time is supported: `runserver`, a single `uvicorn` or `daphne`; with several workers, give each
its own `DEBUG_RENDER_QUERIES_DIR`. When the records it has read show two processes writing over the same span of time,
the extension shows nothing and says why in the output channel. That detection is best effort: the server does not
write a render again that it already wrote within the minute, so writers that do overlap can go unnoticed, and their
counts can then be wrong. The format is described in
[docs/features/render-queries.md](../../docs/features/render-queries.md#편집기로-보내기) (Korean).

## How it works

For each workspace folder the extension finds `manage.py`, runs `python manage.py wireview_lsp --output <file>` and
reads the JSON it writes: the project's components, template directories, tags and filters. The file goes to the
extension's own storage, not into your project. Saving a Python file runs the command again. If a run fails (Django
does not start, or the file you are editing has a syntax error), the last metadata that worked stays in use and the
status bar shows a warning; click it to open the output channel.

Commands: `Wireview: Refresh Project Metadata`, `Wireview: Go to Component…`, `Wireview: Show Output`,
`Wireview: Clear Render Queries` (forgets the counts read so far; the files are the server's and stay).

**Restricted Mode.** In an untrusted workspace the extension starts no process and reads no metadata file — running
`manage.py` executes the project's code, and the paths in the metadata are where go to definition takes you. Results
left by an earlier session are not used either. Syntax highlighting, snippets and the HTML features keep working, and
the status bar shows `Restricted Mode`; the metadata is built as soon as you trust the workspace. The render queries
the dev server writes are not read either. Until then, the settings that decide what runs or what is read
(`pythonPath`, `managePy`, `metadataCommand`, `metadataPath`, `renderQueries.directory`) ignore the workspace's values.

## Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `wireview.pythonPath` | `""` | The Python that runs `manage.py`. Empty: the interpreter the Python extension selected, then `.venv` next to `manage.py` (or at the folder root), then `python3` (`python` on Windows) |
| `wireview.managePy` | `""` | Path to `manage.py`, relative to the folder. Empty: the shallowest one |
| `wireview.metadataCommand` | `[]` | The whole command, when an interpreter alone is not enough, e.g. `["uv", "run", "python", "manage.py", "wireview_lsp"]`. The extension appends `--output <file>` |
| `wireview.metadataPath` | `""` | Read and watch this file instead of running a command. Something else writes it with `manage.py wireview_lsp --output` |
| `wireview.refreshOnSave` | `true` | Run the command again when a Python file is saved |
| `wireview.associateTemplateDirs` | `true` | Open `.html` files in the directories the template engine searches as `django-html` |
| `wireview.diagnostics.enable` | `true` | Diagnostics |
| `wireview.html.enable` | `true` | HTML completion, hover and closing tags in `django-html` |
| `wireview.renderQueries.enable` | `true` | Show the dev server's SQL count at the end of template and property lines |
| `wireview.renderQueries.directory` | `""` | Where the dev server writes them, relative to the folder: the `DEBUG_RENDER_QUERIES_DIR` of `WIREVIEW`. Empty: `.wireview/render-queries` beside `manage.py` |
| `wireview.renderQueries.maxAge` | `30` | Minutes a component's last render is shown for |
| `wireview.renderQueries.mapRelative` | `false` | For a server in a container: also match a record to the file at its path relative to the server's `BASE_DIR`, under the directory of `manage.py`. A file with the same contents at that place is taken for the one the server ran |

## Known limitations

- **Every `.html` under a `templates` directory, and in any directory the template engine searches, becomes
  `django-html`.** If plain HTML that is not a Django template lives there, map it back to `html` with
  `files.associations`: `{"**/templates/static-site/**/*.html": "html"}`. The extension leaves alone any file
  `files.associations` maps to `html`, and once you switch a document back to `html` with the language picker it does
  not touch that document again. Templates read from places the engine does not search (mail templates loaded through
  a separate `Engine`, for instance) can be added the other way: `{"**/emails/**/*.html": "django-html"}`. Switching by
  directory happens after the metadata is read, so in Restricted Mode only `**/templates/**` applies. A file opened as
  `html` still gets diagnostics when it is inside a template directory.
- **If Django only runs inside a container,** open VS Code inside it with Remote Development (Dev Containers, SSH,
  WSL). The paths in the metadata are the ones Django sees, and they do not match those of an editor running outside.
  The render queries name files by the server's paths too; `wireview.renderQueries.mapRelative` is the fallback when
  the editor stays outside.
- Render queries are counted per component class, not per instance: two instances of a class with different data show
  the one drawn last (the tooltip names its id). LiveComponents drawn under the same parent are each their own render,
  so an N+1 across siblings shows as one sibling's count. Handlers, background tasks and `Broadcast` items are not
  counted yet, and neither is a slot that a LiveComponent draws after its parent's render.
- The end tag of a block tag is read from the source of the tag function. A third-party block tag whose source cannot
  be read is not treated as a block: nothing is said about its end and intermediate tags, so there are no false
  warnings, but there is no folding or end-tag completion for it either.
- Handler diagnostics run only when some component uses the template as its `template_name`. Fragments pulled in with
  `{% include %}` and stream item templates are not checked, because there is no telling which component renders them
  (completion offers the handlers of every component).
- `wire-*` attributes are read the way a browser parses HTML: only in start tags (not in comments, inside `<script>` or
  in end tags), and only the first of two attributes with the same name. Where it is not certain where something ends —
  `<![CDATA[` inside SVG or MathML, a `<script>` containing both `<!--` and `<script`, HTML inside SVG's
  `<foreignObject>` — attributes after that point are not checked. After an end tag that matches no open SVG element
  (`</p>` inside `<svg>`, or a `</div>` that closes a `<svg>` opened differently in two branches) the rest is read as
  HTML; since a browser may still consider itself inside SVG there, checking stops at the next `<![CDATA[`. Values
  containing character references such as `&#97;` are not checked either.
- The metadata format is documented in
  [docs/features/editor-support.md](../../docs/features/editor-support.md) (Korean); other editors can read the same JSON.
