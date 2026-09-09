# Changelog

All notable changes to django-wireview are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Git tags `v*` are the
version source of truth; `pyproject.toml` is bumped in the release commit.
Feature-level history is tracked by GAP number in `docs/FEATURE-GAP.md`.

The django-reactor era changelog (2.x) is preserved in
[docs/legacy/CHANGELOG-reactor.md](./docs/legacy/CHANGELOG-reactor.md).

## [Unreleased]

### Fixed

- LiveComponents are owned by their parent (`#78`, `#79`, `#80`,
  `docs/design/live-component-ownership.md`). `joined()` ran twice per child on every connect
  (once through the parent's render, once through the child's own join), a child that first
  appeared through `params_changed` or a broadcast never got `joined()` at all, a parent
  re-render reset whatever the child had changed on its own, and a component that left the
  page normally never received `leaving()`. Now the client does not join `wireview-live`
  elements, a parent re-render calls `update()` only with props whose value changed since the
  parent's previous render, `leave` runs `leaving()` and cascades it to nested LiveComponents,
  and a child the parent stops rendering is retired with `leaving()` by the server
- A second `join` for an id this connection already holds (new DOM after boost navigation)
  re-ran `joined()` on the same instance and never called `leaving()` (`#81`). The instance
  that already joined now leaves, with its LiveComponents, and a fresh one joins, so
  `joined()` is once per instance for Components too. An instance a parent's template pass
  created but that never joined is still adopted by its own join
- `examples/livecomp`: a reset counter no longer snaps back when another counter fires. The
  new E2E scenario `test_a_reset_counter_survives_an_unrelated_parent_rerender` keeps it so
- `bench/compare.sh` copied the current bench *into* the base worktree's existing `bench/`
  directory, so the base commit ran its own bench code. New scenarios never showed up

### Changed

- **Wire protocol.** A live render of `{% live_component %}` emits a component reference
  `{"c": id}` in the parent's diff instead of the child's markup, and the `render` frame
  carries the children's diffs under `children: {id: diff}`. The client registers the children
  before it patches the DOM and substitutes each child's current HTML when it builds the
  parent. One frame and one paint per event; the parent's diff never carries child markup
  again. Measured with `make bench-compare BASE=790dab7 ARGS="--skip-ws"`: first join 4,062 B in
  4 frames → 2,409 B in 1 frame; a child reset followed by an unrelated parent re-render
  2,095 B in 2 frames → 233 B in 1 frame (`docs/design/live-component-ownership.md` §5)
- `consumer.send_render` is the one place that runs a child's `joined()`/`update()`/`leaving()`
  and renders it; the six `_flush_pending_live_components()` call sites are gone. Nested
  LiveComponents (grandchildren) are handled by the same recursion, up to depth 8
- Subscriptions are synced before the operations queued during `joined()` are flushed, so a
  broadcast sent from `joined()` cannot leave before this connection has joined the group
- `wireview/static/wireview/wireview.js`: diff data is applied when the frame arrives and only
  the DOM patch waits for the next animation frame, so a component's diffs apply in the order
  the server sent them whether they came alone or inside a parent's `children`
- Rebuild `wireview.min.js` (`make build-js`) after upgrading: the new server frames need the
  new client

### Changed

- `docs/FEATURE-GAP.md` now matches the code. Three rows claimed features were missing that
  had shipped (form auto-recovery, telemetry, `stream(reset:)`), every remaining gap carries a
  GAP number and an issue, Nested LiveViews is marked as a deliberate exclusion with the reason,
  and the coverage summary is a count of the table rather than a round number

### Changed

- Answered why uvicorn costs 4× more RSS per connection than daphne (`#61`): it negotiates
  WebSocket permessage-deflate by default and daphne does not offer it, so every uvicorn
  connection holds a zlib deflate and inflate context. Measured at 2,000 connections: daphne
  45.9 KB, uvicorn 211.5 KB, uvicorn with `--ws-per-message-deflate false` 50.6 KB — a 160.9 KB
  gap against 158.8 KB for a compressobj/decompressobj pair in the same interpreter. wireview's
  diffs are small and compress badly (a typical event payload shrinks 16%), so `docs/DEPLOYMENT.md`
  now recommends turning it off unless the app pushes large HTML, and the benchmark grew a
  `--server uvicorn-nodeflate` lane to measure both ways

### Changed

- The teaching apps moved from `tests/testproj/` to `examples/` and became a checked
  deliverable (`#66`). Each one is a single concept with a `tests.py` and a README that links
  to its tutorial, `make test` runs `pytest tests examples`, and CI therefore fails when an
  example rots. Adding those tests found four examples that were already broken: `search` and
  `notifications` awaited a `QuerySet`, `quiz` and `rating` read a `wire.session_key` that has
  never existed (the page passes the session key in now), the notifications stream item template
  used `notification` where a stream item is called `item`, and the same app called a
  `self.abroadcast()` that is not a component method. `tests/testproj/` keeps the Django project
  it always was — settings, URLconf, and the `bookmarks` baseline that guards the agent skill
- Library tests that render through channels' `database_sync_to_async` now carry
  `pytest.mark.django_db`. They passed only while no earlier test in the process had left a
  connection open inside a transaction, which made the suite sensitive to the order its two
  halves run in

### Fixed

- Streams now survive a re-render and update in place (GAP-028, `#67`). A render carries the
  template's *empty* stream container, and morphing it over the live one deleted every streamed
  item, so a handler that changed state and re-streamed — a filter or sort switch — ended up with
  an empty list. `[wire-stream]` containers are now skipped by the morph. Streaming an id that is
  already on screen also replaced the item where it stands instead of adding a second copy, which
  is what Phoenix does and what makes a creation and an update the same call
- Client-side navigation no longer hijacks a component event. `boost` intercepted every same-origin
  link click without checking `defaultPrevented`, so `{% on "click.prevent" %}` on an `<a href="#">`
  fired the event and *then* reloaded the page, resetting the component state the event had just
  changed (`#67`)
- `stream(limit=N)` took the WebSocket connection down. The payload carries `limit`, the client
  handles it, but `WireviewConsumer.component_stream_op()` did not accept it, so the channel-layer
  hop raised `TypeError` and killed the ASGI application — with a green unit-test suite, because
  `mount()` stops at `WireviewMeta.send_stream_op` and never crosses that hop. GAP-014 had been
  marked complete on those tests. `tests/test_streams.py` now checks every stream payload's keys
  against the consumer signature (`#65`)

### Added

- An agent skill for people *building apps* with wireview, canonical at `skills/wireview/`
  and shipped in the wheel (`#65`). `manage.py wireview_agent_setup` copies it into a project's
  `.claude/skills/`; it refuses to clobber an existing directory without `--force` and never
  replaces a symlink, which is how this repository dogfoods the same files through
  `.claude/skills/wireview`. The skill routes to `docs/features/` and `docs/tutorials/` instead
  of copying them, and `AGENTS.md` points agents that do not read `.claude/skills/` at it.
  See `docs/features/agent-skill.md`
- `tests/testproj/bookmarks/` — the app an agent built from that skill alone, kept as the
  baseline for the next skill change. Its E2E tests cover what `mount()` cannot: a form
  submit delivering its `name` fields as handler arguments, and stream items reaching the
  DOM. One is xfail against `#67`
- Django system checks for the traps that fail silently (`wireview/checks.py`, `#64`).
  `manage.py check` now reports a non-async event handler (`wireview.W001`) or lifecycle
  override (`W002`), two component classes sharing a simple name (`W003`), a missing
  `wireview.min.js` (`W004`) and `USE_HMIN` degrading partial diffs to token diffs (`W005`);
  `manage.py check --deploy` adds the in-memory channel layer (`W006`), which is the right
  choice for single-process development and only breaks fan-out across processes. Every
  message carries the fix, all are warnings so they cannot break a build, and the handler
  check calls the dispatcher's own predicates rather than reimplementing them. See
  `docs/features/checks.md`

### Fixed

- Method exposure now means "written in user code". `ComponentRepository._is_user_defined_method`
  stopped at `Component` in the MRO, so everything between a subclass and `Component` counted as a
  user handler: `LiveComponent.send_to_parent` and `update` were client-callable, and the
  `model_post_init` Pydantic injects into every component was only blocked by accident, by its
  positional-only signature. A name owned by any `wireview` or `pydantic` class is now blocked even
  when a subclass overrides it, so lifecycle callbacks stay parent-driven and a client can no longer
  forge a parent event that looks like it came from a child (#63)

### Added

- `tests/test_agent_docs.py` guards the agent harness against drift: every path, `make` target
  and GAP number cited by `CLAUDE.md` or a skill must exist, and each skill's frontmatter must
  name its own directory. It runs with `make test`, so a rename that turns the map into a lie
  fails in CI

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
