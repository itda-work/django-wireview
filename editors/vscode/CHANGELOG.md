# Changelog

The extension is versioned on its own (semver, git tags `vscode-v<version>`); the
library it reads is versioned by git tags `v<version>` (see the repository's
CHANGELOG.md). It reads the metadata of `manage.py wireview_lsp` version 1.1 or a
later 1.x (django-wireview 1.0 to 1.2), and 2.x; and the render queries the dev
server writes (#188) in format 1.x.

A version's section says "Unreleased" until the commit that publishes it dates it;
the release workflow refuses a tag whose section has no date
([release procedure](../../docs/ROADMAP.md#vs-code-확장-릴리스-절차)).

## [0.2.1] - 2026-10-09

### Fixed

- A component class drawn several times in one record -- sibling LiveComponents, or a loop of nested
  components -- that ran one statement from one place once each is one row of its snapshot, its counts
  summed: the hint reads `⚠ 3× same query`, not `⚠ 1× same query · 3 queries` (#189).

## [0.2.0] - 2026-10-09

### Added

- The SQL count of each template line and property the dev server's renders ran, as inlay hints at the end
  of the line (#188): `6 queries`, `⚠ 6× same query` for an N+1, `1 query per render` on a property's `def`
  line, with the statements, the component and the render's age in the tooltip. The extension reads the JSON
  lines django-wireview (1.4.0 or later) writes in development to `.wireview/render-queries/` beside
  `manage.py`, following each process's numbered segments as they grow, and keeps the latest render of each
  component class, so a fixed N+1 clears on the next render. A template line is shown only while the file is
  saved and is the source the server's running template was compiled from (a SHA-256 the server writes); a
  property only while the file's stat is the one the server saw, to the nanosecond, and its `def` is on that
  line. Nothing is shown past `wireview.renderQueries.maxAge` minutes, while the records read show two
  processes writing over the same span of time (best effort: the server skips repeated renders, so writers
  that overlap can be missed), or in Restricted Mode, where the records are not read. A line of a format major this extension does not know
  is left, and said once in the output channel.
- Settings `wireview.renderQueries.enable`, `.directory`, `.maxAge` and `.mapRelative` (for a server in a
  container: match a record by its path relative to the server's `BASE_DIR`), and the command
  `Wireview: Clear Render Queries`.

## [0.1.0] - 2026-10-08

### Added

- Published to the Visual Studio Marketplace and Open VSX as `itda.django-wireview`, with an icon.
- The README, the Marketplace and Open VSX page, is in English; the Korean text is `README.ko.md`.
- The `django-html` language: a TextMate grammar over HTML for `{% %}`, `{{ }}` and `{# #}`
  (in text, attribute values and scripts), comment toggling, auto-closing of `{%` and `{#`,
  block-tag indentation, and `**/templates/**/*.html` associated with it. Once the metadata is
  read, an `.html` file in any directory the project's template engine searches is switched to it
  too (`wireview.associateTemplateDirs`), unless `files.associations` names it `html` or the user
  switched it back.
- HTML support in `django-html` files from `vscode-html-languageservice`: tag and attribute
  completion (with the `wire-*` attributes), hover, closing tags as `>` and `/` are typed, folding,
  linked editing. Emmet is on for the language.
- Completion, hover and go to definition for Django's tags and filters and for django-wireview's
  components, arguments, events, modifiers, handlers, slots, hooks and variables, all read from the
  project through `manage.py wireview_lsp`; template paths in `{% extends %}`, `{% include %}` and
  Python strings are links.
- Diagnostics for what Django or django-wireview would raise: unknown components, handlers, tags,
  filters and libraries, tags and filters not loaded, missing ids, arguments and slots, invalid
  events and modifiers, unclosed blocks, templates not found.
- Snippets for templates and for Python.
- The metadata runs again when a Python file is saved; the last metadata that worked is kept when a
  run fails. Commands: Refresh Project Metadata, Go to Component, Show Output.
- A run that is no longer wanted (the folder closed, the metadata source changed) or that takes
  longer than 120 seconds is stopped with the processes it started, within the limits below. On
  macOS and Linux the command runs under a small `/bin/sh` supervisor that leads a process group of
  its own; the group gets SIGTERM, then SIGKILL two seconds later. The supervisor stays until that
  SIGKILL, so a process a `wireview.metadataCommand` wrapper such as `uv run` left in the group is
  killed even when the wrapper died on the SIGTERM. It keeps itself there with shell builtins, not
  with a program found on the user's PATH, and runs the command in the foreground, so the command
  starts with SIGINT and SIGQUIT as they were. Signals are sent only while Node has not
  reported the supervisor's exit. A process that moved to a group of its own is out of reach. On
  Windows `taskkill /T /F` ends the tree, which no longer reaches what an exited process started.
  The run itself is over as soon as it is stopped, so the next one is not held up.
- Restricted Mode: in an untrusted workspace nothing runs and no metadata file is read, not the
  last session's either; the grammar, the snippets and the HTML support work. Trusting the
  workspace starts the metadata.
- Metadata 2.0 (#162), which describes the framework's methods once at the top instead of on every
  component, is read as 1.1 was: the inherited methods are put back on each component when the
  metadata is loaded, so a framework name in `{% on %}` is still "not a handler", with its hover
  and its definition. 1.1 is read as before. Metadata of a later major asks for a newer extension.
