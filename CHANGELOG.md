# Changelog

All notable changes to django-wireview are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Git tags `v*` are the
version source of truth; `pyproject.toml` is bumped in the release commit.
Feature-level history is tracked by GAP number in `docs/FEATURE-GAP.md`.

The django-reactor era changelog (2.x) is preserved in
[docs/legacy/CHANGELOG-reactor.md](./docs/legacy/CHANGELOG-reactor.md).

## [Unreleased]

### Added

- `on_mount` hooks and `attach_hook()` / `detach_hook()` for lifecycle interception (GAP-021)
- `.claude/settings.json` with a permission allowlist and a PostToolUse hook that runs `ruff format` on edited Python files

### Changed

- Rewrote `CLAUDE.md` as a compact agent guide (repository map, commands, conventions, gotchas) that links to `docs/` instead of duplicating API docs
- `make lint` now runs `ruff format --check` and djlint, and CI's lint job reuses it
- Release workflow attaches only the wheel (sdist removed)
- Package version unified to the latest tag (`pyproject.toml`, `package.json`)
- README and tutorials state the actual Python requirement (>=3.12)

### Fixed

- `Component._lifecycle_hooks` type annotation so `pyright` passes
- djlint formatting of the livecomp and slots test templates
- Duplicate test component class names (`ChildComponent`, `SimpleComponent`) that triggered registration warnings on every test run

### Removed

- Stale `MANIFEST.in` (referenced `reactor/` paths; hatchling is the build backend) and placeholder `docs/index.md`

## [0.1.1] - 2025-12-09

### Added

- Wheel build in the release workflow

## [0.1.0] - 2025-12-09

First tagged release of django-wireview, the Pydantic v2 / Django Channels
successor to django-reactor. Highlights over reactor: Streams, Presence, chunked
and external file uploads, AsyncResult and `assign_async`, `JS()` client
commands, JavaScript Hooks, Slots, Function Components, LiveComponent,
`temporary_assigns`, `params_changed`, flash messages, `push_title`, form
auto-recovery, viewport bindings, optimistic UI attributes, type stub
generation, and `mount()` testing utilities. See `docs/FEATURE-GAP.md` for the
Phoenix LiveView parity table.

[Unreleased]: https://github.com/itda-work/django-wireview/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/itda-work/django-wireview/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/itda-work/django-wireview/releases/tag/v0.1.0
