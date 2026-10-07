# Changelog

The extension is versioned on its own; the library it reads is versioned by
git tags `v*` (see the repository's CHANGELOG.md). It reads the metadata of
`manage.py wireview_lsp` version 1.1 or a later 1.x.

## [0.1.0] - Unreleased

### Added

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
