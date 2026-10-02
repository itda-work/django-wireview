# Changelog

The extension is versioned on its own; the library it reads is versioned by
git tags `v*` (see the repository's CHANGELOG.md). It reads the metadata of
`manage.py wireview_lsp` version 1.1 or a later 1.x.

## [0.1.0] - Unreleased

### Added

- The `django-html` language: a TextMate grammar over HTML for `{% %}`, `{{ }}` and `{# #}`
  (in text, attribute values and scripts), comment toggling, auto-closing of `{%` and `{#`,
  block-tag indentation, and `**/templates/**/*.html` associated with it.
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
