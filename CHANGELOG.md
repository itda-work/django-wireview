# Changelog

All notable changes to django-wireview are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Git tags `v*` are the
version source of truth; `pyproject.toml` is bumped in the release commit.
Feature-level history is tracked by GAP number in `docs/FEATURE-GAP.md`.

The django-reactor era changelog (2.x) is preserved in
[docs/legacy/CHANGELOG-reactor.md](./docs/legacy/CHANGELOG-reactor.md).

## [Unreleased]

### Security

- `live_session` draws an authentication boundary around a page (`GAP-009`, `#58`). A project
  declares one with `live_session("admin", authorize=...)`, puts views inside it with
  `@admin.view`, and a component says where it belongs with `_live_sessions = {"admin"}`. The
  `authorize` predicate is one function with two enforcement points -- the view runs it before
  it produces a byte, and `command_join` runs it before it mounts anything -- because a join
  that refuses later cannot recall HTML that already shipped. A halted mount now renders
  nothing and leaves the repository, where it used to skip `joined()` and render anyway; that
  applies to a `{% component %}` in a dead render, a LiveComponent a parent's render created,
  and the root of a join. Crossing a boundary in the browser becomes a full page load rather
  than a body morph, checked where a link click, a `popstate` and a server `push`/`redirect`
  meet, and against the response rather than the requested URL. `user_logged_out` closes the
  sockets it authenticated. `wireview.W010` reports a `_live_sessions` naming a session nobody
  declares, and a component guarded only by `_on_mount` in a project that has boundaries.
  `docs/features/live-session.md` has the whole surface.
- A connection the session re-read refused cannot simply ask again (`#58`). The bookkeeping was
  keyed on "have we subscribed" rather than on the re-read's verdict, so a second join skipped
  the check the first one had failed. The re-read also kept the store it had just loaded, and a
  backend clears the key when it finds nothing there -- which erased the key this connection got
  from the cookie and made every later check vacuous.
- Re-login invalidation publishes to the topic the retired connections are actually on (`#58`).
  It named the generation with the nonce alone while those sockets had subscribed under a
  fingerprint taken from the whole session, so the message went somewhere nobody was listening.
- `testing.mount()` refuses what the server refuses (`#58`). It set the halt flag but left the
  component renderable, so a unit test could show a guard letting markup through on a path where
  the server stops it. A test helper that disagrees with the server about a boundary is worse
  than no test.
- A mount that raises is a refusal on the template paths too (`#58`). `{% component %}` and
  `{% live_component %}` let the exception out without giving up the instance, so on a live
  render it stayed registered and answering events while the join above it was torn down.
- A mount that raises is a refusal, not a pass (`#58`). `Component._mount` used to leave
  `{"halt": True}` as the only refusal, so a hook whose authorization query failed with a
  database error left the component registered and answering events, and a LiveComponent child
  in that state was rendered along with the parent. Every path now treats an exception the way
  it treats a halt -- nothing rendered, nothing left in the repository -- and then lets the
  exception carry its traceback where that path already did.
- A nested `{% component %}` runs its mount hooks on the live path too (`#58`). It used to skip
  them on the theory that the join had covered the page. The join had covered the *page*: a
  `live_session` hook meaning to refuse one nested component never ran, and the component became
  an event target as well as markup. Hooks run once per instance, so this costs one sync/async
  bridge per instance rather than one per render, and nothing at all for a component with no
  hooks on a page with no boundary.
- The authentication generation is a nonce written by `login()`, not the session key (`#58`).
  On the signed-cookie backend `session_key` is the whole signed cookie, so it changes whenever
  anything is written to the session -- a fingerprint built on it moved for reasons that had
  nothing to do with authentication, reloading open pages and pointing a later logout at a topic
  nobody was on.
- Entering a boundary re-reads the session from its backend and replaces the connection's
  snapshot with it (`#58`). The auth-invalidation topic is only subscribed on the first join
  inside a boundary, so a client that held an open socket and delayed that join missed the
  logout published in between. Replacing rather than only comparing matters for a policy that
  reads something other than authentication out of the session: that does not move the
  fingerprint, so a comparison would pass it on data that is no longer there.
- `WIREVIEW["STATE_ACCEPT_LEGACY"]` no longer applies once a `live_session` is declared (`#58`).
  An old token names no boundary and nothing in it says whether its page had one; accepting one
  for a component that declares no `_live_sessions` settled the connection on "no policy" and
  skipped the view's own `authorize` entirely. `wireview.W010` reports the combination.

- Logging in again retires the generation it replaces (`#58`). A step-up or re-auth overwrites
  the session's generation nonce, so no later logout could name the sockets still holding the
  old one. (A *different* user logging in flushes the session before any signal fires, so that
  generation cannot be named at all; logging out first is what retires it.)

### Fixed

- `wireview.testing.mount()` takes `live_session=`, so a component's boundary behaviour can be
  unit-tested the way the rest of its lifecycle already could (`#58`).
- `@session.view` keeps an `async def` view -- and an async class-based view -- async (`#58`).
  Django decides how to call a view by inspecting what it is handed. On a CBV the allowed path
  hid the problem, because `dispatch`'s own coroutine passed straight through; the refusal
  returned a plain response into an `await`.
- Boosted navigation announces the new location only once the destination is admitted (`#58`).
  `newLocation` is what makes the client send `params_changed`, and it fired before the fetch,
  so the page being left handled the destination's query under the authentication it was
  leaving behind. Work queued for a navigation (the body morph, and the component joins that
  follow it) is now dropped when a newer navigation starts or when the destination turns out to
  be across the boundary.

- The signed `data-state` envelope is now v2 and carries the page's `live_session` and the
  authentication generation it was issued under (`#58`). Signing the policy name on its own
  would not have helped: pairing a public page's *valid* signature with a protected
  component's *valid* state needs no forgery at all, so both have to travel inside the same
  envelope as the state. A page outside every boundary writes neither field and keeps the
  token it always had. v1 tokens are refused like the pre-v1 formats -- pages open across the
  upgrade reload once -- and `WIREVIEW["STATE_ACCEPT_LEGACY"]` decodes them as "no boundary",
  so a page under a policy still turns them down.

- The signed `data-state` is now bound to the component class it was issued for and expires
  (`#76`). It used to sign the state alone while the class name travelled beside it unsigned,
  so a signature issued for one component could be presented as another whose fields fit, and
  nothing ever aged out. `sign_state()` now signs a versioned envelope
  `{"v": 1, "n": <class FQN>, "d": <state>}` with `TimestampSigner(salt="wireview.state.v1")`,
  and the join path verifies it with `max_age=WIREVIEW["STATE_MAX_AGE"]` (14 days by default)
  and refuses a state whose class differs from the one the client named. The two pre-v1
  formats carry no class, so they are rejected unless `WIREVIEW["STATE_ACCEPT_LEGACY"]` is
  turned on for a rollout window; `docs/DEPLOYMENT.md` has the upgrade note. A render reuses
  its token while the state is unchanged and younger than `WIREVIEW["STATE_REFRESH_AFTER"]`,
  so `data-state` stays byte-identical across no-change renders and partial diffs are
  unaffected. One consequence: two components registered under the same simple name in
  different modules (`wireview.W003`) used to mount whichever registered last; now the
  envelope names the exact class, the simple name resolves to the other one, and the join is
  refused with `reload`. Fix the collision or reference the class by `app:Name` or FQN
- An upload `ref` is now validated on registration (`#84`). The client picks the ref and it
  became the prefix of the upload's temp filename unchecked, so a ref like
  `x/../../victim/pwn` escaped the temp directory whenever a `wireview_<name>` directory
  already existed there — reachable on a shared host with a world-writable `/tmp`.
  `UploadRegistry.add_entry()` refuses anything outside `[A-Za-z0-9_-]{1,64}` with the
  `ValueError` the consumer already reports as an upload error, and `create_temp_file()`
  sanitizes the ref again before it reaches a path

### Added

- Components can read the Django session as `self.session` (`#68`, GAP-029). Phoenix hands the
  session to `mount/3`; wireview hands the same thing to every component and, unchanged, to the
  third argument of the `_on_mount` hooks. `self.session` is a read-only mapping over the
  session data with `self.session.session_key` beside it, which is what code identifying an
  anonymous visitor (one vote per browser, a star rating, a cart) needs. Two examples were
  already writing `self.wire.session_key`, an API that never existed, and died with
  `AttributeError` on every connection. It is read-only because a WebSocket has no response to
  carry `Set-Cookie` and Channels never flushes a session changed on the socket, so a write
  that appeared to work would be lost; sessions are still written in a view, which is also the
  only place that can create one. `session` joins `_exclude_fields` by default, so session data
  never reaches the signed `data-state`. The socket reads the session once at connect, off the
  event loop, so a later `self.session[...]` inside an async handler is a dict lookup rather
  than a query that would raise `SynchronousOnlyOperation`; the HTTP render stays lazy and a
  page that never looks at the session pays nothing. Tests inject one with
  `mount(Component, session={...}, session_key="s1")`
- Chunked uploads work on any worker (`#83`). Chunks arrive over HTTP, so nothing routes them
  to the process holding the WebSocket; the endpoint used to look the upload up in a
  process-local dict and answer `404 Component not found` on every other worker, valid token
  or not. It now keeps no per-upload state: the signed token is the only authority, and the
  bytes go to a path computed from it (`<UPLOAD_TEMP_DIR or system temp>/wireview-uploads/
  <connection>/<digest>.part`). The worker that owns the connection learns what happened from
  the `upload.progress`, `upload.completed` and `upload.error` messages the endpoint publishes
  to the group it already joined, so `this.uploads` and `consume_uploads()` stay true across
  processes. N workers on one host need no extra infrastructure; several hosts need a shared
  volume as `UPLOAD_TEMP_DIR`, or external uploads. `docs/features/chunked-uploads.md`
- `WIREVIEW["SIGNING_KEY"]` and `WIREVIEW["SIGNING_KEY_FALLBACKS"]`, defaulting to Django's
  `SECRET_KEY` and `SECRET_KEY_FALLBACKS` (`#83`). With the chunk endpoint stateless, the
  upload token is the only proof a chunk has, and its key's lifetime is the upload's lifetime.
  A dedicated key separates that from Django's: rotating `SECRET_KEY` no longer kills uploads
  in flight or the `data-state` of open pages (14 days), and rotating wireview's key does not
  end everyone's session. Both are read at call time, so a rotation needs no restart. A key
  set to the empty string silently means `SECRET_KEY`, so `manage.py check` reports it as
  `wireview.W009`
- `manage.py wireview_upload_gc` removes chunk files older than
  `WIREVIEW["UPLOAD_TOKEN_MAX_AGE"]` (`--dry-run` to look first). A stateless endpoint accepts
  chunks for a component that is already gone until its token expires, and a worker that dies
  takes its cancel path with it, so what can be left behind is bounded by that age. The write
  path also sweeps once every ten minutes per process, so a deployment without cron is still
  bounded
- `{% live_component_block "Name" id="..." %}…{% endlive_component %}` passes slots to a
  LiveComponent (GAP-036, `#82`): `{% fill %}`, the default slot and `let:` bindings work as
  in `{% component_block %}`. Fills rendered in the parent's pass reach the child without the
  parent's diff markers, a change in that content re-renders the child, and the parent's diff
  still carries only the reference

### Fixed

- The chunk endpoint returned 500 on every request in a project with
  `DATABASES[...]["ATOMIC_REQUESTS"] = True` (`#83`). `UploadView.post` is async and Django's
  handler refuses to wrap an async view in a transaction, raising before the view runs. The
  endpoint opens no database connection at all, so `wireview.urls` now registers it as
  non-atomic on every configured alias. Only the two-worker end-to-end test found this: every
  other test called the view directly, walking past the handler that raises
- A chunk was never checked against any size limit (`#83`). The endpoint wrote whatever
  arrived and only stopped calling the upload incomplete once `client_size` bytes had come in,
  so a client that under-reported its size could write past the limit its entry was validated
  against. The signed token now carries the size the entry was accepted with, and a chunk that
  would exceed it is refused with 413 and the partial file dropped
- `WIREVIEW["UPLOAD_TEMP_DIR"]` had no effect (`#84`). It was exported and documented, but
  `create_temp_file()` never read it, so every chunked upload went to the system temp
  directory whatever the setting said. It is now read on each call, the directory is created
  if missing, and a configured directory that cannot be used raises `ImproperlyConfigured`
  rather than silently falling back to local disk — the failure mode that matters for an
  operator who pointed uploads at a shared volume. `manage.py check` reports an unusable
  setting up front as `wireview.W008`
- Upload registries were keyed by component id alone, so two connections on the same page
  interfered with each other (`#77`). Component ids are only unique within a page and
  templates commonly fix them (`{% component 'X' id="bookmarks" %}`), so a second connection
  overwrote the first's registry, a `leave` on either popped whatever was under that id and
  deleted its temp files, and both shared the `wireview_upload_<component_id>` progress group.
  `disconnect()` released nothing at all, leaking registries and temp files, because the
  upload group is not in `self.subscriptions`. Every upload artefact is now scoped by a
  per-connection owner id that `WireviewConsumer.connect()` mints: the index is keyed by
  `(connection_id, component_id)`, the token signs
  `connection_id:component_id:upload_name:ref` (salt `wireview.upload`, aged out with
  `WIREVIEW["UPLOAD_TOKEN_MAX_AGE"]`, which was defined but unused), and the progress group is
  one per connection, joined when the connection's first registry is registered so a page
  without uploads costs no group on the channel layer. `disconnect()` releases every registry
  the connection owns, a
  LiveComponent child's registry is registered after its `joined()` (it never was), and a
  chunk that finishes writing after a cancel returns 410 and leaves no temp file behind. The
  index is still per process: chunked uploads must reach the process holding the WebSocket,
  which `docs/DEPLOYMENT.md` now spells out, and a shared registry is `#83`
- Diff markers went missing at random when templates are not cached (`DEBUG = True`, or an
  explicit loader list): the marker engine remembered prepared templates by `id()`, a freed
  template's id was reused by a fresh one, and that one was skipped, so its next diff became a
  full render. Prepared templates now carry a stamp on the object. Found as a flaky test
  after #75 added locmem-template tests
- `_on_mount` hooks never ran: `_run_on_mount_hooks()` was defined and documented as an
  authentication boundary but had no call site (GAP-021, `#75`). They now run once per
  instance, before `joined()`, on every path that mounts a component — the HTTP (dead)
  render, the WebSocket join, a LiveComponent the parent's render named, and
  `wireview.testing.mount()`. A halt skips the remaining hooks and `joined()` while the
  component still renders, so a hook that redirects gets its `url_change` frame over the
  WebSocket and a `<meta http-equiv="refresh">` on an HTTP render. Hooks receive the
  request or connection session, and `manage.py check` reports an `_on_mount` entry
  wireview cannot call as `wireview.W007`

- A nested component rendered with `{% component_block %}` lost its slots when it re-rendered
  on its own event: `render_diff` built the context without `slots`. The component now keeps
  the slot content the enclosing template passed (`wire.slots`) and uses it in every render

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

- The chunked upload endpoint took a connection segment: `/__wireview_upload__/<connection_id>/
  <component_id>/<upload_name>/` (`#77`). The server sends the endpoint to the client in the
  `config` upload op, so apps only notice if they hard-coded the path. Tokens issued before the
  upgrade no longer validate (new salt and new contents); a client that reloads gets fresh ones
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
- **Wire protocol.** New outbound command `reload` with payload `{"id", "reason"}`
  (`"expired"`, `"legacy"`, `"invalid"`). The server sends it instead of mounting when a join's
  root state cannot be used, and the client does a full page load so the server can re-render
  with the current auth context and fresh tokens. The client refuses to reload twice within 30
  seconds (`sessionStorage["wireview:last-reload"]`) so a misconfigured server cannot loop the
  page. A child state that fails to decode is dropped from the restore map with a warning and
  the join continues (`#76`)
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
