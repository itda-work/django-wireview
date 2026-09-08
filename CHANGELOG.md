# Changelog

All notable changes to django-wireview are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Git tags `v*` are the
version source of truth; `pyproject.toml` is bumped in the release commit.
Feature-level history is tracked by GAP number in `docs/FEATURE-GAP.md`.

The django-reactor era changelog (2.x) is preserved in
[docs/legacy/CHANGELOG-reactor.md](./docs/legacy/CHANGELOG-reactor.md).

## [Unreleased]

### Changed

- Agent harness: the working procedure moved out of `CLAUDE.md` into a `wireview-dev` skill
  (`.claude/skills/wireview-dev/SKILL.md`). `CLAUDE.md` now carries only what an agent needs in
  every session — the map and the prohibitions — and points at the skill for session start-up,
  issue and `wip` conventions, the definition of done, commits, the full command table and the
  benchmarking pitfalls

### Added

- Telemetry signals (GAP-022): `event_handled`, `component_rendered`, `diff_computed` and
  `broadcast_published` report duration and payload size from the event, render, diff and
  fan-out paths. Opt in with `WIREVIEW["TELEMETRY"]`; while off the instrumented paths take a
  shared no-op span and `make bench-compare` shows no change. See `docs/features/telemetry.md`

## [0.2.1] - 2026-09-08

### Added

- Published on PyPI: `pip install django-wireview` works. The release workflow now builds through
  `make ci-build` (which fails if the wheel is missing `wireview.min.js`), checks that the tag
  matches `pyproject.toml`, and publishes the sdist and wheel with PyPI trusted publishing (OIDC,
  environment `pypi`). No API token is stored anywhere. Releases up to v0.2.0 were GitHub-only

## [0.2.0] - 2026-09-08

The release that makes the SQLite-plus-Windows deployment premise real: a channel
layer that needs no Redis, partial HTML diffs that actually fire, a transport seam,
and reproducible benchmarks on macOS, Linux and Windows to back the claims.

### Added

- `make bench ARGS="--layer redis"` benchmarks channels_redis next to channels-nats, so the channel-layer
  choice can be made on measurements (`docs/design/transport-abstraction.md` §5-3). The benchmark starts
  the broker itself on a free port; `REDIS_URL` / `NATS_URL` point it at an existing one instead
- Windows benchmark lane and results: `make bench ARGS="--server uvicorn"` (also `uvicorn-wsproto`) picks the
  ASGI server, `bench/windows/` runs the same benchmark inside a Parallels Windows guest, and
  `bench/results/win11-parlab-*.json` hold the numbers next to the macOS control group `a993181-*.json`.
  daphne dies at about 500 connections per process on Windows (`select()` limit); a single-process uvicorn
  does not. `docs/DEPLOYMENT.md` gained the Windows single-server recipe (uvicorn × N behind Caddy,
  channels-nats, SQLite WAL) and `docs/design/transport-abstraction.md` §5-2 the measurements
- NATS channel layer support in the test project and the benchmark: `tests/testproj/settings_nats.py`,
  `tests/test_nats_layer.py` (cross-process broadcast reaches a consumer through channels-nats),
  `make bench ARGS="--layer nats --processes N"` with a broadcast fan-out measurement
- `bench/`: reproducible benchmarks (`make bench`) for render payload sizes, per-event cost,
  memory and WebSocket connection density, plus `make bench-compare BASE=<ref>` to benchmark a
  past commit in a throwaway worktree and print a side-by-side table
- Comprehensions and blocks: `{% for %}` output is one dynamic slot holding the item template's
  statics once and per-item dynamics, and `{% if %}` output is a nested block with its own
  statics. Adding, removing or changing items and switching branches now produce partial diffs
  instead of full renders (GAP-025). Client side lives in `wireview/static/wireview/rendered.mjs`
  and is unit-tested with `npm test`
- `wireview.core.transport`: `Outbound` and `Broker` interfaces with Channels implementations.
  Every channel-layer call now goes through them, so another connection layer only has to
  implement the two interfaces (GAP-026)
- `Rendered.to_dict()` / `from_dict()` so render snapshots can live outside the process
- `docs/implementation/wire-protocol.md` (message shapes in all four directions) and
  `docs/design/transport-abstraction.md` (measurements, options, remaining steps)
- `on_mount` hooks and `attach_hook()` / `detach_hook()` for lifecycle interception (GAP-021)
- `.claude/settings.json` with a permission allowlist and a PostToolUse hook that runs `ruff format` on edited Python files

### Changed

- NATS is now the layer this project targets. `make test-e2e` runs the browser suite on
  channels-nats and `tests/e2e.sh` starts a throwaway nats-server for it, so no broker has to be
  running first;
  `WIREVIEW_TEST_LAYER` (memory by default, nats or redis) picks the layer for the
  test project, and README and `docs/DEPLOYMENT.md` recommend it first. CI runs the E2E suite on
  NATS too, and channels-nats is a dev dependency now that it is on PyPI. channels_redis stays
  fully supported
- The event transpiler cache is a small pure-Python LRU instead of `lru-dict`, so wireview installs
  without a C compiler on platforms that have no `lru-dict` wheel (Windows ARM64). The dependency is gone
- `WireviewMeta` accepts a `broker=` argument; `channel_layer=` still works and builds a
  `ChannelsBroker`. Tests that patched `get_channel_layer` should patch
  `wireview.core.transport.get_channel_layer` instead
- `data-state` now carries a compact, zlib-compressed `Signer.sign_object` payload
  (`wireview.core.state`). The legacy `Signer().sign(json)` format is still accepted on join
- Rewrote `CLAUDE.md` as a compact agent guide (repository map, commands, conventions, gotchas) that links to `docs/` instead of duplicating API docs
- `make lint` now runs `ruff format --check` and djlint, and CI's lint job reuses it
- Release workflow attaches only the wheel (sdist removed)
- Package version unified to the latest tag (`pyproject.toml`, `package.json`)
- README and tutorials state the actual Python requirement (>=3.12)

### Fixed

- The published wheel and sdist now contain `wireview.min.js`. hatchling honours `.gitignore`,
  which ignores `*.min.js` as a build artifact, so every release up to v0.1.1 shipped a package
  whose `{% wireview_header %}` pointed at a file that was not there and no JavaScript loaded.
  `make ci-build` now fails if the wheel is missing it
- HTTP renders no longer leak diff markers into attributes (`value="<!--$0-->…"`); markers are
  stripped for non-live renders
- Variables inside `{% if %}` branches were never marked, so any change inside a conditional
  was a full render; branches are now wrapped like the rest of the template
- Empty variable output is now marked too, so a value toggling between "" and text no longer
  shifts every following index into a full render
- Partial HTML diffs never fired for live components: `{% tag_header %}` embedded the freshly
  signed `data-state` in the static parts, so the fingerprint changed on every render and every
  event shipped a full render. The signed state is now a dynamic part (GAP-024)
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

[Unreleased]: https://github.com/itda-work/django-wireview/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/itda-work/django-wireview/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/itda-work/django-wireview/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/itda-work/django-wireview/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/itda-work/django-wireview/releases/tag/v0.1.0
