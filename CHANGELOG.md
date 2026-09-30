# Changelog

All notable changes to django-wireview are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Git tags `v*` are the
version source of truth; `pyproject.toml` is bumped in the release commit.
Feature-level history is tracked by GAP number in `docs/FEATURE-GAP.md`.

The django-reactor era changelog (2.x) is preserved in
[docs/legacy/CHANGELOG-reactor.md](./docs/legacy/CHANGELOG-reactor.md).

## [Unreleased]

### Fixed

- A sticky component rendered without an `id` was silently not sticky: `{% component %}` gave it
  a fresh `rx-<uuid>` on every page, which never paired with the next page's. It now gets an id
  derived from its class (`sticky-<module path>-<Class>`), the same on every page. A second
  id-less instance of the same sticky class on one page keeps a random id and logs a warning on
  the `wireview` logger, since two elements cannot share an id (#128).
- `import wireview` failed on pydantic 2.13 and later (1.0.0rc3, which is yanked):
  `LiveComponent.update_many` is annotated with `t.Self`, handler validation wrapped it in
  `validate_call`, and pydantic's refusal stopped being a `TypeError` in 2.13. Handler validation
  now wraps exactly what a client may call -- the dispatcher's rule, in one place
  (`wireview.core.handlers`) -- so framework and pydantic methods are never wrapped, overridden or
  not. A user handler whose signature pydantic cannot validate loses its validation with a warning
  in the `wireview` log instead of breaking the class. The test suite now runs on pydantic 2.13
  (`uv.lock`), and `make test-latest` and a CI job run it on the newest dependencies a fresh
  install gets (#127).
- Test harness: two test runs in one checkout no longer break each other. The test database was
  one fixed file, so concurrent runs created, flushed and dropped it under each other and failed
  with "readonly database" and "no such table", differently each time; each test process now has
  its own. `--ff` moved from pytest's `addopts` to the Makefile targets, so
  `pytest -p no:cacheprovider` starts again, and `make test-concurrent` runs the suite twice at
  once (#125).
- Test harness: the test project served HTTP through asgiref's `WsgiToAsgi`, which sends each
  response through `async_to_sync`; Uvicorn started the next request on a kept-alive connection
  inside that call, and it died with "CurrentThreadExecutor already quit or is broken" -- an
  ordinary form POST failing once in a full E2E run. It uses Django's ASGI handler now, as
  projects are told to, and `server_errors()` sees what Uvicorn logs, which it silently did not
  (#129).
- Test harness: with Django's ASGI handler, WhiteNoise's sync-only middleware served every static
  file as a sync iterator, and each one warned "StreamingHttpResponse must consume synchronous
  iterators" (126 more warnings per E2E run). The test project serves static files through
  Django's `ASGIStaticFilesHandler`, as daphne's and Channels' runserver do, and a run that records
  that warning now fails (`tests/testproj/warning_guard.py`).
- A live render read the component's properties on the event loop, so a plain property that used
  the ORM raised `SynchronousOnlyOperation` on a real server and the component could not join (the
  notifications example's recipient list). Properties are now read in the same worker thread as the
  template; async properties are still awaited on the loop (#120).
- `Component.stream()` iterated a QuerySet synchronously on the event loop, so every join of a page
  that streamed one failed on a real server (the bookmarks page). A QuerySet or any other async
  iterable is now consumed with `async for` (#120).
- The quiz example's handlers read a property that queried the database on the event loop; they use
  async queries now (#120).

### Changed

- The test suite, E2E and benchmarks no longer set `DJANGO_ALLOW_ASYNC_UNSAFE`, which hid every
  failure above while the suite was green. The suite refuses to start with it set; E2E tests exempt
  only Playwright's own thread, so the live server keeps Django's check as production has it
  (`conftest.py`, #120).
- `tests/test_doc_streams.py` reads every example a reader or an agent copies from -- the
  documentation's Python blocks, the `wireview` skill, the examples and the test project -- and
  fails on async code that evaluates a QuerySet synchronously (`list(qs)`, `reversed(qs)`,
  `[x for x in qs]`, `for x in qs:`) or passes one to `stream_insert()`. Handing a QuerySet to
  `stream()` as is stays the documented pattern (#121).
- A release tag no longer goes to PyPI untested. `release.yml` calls the whole `ci.yml` on the
  tagged commit -- the Python x Django matrix, the newest-dependencies run, E2E on NATS, lint,
  typecheck and the package build -- and a `smoke` job installs the built wheel on freshly
  resolved dependencies and imports it (`make ci-smoke`); `publish` waits for both. The yanked
  1.0.0rc3 wheel fails the smoke job (#122).

## [1.0.0rc3] - 2026-09-30

### Changed

- The session logic is `wireview.session.WireviewSession`, answering only through an `Outbound`;
  `WireviewConsumer` is its Channels WebSocket adapter. Nothing changes for an application --
  both are internal -- but a session can now be driven with no socket, scope or channel layer
  (GAP-027 step 1, #60).

### Added

- `docs/features/dead-view.md` says what a browser without JavaScript gets: the complete first
  render, links, and forms with an `action` posting to their view -- the same form a
  `{% on "submit.prevent" %}` handler takes when JavaScript is there. A JavaScript-disabled
  browser test holds it (GAP-034, #73).
- Sticky components: `class Meta: sticky = True` keeps a component's instance, state, element and
  hooks across a boosted navigation to a page that has it under the same id, as Phoenix's sticky
  LiveView. A move across a `live_session` boundary is a full load and ends it (GAP-033, #72).
- Toasts: `toast(user, message)` and `await atoast(...)` show a flash on the pages the recipient has
  open, and `{% wireview_toasts %}` in the layout receives them. A session key reaches a visitor
  who has not signed in, and `toast_channel()` names the channel for a receiver of your own. Taken
  from the notifications example, which every app copying it had to rewrite (#116).
- `LiveComponent.update_many(updates)`, a classmethod that receives every child of its class whose
  props changed in one parent render, as `(component, assigns)` pairs, so rows can load what they
  show in one query rather than one each. The default calls each `update()`. Phoenix's
  `update_many/1` (GAP-035, #74).

## [1.0.0rc2] - 2026-09-30

What 1.0 promises, settled before it is frozen (#119). A 1.0.0rc1 project has changes to make;
`docs/UPGRADING.md` walks through them.

### Removed

- `window.wireview.debounce()` and `throttle()`. Only the inline-script bindings #90 retired
  called them, and they shared one timer across the page, so two wrapped functions cancelled each
  other. Use `{% on "input.debounce.300" ... %}`, or your own timer.
- `window.wireview.exec()` from the documented API. Its argument is the JSON a `JS()` chain
  becomes, which is wire format; call `JS()` from the server or `{% on %}` instead.
- The `TRANSPILER_CACHE_SIZE` setting, and the inline transpiler it sized, which nothing had called
  since #90. `manage.py check` names the key if a project still sets it (`wireview.W014`).
- `Component.dom()` and the test helpers `view.dom_actions` and `view.clear_dom_actions()`.
  `dom()` was the only way to send a DOM action, nothing documented or tested it, and it took an
  internal enum, so `dom_actions` was always empty. The client no longer handles `append`,
  `prepend`, `insert_after`, `insert_before` or `replace_with` messages.
- `send_notification` and `asend_notification` from `wireview`. They did what `broadcast` and
  `abroadcast` do; use those. Also `ComponentNotFound` and `list_function_components`, which
  nothing documented, and `span` and `payload_size` from `wireview.telemetry`, which are its own
  instruments. All stay importable from their modules as internals.

### Changed

- `docs/features/navigation.md` says what `redirect_to`, `push_to`, `replace_to` and writing
  `self.wire.params` each fetch, keep and record. The README described `push_to` as pushing a URL
  "without fetching"; it fetches the page and swaps the body. `to` is positional-only, so a URL
  pattern argument called `to` reaches `reverse()`.
- `{% upload_button "name" %}` is an attribute, like `{% upload_drop_zone %}`:
  `<button type="button" {% upload_button "images" %}>Select</button>`. It rendered an opening
  `<button>` whose closing tag the template supplied, and took extra attributes as arguments.
- `Component.scroll_into_view(element_id, *, behavior, block, inline)`: the options are
  keyword-only. `/__wireview__` and `/__wireview_upload__/` are declared public paths that must be
  mounted at the root (`docs/COMPATIBILITY.md`).
- `wireview.dom.onBeforeElUpdated(callback)` adds a callback and returns a function that removes
  it. It replaced the one callback there was, so a second library's registration silently evicted
  the first's (Alpine's, say). Passing `null` no longer clears anything; call the returned function.
- Upload DOM events are `wireview:upload-added`, `-progress`, `-complete`, `-error` and `-cancel`,
  inside the `wireview:` namespace the public events use. The 0.x `upload:*` names are sent too
  until 2.0. All five are documented in `docs/features/external-uploads.md`.
- `window.wireview.send(element, name, args, options)` takes `eventType`, `commit` and `target` in
  one options object. It took `eventType` as a fourth argument, an options object fifth, and the
  LiveComponent target hidden in `args._target`.
- `SYNC_TRANSITION_WARNING_THRESHOLD` and `SYNC_TRANSITION_ERROR_THRESHOLD` are
  `DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD` and `DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD`, next to
  the `DEBUG_SYNC_TRANSITIONS` switch they belong to. `wireview.W014` names the old keys.
- `wireview_lsp` lists the modifiers the client runs. It read the retired transpiler's, which
  offered `inlinejs`, a modifier `{% on %}` refuses.
- `mount()`, `ComponentTestCase.mount()` and `follow_redirect()` take their options as keywords
  only, and field values also through `state={...}`. The options shared a namespace with the
  fields, so a field called `params` could not be set, and every option a later release added
  would have broken the component with a field of that name. `mount(Cls, user)` is a
  `TypeError`; write `mount(Cls, user=user)`.
- `handle_async(name, result)` receives an `AsyncResult`, the type `assign_async` already fills,
  instead of an Elixir-style `("ok", value)`/`("exit", exc)` tuple: read `result.ok` and
  `result.result`, or `result.failed` and `result.error`. An override that indexes the tuple
  raises `TypeError`, which the consumer reports as a crash.
- `docs/COMPATIBILITY.md` says what is public in fewer words that cover less: a `wire-*` attribute
  or a `wireview-*` class is public when `docs/features/` documents it, not because of its prefix.
  The markup template tags print (`wire-on-*` and its JSON, the upload attributes, `data-state`
  and the other root markers), hook members starting with `__`, what `wireview.debug` returns and
  the shape of the wire messages test helpers hand back are internal.
- `Component`'s public members are listed in `docs/features/component-api.md`; one without an
  underscore is internal unless it is there. The README's method tables, which had the wrong
  `allow_upload` signature and missed twelve methods, point there now.
- Every setting is in `docs/features/settings.md`; the README's block missed seven.
- `AUTO_BROADCAST`'s channel names are public and tested.
- An m2m change is announced on `<model>.<pk>.<field>` for both rows, whichever side's manager
  made it. The row the change was made from used a trailing `.<pk>` per related row instead, so
  a subscriber of `auth.user.1.groups` saw `group.user_set.add(user)` but not
  `user.groups.add(group)`, and a `clear()` reached no channel. A subscription to the old
  `<model>.<pk>.<field>.<pk>` form hears nothing now.

### Fixed

- `mount()` builds the component through `new()`, as a page and a join do. The tutorials teach
  overriding `new()` to read `wire.params`, and under `mount()` that override never ran.
- `abroadcast` fires `broadcast_published` like every other fan-out.
- `wireview.W002` checks every callback wireview awaits, not only `joined`, `update` and `destroy`.
- The client bundle's URL carries the package version instead of a fixed `?v=2`.
- The README and the first tutorial install `wireview.urls`; without it uploads 404. The nginx
  example upgrades `/__wireview__`, not `/ws/`.

## [1.0.0rc1] - 2026-09-29

The first release candidate for 1.0: the code of 0.7.0, with nothing to change on upgrade. The
public API is what `docs/COMPATIBILITY.md` lists; from 1.0 it changes only through the
deprecation path described there. The candidate stays out while new bug reports are watched and
the library is used in an application.

### Changed

- The session state table in `docs/implementation/wire-protocol.md` and the remaining steps of
  `docs/design/transport-abstraction.md` said the upload registry was a process-wide
  `views._registries` a session move could not carry. It has been the component's since #77, and
  the chunk endpoint keeps no state since #83 (#60).

## [0.7.0] - 2026-09-29

Bug fixes and small additions gathered from building an application on 0.6, with nothing to
change on upgrade. A loading state ends with its own answer (#118); a boosted navigation shows
where it landed after a redirect (#104); infinite scroll judges the list once the join has
landed, which takes protocol 5 (#112); type stubs are the same on every run (#109). Added:
`wire-update="ignore"` (#102), boosted forms with `wire-boost` and `wireview.visit()` (#103),
and `view.render_diff()` for tests (#117).

### Added

- Boosted forms and a navigation API. A form with `wire-boost` is submitted without a page
  load under `BOOST_PAGES` -- a GET to its query, anything else by fetch, the history entry at
  the page its redirect ended on. Forms opt in, since a login or logout form that boosted would
  keep a socket speaking for the old identity. `wireview.visit(url, {replace})` navigates the
  way a boosted link does, for code that had to click a hidden `<a>`.
  `docs/features/boost.md` documents boost for the first time (#103).
- `wire-update="ignore"`: after its first render no render touches the element -- its
  attributes, children or text -- as Phoenix's `phx-update="ignore"`. What a hook or a widget did
  to it stays, where `updated()` could only put it back after a morph had shown the gap (#102).
- `await view.render_diff()` on what `mount()` returns: what the next live render sends the
  client, or `None` when the render is skipped or nothing changed. `view.render()` always draws
  the whole page, so a handler that skipped the render its page needed -- a button left
  disabled -- passed every unit test; only a browser caught it (#117).

### Fixed

- Type stubs are the same on every run. A field typed with `AsyncResult` carried its schema
  object's repr, memory address included, into the stub, so `AUTO_GENERATE_STUBS` rewrote a
  tracked `live.pyi` on every DEBUG start -- noise in git and a conflict between worktrees. The
  LSP metadata had the same string (#109).
- Infinite scroll judges the list once the join has landed. `wire-viewport-bottom` was watched
  from the moment the join was sent, when a stream's first page had not arrived: the binding sat
  near the top and a long list asked for a second page, or not, depending on timing. The server
  now sends `joined` after everything `joined()` queued, to clients that speak protocol 5; with
  an older server the component's first render stands in for it (#112).
- A boosted navigation that follows a redirect shows where it landed in the address bar. The
  entry kept the requested URL, so a reload ran the redirecting view -- and its side effect --
  again (#104).
- The loading state an event starts -- the loading classes and `wire-disabled-with` -- ends
  with that event's answer, the render or `error` carrying its `ref`. Any render of the
  component ended it, so the join's answer landing after a click, or the render of a broadcast
  the component was busy with, brought a button back enabled mid-save; under load
  `test_a_change_marks_its_element_loading_until_the_answer` failed on it now and then. A morph
  that rewrites a waiting element puts its mark back, a closed connection or a component that
  joins again after an error clears its marks, and a server that does not echo refs is answered
  by the component's next render other than the join's. The release removes every
  `wireview-<event>-loading` class it finds rather than a fixed list (#118).

## [0.6.0] - 2026-09-29

The release that makes the documented contracts hold before 1.0. A temporary assign that was
reset no longer erases what the page shows (GAP-006, #111); hooks belong to the component
they sit in, hear `destroyed()` when it leaves, and get their own `pushEvent` replies (#107,
#108); every supported row of `docs/FEATURE-GAP.md` now names the test that runs it (#110),
and the documentation's code runs (#113). Four changes can need a code or test change; see
`docs/UPGRADING.md` ("0.5에서 0.6으로").

### Added

- `view.broadcasts` and `view.presence_broadcasts` on what `mount()` returns: what the component
  broadcast, for a test to assert on. The testing guide sent readers to `view.wire.broadcasts`,
  though `self.wire` is public only for its navigation; `docs/COMPATIBILITY.md` now says the
  testing surface is `MountedComponent`'s documented members (#114).
- Every supported row of `docs/FEATURE-GAP.md` names the tests that run the feature, and
  `tests/test_feature_gap.py` fails when a row has none, names one that does not exist, or the
  overview's counts drift from the table. The audit that filled it found and fixed the defects
  below; two it could not fix in place are #111 and #112 (#110).
- `docs/features/flash.md`: `put_flash()`, the `[wire-flash]` container and its classes, and
  where a flash ends and a toast begins -- a toast is a flash sent from somewhere else, over a
  channel the recipient's component subscribes to. GAP-011 shipped without a guide (#41).
- Model instances in component state, wherever they are: a single field, a list, a dict's
  values, an `AsyncResult`'s result. They are signed as their pks and loaded back on join by
  following the field's annotation -- one query per list, in order; a row deleted meanwhile
  drops out of a list and leaves a single field `None`. Only a field typed exactly as a model
  was handled before, so `list[Book]` or `AsyncResult[Stat]` failed to sign and the component
  could not render; the search and dashboard examples and several tutorials had been reworked
  around dicts to avoid it, and are back to model instances (#113).
- `tests/test_doc_examples.py`: every Python block of the user-facing docs parses, and the
  mistakes that recurred -- an awaited QuerySet, `self.abroadcast`, an awaited `skip_render()`,
  a plain `for` over `consume_uploads()`, `allow_upload(UploadConfig(...))`, a template calling
  `JS()` with arguments -- are refused (#113).

### Deprecated

- `view.wire.broadcasts` and `view.wire.presence_broadcasts`: use `view.broadcasts` and
  `view.presence_broadcasts`. They raise `WireviewDeprecationWarning` and go in 2.0 (#114).

### Changed

- The signed state (`data-state`) leaves out `Meta.temporary_assigns` fields. A list reset after
  the render was signed while the render held it, so ten thousand rows went into a page
  attribute. **Upgrading:** a component joined again (a reconnect) starts such a field from its
  default. Load it in `joined()`, as the guide does; a field filled only by the template tag
  (`{% component "X" messages=... %}`) is empty after a reconnect (#111).
- `MountedComponent.call()` meets the checks a browser's event meets: a name a client cannot
  call (`_private`, a lifecycle method, a mixin's framework method such as
  `presence_set_typing`) raises `AssertionError`, and arguments the handler does not take are
  dropped. A test that passed through `call()` could exercise what no page can. **Upgrading:**
  a test that meant the method, not the event, calls it directly:
  `await view.component.presence_set_typing(True)` (#110).
- A hook inside a nested component belongs to that component. A parent's `push_event` reached
  the hooks of the components it rendered, because the parent joined first and took them.
  **Upgrading:** push from the component that holds the hook, or move the hook to the parent's
  own markup (#107).
- Submitting a form shows the `wire-feedback-for` feedback of all its fields, touched or not,
  as Phoenix does. The guide told applications to do this with an inline `onclick`, which a
  strict CSP blocks (#110).

### Fixed

- A temporary assign that was reset is not a change, as in Phoenix. The next render -- for
  whatever reason -- sent the reset list and the list vanished from the page, so
  `temporary_assigns` held for one render only. A part that read nothing but reset fields now
  keeps its previous value and is not sent; a part that also read another field renders from
  what it has, so no change is hidden. The rule, and what a `{% if %}` or a loop over the list
  does, is in `docs/features/temporary-assigns.md`; the design in
  `docs/design/temporary-assigns-change-tracking.md` (#111).
- Hooks: a hook belongs to the component it sits in. A parent joins before its nested
  components and its scan took their hooks, so what a nested component pushed reached
  nothing (#107).
- Hooks: a component that leaves the page -- its parent stops rendering it, a boost
  navigation -- calls `destroyed()` on all its hooks. The MutationObserver watches inside the
  root and never saw the root go, so a hook on the root, and every hook under a root removed
  whole, kept its timer or microphone (#107).
- Hooks: a row that a render moves keeps its hook. The MutationObserver reports a move as a
  removal after the morph, so the hook was destroyed on an element still on the page and
  never mounted again (#107).
- Hooks: a `pushEvent` reply reaches the callback that asked. Refs were counted per component
  from `hook-1` and a reply went to the first component holding the ref, so two components
  waiting at once swapped answers. One counter serves the page (#108).
- `view.render()` draws a component whose template holds `{% component %}` or
  `{% live_component %}`. It raised `AttributeError` from the stand-in repository, so such
  components were tested without their HTML. `mount()` now renders with the real repository in
  its HTTP-render mode: children are drawn inline as the page's first response draws them, and a
  LiveComponent child's `joined()`/`update()` still belong to the consumer (#115).
- `mount()` without `params` shares one params dict between the component and its repository, as
  its docstring said. An empty dict was swapped for a fresh one, so after `follow_push()` the
  component's `self.wire.params` kept the old query (#115).
- A component whose `assign_async` failed can be rendered. `AsyncResult` kept the exception in
  a field the signed state could not hold, so the failure path -- the one the docs show with
  `{% if stats.failed %}` -- raised on every render. The state carries the state, the result
  and `error_message`; the exception stays on the server (#113).
- `{% class %}` and `{% cond %}` read a dotted name the way the rest of a template does:
  `forloop.counter0` and a `.values()` row's `row.title` failed as attribute lookups on a dict
  (#113).
- Examples: poll and quiz counted ids the browser sent without checking they
  belonged to that poll or question (and quiz scored the same answer twice); the notifications
  example's `"pulse 500ms"` transition, left over from the #110 guard, raised on every new
  notification. Their tests now render and try foreign ids (#113).
- Examples: notifications belong to a user. Everyone saw everyone's notifications, and a
  handler deleted or marked read whatever id the browser sent. Components now listen on the
  signed-in user's channels only -- the stored notification on auto-broadcast's related
  channel `auth.user.<pk>.notifications`, the toast on `toasts.user.<pk>` -- and every handler
  starts from the user's own rows. The send buttons never enabled in a browser: `set_title`
  skipped every render, so the one that should drop `disabled` never went out. A two-browser
  E2E covers both (#41).
- The documentation's code runs. Every Python block of the user-facing docs was executed by a
  second model and each finding reproduced before it was fixed: about seventy, across the
  tutorials (03-06, 08-10, 12-15), the feature guides, README, ARCHITECTURE, PERFORMANCE,
  DEPLOYMENT and ROADMAP. Two findings about `self.wire.params` were not defects: the consumer
  sends the new query string after each event (#113).
- A `temporary_assigns` field with no default is left alone, as the guide says; it was set to
  Pydantic's `PydanticUndefined`. A field defaulting to `None` is reset to `None`; it was never
  reset (#113).
- README examples run: undeclared fields (`expanded`, `user_id`), an awaited QuerySet, the old
  upload API, and a JS section that called `JS()` with arguments inside a template, which Django
  cannot parse (#113).
- A page reconnects every component after a drop. A render painted just after the socket
  closed re-ran the joins on the dead socket, marking the elements live; the reconnect then
  skipped them, so they never joined again and their events were dropped. A component joins
  only on an open socket, and a late paint keeps the offline marks (#110).
- Leaving a `live_session` through a redirect adds one history entry, not two. The full page
  load that hands the page over used `location.assign`, which added an entry for the
  redirect's destination after the one the boosted click had pushed; Back then went to the
  redirecting URL, which sent the browser forward again (#110).
- `wireview_stubs` lists in `__wireview_handlers__`, and `wireview_lsp` marks `is_handler` on,
  only what a client can call -- the check every event meets. Both counted a mixin's framework
  methods (`presence_join`) as handlers. `wireview_stubs --app` takes an app's label or name;
  it compared the first segment of the module path, so `--app blog` found nothing in
  `apps.blog` (#110).
- `docs/features/temporary-assigns.md` says what happens: the next render, whatever it is for,
  empties a cleared field on the page too, and the field rides in that render's signed state.
  FEATURE-GAP marks `temporary_assigns` partial until #111 (#110).
- A `handle_async` that raises recovers like a raising handler: logged, and the component is
  joined again from its last rendered state. It used to vanish inside the task -- not logged,
  and the render skipped (#110).
- `wire-viewport-top` and `wire-viewport-bottom` are documented, in the streams tutorial,
  which also stops awaiting a QuerySet and keeps the page short with `limit=` instead of a
  list held in state (#110).
- An upload consumed with `read()`, `open()` or a `with` block is consumed. Only `save_to()`
  marked it, so an upload read in `consume_uploads()` kept its `max_entries` slot -- with the
  default of one, the second upload was refused -- and came back on the next call. Every upload
  the loop moves past is now consumed and its temp file deleted (#110).
- A component is rendered when an upload's progress or error arrives, so `{{ entry.progress }}`
  and `entry.errors` in a template change as the upload goes, as the upload tutorial shows them.
  They stayed as they were until the upload finished (#110).
- The `external=` upload callback may be `async def`; its result was never awaited (#110).
- The upload tutorial uses the API that exists: `allow_upload(name, ...)` rather than an
  `UploadConfig` argument, `async for` over `consume_uploads()`, `{% upload_drop_zone %}` for
  drag and drop, `STORAGES` instead of the removed `DEFAULT_FILE_STORAGE`. The external upload
  guide no longer draws a server-side progress bar the server has no numbers for (#110).
- The Django form example in `docs/features/form-feedback.md` runs. Its template looped over a
  `form` the component never exposes and used a `get_item` filter that does not exist (#110).
- Hooks keep working across a reconnect. `reconnected()` was never called, and a hook's
  instance was stranded: the component that joined again had a new hook manager, so the hook
  got no `updated`, no `handleEvent` and no `pushEvent` reply. The joining component now takes
  the hooks over and calls `reconnected()` (#110).
- `wire-auto-recover` recovers. Its handler was sent before the component joined again and
  the server dropped it, and without a handler it only put back values the page still had. It
  now runs after the join with the form's current values: `wire-auto-recover="handler"` calls
  the handler with `form_data`, a bare `wire-auto-recover` fires the form's change binding, as
  Phoenix does (#110).
- `push_title()`, `put_flash()` and `clear_flash()` work on a live page. The consumer had no
  handler for their messages, so the lookup raised and closed the socket: calling one dropped
  the connection it was meant for. A command without a handler is now logged and dropped, and
  a test reads every command the server sends from the source and checks it has one (#110).
- `attach_hook()` hooks run. They were registered and documented -- the lifecycle guide uses
  them for rate limiting and an audit log -- but nothing called them, so a `handle_event` hook
  that halted stopped nothing. `handle_event` runs before the handler (also in
  `MountedComponent.call()`), `handle_params` before `params_changed` on every path, and
  `after_render` after each live render (#110).
- `JS().navigate(url, replace=True)` navigates. It only replaced the URL in the address bar;
  it now loads the page in place of the current history entry (#110).
- `wire-disabled-with` on a form's submit button works, as the documentation shows it. The
  form carries the binding, so only the form was marked; a submit now also marks the form's
  submit buttons that ask for it (#110).
- A `JS()` chain that ends in `push` gets the loading classes and `wire-disabled-with`, like a
  binding that names a handler. It sent the event with no feedback at all (#110).
- `transition="fade-in 200ms"` raises `ValueError` instead of leaving a class named `200ms`
  on the element and not waiting. The string is class names; the duration is the tuple's
  second item. The notifications example and tutorial used the string form (#110).

## [0.5.0] - 2026-09-27

The release that gathers every breaking change before 1.0 (#93): component configuration
in `class Meta:`, `wireview.__all__` as the whole public API, the legacy paths and
`USE_HMIN` removed, and Django 5.2 or newer. It also closes a cross-site WebSocket hijacking
hole (#96), keeps the connection when component code raises (#94), and ends a component's
async tasks with it (#95).

**Upgrading from 0.4:** `docs/UPGRADING.md` walks through every breaking change below.

**Upgrading.** `{% on %}` renders a `wire-on-<event>` data attribute instead of an inline
`on<event>` handler, and only the new bundle understands it. `{% wireview_header %}` bumps the
bundle's cache key; a page that serves the bundle some other way has to drop its cached copy.

### Added

- The public API is `wireview.__all__` and nothing else (#98). `docs/COMPATIBILITY.md` says
  what is public and how it is retired. Names the documentation used to import from
  submodules are exported: `PresenceMixin`, `PresenceTrackerMixin`, `PresenceConfig`,
  `PresenceUser`, `PresenceState`, `UploadConfig`, `UploadEntry`, `ConsumedUpload`,
  `AutoBroadcast`, `ModelAction`, `send_notification`, `asend_notification`,
  `invalidate_authentication`, `get_function_component`, `list_function_components`,
  `iter_exposed_handlers` and the `telemetry` module. Every example in the docs now imports
  `from wireview`; tests/test_public_api.py keeps it that way.
- `wireview.W014`: a key in `settings.WIREVIEW` wireview does not read (#100). A typo names
  the key it resembles; a removed key says what replaced it.
- `wireview.WireviewDeprecationWarning`, raised by everything on its way out.
- `py.typed`, so type checkers read the package's annotations (#98).

### Removed

- The legacy paths, before 1.0 fixes the API (#99). `WIREVIEW["STATE_ACCEPT_LEGACY"]` and
  every state format before the v2 envelope: a page carrying one reloads, and the refusal
  is logged as `invalid` (the `legacy` reload reason is gone). The pre-#83 upload token,
  which carried no size or extension and so skipped both checks. The inbound `query_string`
  command, which no client sent. The token diff for HTML without markers, and with it
  `WIREVIEW["USE_HTML_DIFF"]`, which only ever switched that diff: such HTML is now one
  static part, sent whole when it changes.
- `WIREVIEW["USE_HMIN"]` and `wireview.W005` (#100). django-hmin stripped the diff markers, so
  every change sent the component's whole HTML: a trade that loses, which the recommended
  settings in the deployment and performance guides nonetheless made. Compress the socket
  (permessage-deflate) instead.

### Deprecated

- `wireview.component`: import from `wireview` instead. It warns and is removed in 2.0.

### Changed

- **Breaking:** Django 5.2 or newer (#93). wireview supports the Django releases Django
  supports, on Python 3.12 and up: today Django 5.2 LTS, 6.0 and 6.1 on Python 3.12, 3.13
  and 3.14. Django 5.0 and 5.1 are past their end of life. `make test-matrix` runs the
  whole grid locally, since CI only runs when dispatched. `docs/COMPATIBILITY.md`.
- **Breaking:** component configuration moved into `class Meta:` (#99). The underscore
  class attributes read as private and sat among the registry's own underscore names.
  `_template_name`, `_subscriptions`, `_temporary_assigns`, `_exclude_fields`, `_slots`,
  `_on_mount`, `_live_sessions` and `_presence_config` are now `template_name`,
  `subscriptions`, `temporary_assigns`, `exclude_fields`, `slots`, `on_mount`,
  `live_sessions` and `presence` in the Meta. There is no compatibility path before 1.0:
  an old name is a `TypeError` that says where it went (Pydantic had turned it into a
  private attribute, so it would otherwise have configured nothing in silence), and so is
  a key the Meta does not know. A subclass inherits each key its Meta does not set, so a
  base that guards itself with `on_mount` or `live_sessions` keeps guarding.
  `exclude_fields` adds to `user`, `wire` and `session` instead of replacing them.
  Subscriptions that depend on state come from overriding `get_subscriptions()`, which
  replaces `@property def _subscriptions`.
- Settings are read when used (#100). `wireview.settings` copied `settings.WIREVIEW` into
  constants at import, so `override_settings(WIREVIEW=...)` changed nothing and tests had to
  monkeypatch the module, which pinned values past the test. An assignment to the module is
  now an `AttributeError` pointing at `override_settings`. The merged settings are cached
  against the `settings.WIREVIEW` object, so a running project pays an identity check.
  Components no longer keep their own template cache, which outlived a change to
  `TEMPLATES` and switched on with `DEBUG`; Django's cached loader (on by default) does
  that job. A project that lists `loaders` without the cached loader now compiles on each
  render, as its Django views already do.
- **Breaking:** `Component.deffer` is `defer` (#99). The public part of `self.wire` is
  `params`, `redirect_to`, `replace_to` and `push_to`; the rest is plumbing, reached
  through `Component`'s own methods (`docs/COMPATIBILITY.md`).
- Event bindings no longer put script in the markup, so a Content Security Policy without
  `'unsafe-inline'` holds (#90). `{% on "keyup.enter" "save" %}` renders
  `wire-on-keyup.enter="{…json…}"` and the bundle delegates from `<html>`; the upload tags lose
  their inline `onchange`/`onclick` the same way, and `{% wireview_header %}` puts the request's
  CSP nonce on its `<style>` and `<script>` tags. Modifiers keep their meaning. Along the way:
  the same event can now be bound twice on one element (`keyup.enter` and `keyup.esc`; the
  second inline `onkeyup` used to be dropped, which left three of `examples/search`'s four key
  bindings dead), debounce and throttle keep one timer per element and binding instead of one
  for the page, and loading classes, `wire-disabled-with` and `JS()` act on the element that
  carries the binding rather than the innermost element clicked. A server binding on a page
  that is not live does nothing, not even `.prevent`, so a form submits to its `action` as it
  would without JavaScript. `.inlinejs`, which never worked through the tag, is now a clear
  template error pointing at `JS()`. `docs/features/csp.md`.

- List items that move no longer resend the list (GAP-030, #69). Inserting, removing
  or reordering items in a `{% for %}` loop used to resend every item after the edit
  point; now the unchanged items go as runs of the previous list, `{"k": [[start,
  length] | {"d": [...]}, ...]}`, and only new or changed items carry their dynamics.
  Items are matched by their rendered content, so templates need no keys. On 500 items
  an insert at the front is 3.9 KB instead of 20 KB (most of what is left is the
  signed state). The positional form stays wherever it is as small (an edit in place,
  an append, a truncation), byte for byte.
- The client names the diff protocol it speaks when it connects (`/__wireview__?vsn=2`),
  and the server never sends a newer form. A page still running an older bundle names
  none and keeps receiving exactly what it did, so a rolling deploy cannot hand it a
  shape it would render as `[object Object]`. `docs/implementation/wire-protocol.md`.

### Fixed

- The system checks and the stub and LSP generators called `asyncio.iscoroutinefunction`,
  deprecated in Python 3.14 and removed in 3.16; they use `inspect.iscoroutinefunction`. On
  3.14 the test suite went from 129,088 warnings to 3.
- **Security:** a page on another site could open this site's socket with the user's
  cookies (cross-site WebSocket hijacking, #96). The quick start's `asgi.py` had no Origin
  validation, and nothing in wireview checked. The consumer now refuses, before accepting,
  a handshake whose Origin host is not in `ALLOWED_HOSTS` (Django's rule for Host, with
  localhost under DEBUG when it is empty), whatever `asgi.py` wraps around it. A handshake
  without an Origin header is not from a browser and connects. `WIREVIEW["CHECK_ORIGIN"]`
  turns it off. `docs/DEPLOYMENT.md`, and `SECURITY.md` for reporting vulnerabilities.
- Three documented examples did not run (#99): `self.wire.push_event` (form feedback) and
  `component.wire.user` (external uploads) do not exist, and the LiveComponent tutorial
  summed its children through `self.wire.repo`, which is not there either. The tutorial now
  keeps what the children report, as `examples/livecomp` does. A test holds the
  documentation to the public part of `self.wire`.
- A client could call a class defined in a component's body (#99). The exposure rule
  took any callable the user's class defined, and a class is callable, so a `user_event`
  naming a nested class instantiated it. `class Meta:` made every component have one; a
  class is never a handler now.
- `LiveComponent` wrapped its handlers the way `Component` did before a76836c: a
  classmethod stayed bound to the class that defined it and a staticmethod received the
  instance. It shares `Component`'s wrapping now.
- `from wireview import function_component` could return a module (#98). The decorator's
  submodule had the same name, and importing it (the template tags do) replaced the
  package attribute with the module, so `@function_component` failed with "module is not
  callable" in a running project. The submodule is now `function_components`.
- A page that loses its connection no longer submits its forms natively (#97). While the
  socket was down a server binding did nothing, `.prevent` included, which is the rule for
  a page that was never live: Enter in a `submit.prevent` form reloaded the page and lost
  its state, and a `click.prevent` link reached by keyboard navigated away. A page that has
  been live now keeps `.prevent` and `.stop` while disconnected and sends nothing; before
  the first connection nothing changes. What was sent on the socket while it was down (a
  hook's `pushEvent`, upload messages) was queued without a bound and delivered after the
  reconnect to instances that no longer existed; it is now dropped, and only what was sent
  before the first connection waits for it. `docs/features/csp.md`.
- A component's async tasks end when it leaves (#95). Nothing cancelled what `start_async`
  and `assign_async` started, so after a closed tab, a `leave`, a replaced instance or a
  handler that raised, the task ran to the end, kept the instance in memory and then asked
  for a render nobody would receive. They are now cancelled after `leaving()`. A task
  replaced under the same name no longer drops its replacement from the bookkeeping when
  its cancellation finishes (the replacement could not be cancelled any more), a cancelled
  task no longer triggers a render, and `assign_async` holds its task so it cannot be
  garbage collected while it runs. `docs/features/async-operations.md`.
- Server code that raises costs its component, not the connection (#94). A handler that
  raised used to escape the consumer and close the socket, so the page reconnected and
  joined every component again. Now the component that raised is discarded (`leaving()`
  runs) and the client joins it again from the state its element carries, which is the
  state before the event: what the handler changed before raising is gone, the button it
  disabled comes back, and the other components and the socket carry on. The same holds for
  broadcast receivers, `params_changed`, hook events, upload callbacks, a LiveComponent's
  `update()` (its root joins again) and rendering. A join that fails (mount, an `on_mount`
  hook, `joined()`) is no longer answered with `remove`: the element keeps what the page
  rendered and gets `wireview-error`, without a retry. Both dispatch a bubbling
  `wireview:error` event. A message no client sends (an unknown command, a payload that
  does not fit) and an event naming no handler are logged and dropped instead of closing
  the socket. The new outbound `error` is protocol version 4; an older bundle gets the old
  behaviour. `docs/features/errors.md`.
- What the user types is no longer erased by a render that is not about it (#91). A morph
  copied the server's value into every input, so a field the server does not render (or has
  not heard from yet) was reset to empty by any render: another field's debounced event, a
  click elsewhere, a broadcast, the focused field included. A field the user edited now keeps
  its value, except when the render answers an action (any event but `input`) from that field
  or its form, which is how Enter still empties a todo input and a submit its form, or when
  the server renders a new value for a field that is not focused. `JS().set_value` still sets
  a field anywhere. It was also why the todo E2E now and then added an item with an empty
  label (#86): the test typed before the join's answer, whose morph emptied the field.
  `docs/features/html-diff.md`.
- The input-value rule answers the right event (#92, from an implementation review of #91).
  The first version marked a committing action's fields and let the first morph to touch
  them use the mark, so an earlier event's late answer (or a broadcast) could empty a field
  in the Enter's place, and an answer that changed only a child left the mark behind for an
  unrelated render. Events are now paired with their render by `ref` (protocol version 3:
  the server announces its version on the render answering a join, and the client sends a
  `ref` only to a server that did), and an answer resets only what was sent: keystrokes
  typed after the action stay. Only a submit, a change, leaving a field or Enter commits;
  an arrow key in a search box no longer undoes a query still waiting on its debounce. A
  `JS().push` inside a binding commits like a handler binding. An event whose answer changed
  only a child no longer leaves its button in the loading state. A second review of that
  change found the answer's permission and the morph were not tied together: two answers
  arriving within one frame could lose the later one's, a later render folded into the same
  morph could use it, the fields marked under `myself` were not the ones sent, and an edit
  made after sending was still overwritten once the field lost focus or was emptied. The
  permission now goes only to the morph of the render that carries it, which runs at once;
  the marked fields are exactly the ones sent; a field changed since it was sent is always
  kept; and a ref never answered is dropped when a later one is. Key modifiers no longer fire
  while an IME is composing, so the Enter that completes a Hangul syllable does not submit.
  A click on a form's submit button and `key_code.13` count as committing.
- Uploads through `{% upload_input %}` and `{% upload_button %}` work in a browser (found with
  #90). The `upload_op` that creates an upload on the client carried no component id, and the
  client applied it to "the component that already has this upload", which on the first message
  is none, so the config was dropped and no file ever went up. Nothing in the repository
  uploaded through a browser; the new CSP end-to-end test does.
- The upload tags escape their extra attributes, once (found with #90). `upload_input` and
  `upload_button` escaped them twice (`class="btn"` arrived as `class=&quot;btn&quot;`), and
  `upload_preview` not at all, so a file name the client chose, passed as `alt`, could close
  the attribute and add an event handler. Underscores become hyphens in all three
  (`data_id=` → `data-id`). `upload_preview` also always read an UploadEntry's ref as empty.
- A handler that changes nothing no longer leaves its button disabled (found with #90). Loading
  classes and `wire-disabled-with` clear when a render arrives, and no render was sent for an
  event with no diff; now the event is acknowledged with `{"diff": null}`.
- Generated type stubs are valid Python that binds every name it uses (#89). Annotations
  were copied as source text, so `t.Any` from `import typing as t` (or any
  `from __future__ import annotations` module) reached the `.pyi` with `t` unbound, and
  `**kwargs` lost its stars, which after a parameter with a default is a SyntaxError. A
  project running `ruff check` failed on files regenerated at every DEBUG reload. Stubs
  now render each annotation from the resolved object and import what they name:
  builtins as they are, `typing` and `collections.abc` names, and classes from other
  modules with a real `from ... import`, where they used to be a commented-out
  `# from . import X`. What cannot be written faithfully (a TypeVar, a class only the
  source module has) becomes `Any`. `*args`, `**kwargs`, `*,` and `/` are kept, and so
  are `@classmethod` and `@staticmethod`. An implementation review then found more inputs
  that still produced invalid stubs, now fixed: a `ParamSpec` in `Callable` stopped generation
  (and `Concatenate` got the wrong arity; both are now `Callable[..., R]`), `typing.IO`/
  `BinaryIO`/`TextIO` were written but not imported, an imported class could shadow a builtin
  or a component (a class named `int`, a nested class named like a component, a component
  named `Any`), a parameter called `cls` or `self` was dropped by name, `inf`/`nan` defaults
  and keyword field names were not valid Python, a docstring holding triple quotes broke the
  file and a class without one inherited the base class's, and an annotation that failed to
  evaluate made every other one run twice.
- A component's own `@classmethod` and `@staticmethod` work again (found while fixing
  #89). Wrapping public methods in `validate_call` stored them back as plain functions:
  a classmethod stayed bound to the class that defined it, so a subclass building
  itself through an overridden `new` got an instance of the parent, and a staticmethod
  called through `self` received the instance as its first argument.

## [0.4.0] - 2026-09-19

JavaScript hooks that load themselves (GAP-032), navigation and stream assertions for tests
(GAP-031), and three more silent failures turned into `manage.py check` warnings (W011-W013).
It also fixes the README's own quick start, which led to a connection that died on arrival (#87).

**Upgrading.** Two things change for a project that is already running wireview:

- **Django 4.2 is no longer supported.** The requirement is `django>=5.0`; 5.0 through 6.1 are
  tested.
- **Hook files under `static/<app_label>/hooks/*.js` now load on every page by themselves.** A
  project that already includes them with its own `<script>` tags loads them twice. Remove the
  tags, or set `WIREVIEW["COLLECT_HOOKS"] = False` to keep loading them your way.

### Added

- JavaScript hook files load themselves (GAP-032, #71). An app puts them in
  `static/<app_label>/hooks/*.js` and `{% wireview_header %}` loads every app's,
  deferred and after the bundle; the file still names its own hook. The list is a
  property of the project rather than of the page, because a boosted move
  replaces the body and a script in the destination's head never runs -- loading
  only a page's own hooks would work on first load and break after a navigation.
  `WIREVIEW["COLLECT_HOOKS"] = False` turns it off for a project that bundles the
  same files itself. `docs/features/hooks.md`.
- `wireview.W011` reports a template asking for a hook no collected file
  registers -- until now the quietest failure in the library, since the client
  warns to the console and the component renders exactly as it should.
- A `hooks` example, and with it the first test anywhere of the client half of
  JavaScript hooks (#71). Nothing in this repository used `wire-hook` -- the
  server side had unit tests, while `mountHooks`, the lifecycle callbacks and the
  `pushEvent` round trip had never run. `examples/hooks/`.
- Navigation and stream assertions in `wireview.testing` (GAP-031, #70). `assert_pushed_to()`,
  `assert_replaced_to()`, `assert_redirected_to()` and `assert_no_navigation()` compare a
  destination and its query params and, on a miss, report every URL change that did happen;
  `follow_redirect()` mounts the destination page's component with the redirect's params, the
  same user and session, and **the boundary read from the URLconf** -- refusing when that
  boundary refuses the user, as the server would; `follow_push()` runs the `params_changed` a
  client sends after a push, and refuses a push that leaves the live_session because that one
  is a full page load. `stream_html()`, `stream_items()` and `stream_ops()` replace the
  `sent_messages` filtering the docs had been teaching. `docs/features/testing.md`.
- `ComponentTestCase.mount()` forwards `live_session=`, which `mount()` has taken since 0.3.0.
- Django 5.2 LTS and 6.1 join the CI matrix and the classifiers. The matrix had skipped from 5.1
  to 6.0, so the current LTS and the current release were the two versions nothing tested; the
  suite passes on both unchanged.
- `wireview.W013` warns when `runserver` starts as Django's WSGI server, which cannot accept the
  WebSocket: `daphne` missing, or listed below the app that provides the command. The page
  renders, nothing raises, and no component comes alive. It only looks while `runserver` is the
  command being run, so a project served by uvicorn never sees it.
- A `LICENSE` file. The package metadata has said MIT all along, but the repository had no text
  to point at and the README linked to a file that did not exist. It carries the original
  django-reactor notice alongside wireview's.

### Removed

- Django 4.2. Supported versions are now 5.0 through 6.1, and `django>=5.0` is the requirement.

### Fixed

- `docs/DEPLOYMENT.md` no longer asks for sticky sessions, which it did in two places while saying
  the opposite a few lines further down. A WebSocket stays on one instance by itself, and a join
  restores from the signed state on any instance. It also stopped asking for a WebSocket health
  check on an AWS ALB target group, which only speaks HTTP, and its WebSocket probe now opens
  `/__wireview__` and closes it rather than sending a `ping` command the protocol does not have
  to a path wireview does not serve.
- The opportunistic upload sweep ran late on a freshly booted machine. It marked "never swept" as
  0.0 on a clock that counts from boot, so on a host up for less than the ten-minute interval --
  a new container host, an autoscaled instance -- the first sweep waited until the machine had
  been up that long. CI's test job had failed on this for a week.

- Components no longer join before the page's own deferred scripts have run.
  The bundle opens the socket as soon as it executes, which is inside the
  deferred phase, so a page registering a JavaScript hook from its own `defer`
  script was racing the handshake -- and losing it is silent, since an
  unregistered hook is a console warning and nothing else. `ready.mjs`.
- A navigation to a query-only destination (`push_to("?page=2")` -- what `params_changed`'s own
  docstring shows) raised `NoReverseMatch`. `resolve_url` reverses any string with no `/` and no
  `.` in it; `resolve_destination` now passes `?` and `#` through unchanged.
- A `.json` query key decoded to a value on the first load and arrived as a raw string after a
  navigation, because the client hands back what it read from the address bar. The consumer
  decodes with the same rule the first load used.
- `make bench` runs again. Its WebSocket half had died at the first join since 0.3.0: the bench
  signed its join state the pre-v1 way and relied on `STATE_ACCEPT_LEGACY`, which stops applying
  once a project declares a `live_session` -- and testproj, the project the bench runs on,
  declares one. It now signs with the tree's own `sign_state`, and `tests/test_bench_harness.py`
  joins the way the bench does, since CI never ran the bench and nothing else noticed.
- The client bundle no longer prints `BOOST_PAGES false` to the console on every page load,
  nor `LOAD <url>` on every boosted move. Two debug lines inherited from reactor sat outside
  `wireview.debug`, in a module every page runs whether or not `BOOST_PAGES` is on.
- A project with no `CHANNEL_LAYERS` had every WebSocket accepted and then killed by
  `AttributeError: 'WireviewConsumer' object has no attribute 'channel_name'` (#87). Channels has no
  default layer -- it resolves a missing `default` alias to `None` and then never sets that
  attribute -- and the README's setup never asked for one, so its own quick start led here. The
  consumer now refuses before accepting, with an `ImproperlyConfigured` that names the setting and
  the three-line fix, and `wireview.W012` reports the same sentence from `manage.py check`.

## [0.3.0] - 2026-09-10

Page-level authentication boundaries (`live_session`, GAP-009), and the reworking of the mount
path that making them real required.

**Upgrading.** Four things change for a project that is already running wireview:

- **Every open page reloads once.** The signed `data-state` envelope goes from v1 to v2, and v1
  is refused. That is the designed recovery -- the page re-renders under the current auth
  context and gets a fresh token -- but unsaved input in an open tab goes with it.
  `WIREVIEW["STATE_ACCEPT_LEGACY"]` widens the window, unless the project declares a
  `live_session` (see below).
- **A halted `_on_mount` hook now stops the render.** It used to skip `joined()` and draw the
  component anyway, which shipped the markup and the state a guard was refusing. If a hook of
  yours halts for a reason that was never meant to hide anything, it now hides it. The same
  applies to `wireview.testing.mount()`, which had been the one path that disagreed.
- **A hook that raises is a refusal**, not an unfinished mount: nothing renders and nothing stays
  in the repository.
- **`login()` writes one key into the session** (`_wireview_auth_gen`), whether or not the
  project uses boundaries.

`manage.py check` reports the new traps as `wireview.W010`. `docs/features/live-session.md` has
the feature; `docs/DEPLOYMENT.md` has the upgrade and the transition procedure for putting a
boundary around a page that was public.

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

- `tests/test_agent_docs.py` guards the agent harness against drift: every path, `make` target
  and GAP number cited by `CLAUDE.md` or a skill must exist, and each skill's frontmatter must
  name its own directory. It runs with `make test`, so a rename that turns the map into a lie
  fails in CI

- Telemetry signals (GAP-022): `event_handled`, `component_rendered`, `diff_computed` and
  `broadcast_published` report duration and payload size from the event, render, diff and
  fan-out paths. Opt in with `WIREVIEW["TELEMETRY"]`; while off the instrumented paths take a
  shared no-op span and `make bench-compare` shows no change. See `docs/features/telemetry.md`

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

- `docs/FEATURE-GAP.md` now matches the code. Three rows claimed features were missing that
  had shipped (form auto-recovery, telemetry, `stream(reset:)`), every remaining gap carries a
  GAP number and an issue, Nested LiveViews is marked as a deliberate exclusion with the reason,
  and the coverage summary is a count of the table rather than a round number

- Answered why uvicorn costs 4× more RSS per connection than daphne (`#61`): it negotiates
  WebSocket permessage-deflate by default and daphne does not offer it, so every uvicorn
  connection holds a zlib deflate and inflate context. Measured at 2,000 connections: daphne
  45.9 KB, uvicorn 211.5 KB, uvicorn with `--ws-per-message-deflate false` 50.6 KB — a 160.9 KB
  gap against 158.8 KB for a compressobj/decompressobj pair in the same interpreter. wireview's
  diffs are small and compress badly (a typical event payload shrinks 16%), so `docs/DEPLOYMENT.md`
  now recommends turning it off unless the app pushes large HTML, and the benchmark grew a
  `--server uvicorn-nodeflate` lane to measure both ways

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

- Agent harness: the working procedure moved out of `CLAUDE.md` into a `wireview-dev` skill
  (`.claude/skills/wireview-dev/SKILL.md`). `CLAUDE.md` now carries only what an agent needs in
  every session — the map and the prohibitions — and points at the skill for session start-up,
  issue and `wip` conventions, the definition of done, commits, the full command table and the
  benchmarking pitfalls

### Fixed

- The sdist carries `skills/wireview` again, so the release build works. Hatchling's sdist walk
  skips a nested directory whose name matches the package -- `skills/wireview` was invisible to
  it while `skills/anything-else` was not -- and `uv build` builds the wheel *from the sdist*,
  so the wheel's force-include then failed on a directory sitting in the working tree. It failed
  the same way at v0.2.1. `make ci-build` now checks the wheel for the agent skill as well as
  for `wireview.min.js`.

- The E2E harness cleans up on every exit, not just the successful one (`#58`). A startup that
  timed out handed back a live thread and then restored settings out from under it -- the
  original defect, in the branch nobody was looking at -- and the event loop was never closed.
- The E2E suites share one live-server harness (`tests/testproj/e2e_server.py`), and it waits.
  Four copies of the same thread each entered a global `override_settings(DEBUG=True)` on their
  own schedule and each teardown asked the server to stop without waiting for it, so a thread
  winding down restored settings underneath the next test and a server that had only been asked
  to stop kept answering into it. Both failures land as a warning *after* a green summary, which
  is how they survived in four places. The port is now bound before the thread starts rather
  than picked at random and hoped for. `tests/test_e2e_harness.py` holds the properties -- no
  thread left behind, no override outliving the block, no second copy of the pattern -- because
  the race itself is not reproducible on demand and a suite run is not a test of it.
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

- Method exposure now means "written in user code". `ComponentRepository._is_user_defined_method`
  stopped at `Component` in the MRO, so everything between a subclass and `Component` counted as a
  user handler: `LiveComponent.send_to_parent` and `update` were client-callable, and the
  `model_post_init` Pydantic injects into every component was only blocked by accident, by its
  positional-only signature. A name owned by any `wireview` or `pydantic` class is now blocked even
  when a subclass overrides it, so lifecycle callbacks stay parent-driven and a client can no longer
  forge a parent event that looks like it came from a child (#63)

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
- A nested `{% component %}` crosses the sync/async bridge once per instance, not once per
  render (`#58`). The guard that answers "already mounted" sat behind the bridge rather than in
  front of it, so a parent inside a boundary paid ~194us per nested component on every
  re-render. Measured: 88us with no boundary, 308us on the instance's first render inside one,
  56us on every render after it.
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

- The session's user id is compared through the model's own primary-key field (`#58`), the way
  `django.contrib.auth` reads it back -- and through `get_user_model()`, because the user on a
  connection is a `SimpleLazyObject` whose type has no `_meta`.
- Entering a boundary asks the re-read session who it authenticates, not only what it
  fingerprints to (`#58`). Dropping the auth hash left a session written before the generation
  nonce with the pk as its only fingerprint input -- and the pk comes from the connection, not
  from the session, so a flushed session fingerprinted exactly like a live one and a delayed
  first join after a logout was admitted. A regression the same release introduced, found by
  re-judging it.
- The authentication generation no longer includes `_auth_user_hash` (`#58`).
  `update_session_auth_hash()` moves that hash while deliberately keeping the user logged in,
  and fires no signal -- so a password change moved the topic out from under a socket that was
  already listening, and every later logout published somewhere else. On a healthy broker,
  every time. A binding that breaks the retirement path is worth less than the retirement path.
- Logging in retires the previous generation even when there was no nonce to name it (`#58`).
  Sessions from before the upgrade subscribed under the pk alone, and skipping the publish
  because the key was missing left exactly the connections the upgrade could not otherwise
  reach.
- An explicit `AnonymousUser` no longer takes the pk out of the session (`#58`). That fallback
  is for a caller with no user object -- which is what `logout()` hands its signal when the
  request has no `request.user` -- and applying it to an anonymous connection fingerprinted the
  socket as whoever the session's leftover keys named. Channels leaves those keys in place when
  a backend declines to return the user, an inactive account for instance.
- `wireview.W010` reports a `live_session` declared without
  `django.template.context_processors.request` (`#58`). The template tags read the page's
  boundary off the request, so without that processor the whole feature turns itself off in
  silence: the header publishes an empty name, every state is signed with no boundary, and a
  component that declared `_live_sessions` vanishes from the page it belongs on. The view
  decorator still refuses unauthorized requests, so it is not an open door -- it is the rest of
  the boundary quietly missing, on a page that renders 200.
- A logout names the right generation even when the request has no `request.user` (`#58`).
  `logout()` sends `user=None` in that case, and the fingerprint quietly dropped the pk, so the
  message went to a topic none of the connections it meant to retire were on. The session
  records who is logged in, so the pk comes from there when the caller has no user object.
- Logging in again retires the generation it replaces (`#58`). A step-up or re-auth overwrites
  the session's generation nonce, so no later logout could name the sockets still holding the
  old one. (A *different* user logging in flushes the session before any signal fires, so that
  generation cannot be named at all; logging out first is what retires it.)

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

[Unreleased]: https://github.com/itda-work/django-wireview/compare/v1.0.0rc3...HEAD
[1.0.0rc3]: https://github.com/itda-work/django-wireview/compare/v1.0.0rc2...v1.0.0rc3
[1.0.0rc2]: https://github.com/itda-work/django-wireview/compare/v1.0.0rc1...v1.0.0rc2
[1.0.0rc1]: https://github.com/itda-work/django-wireview/compare/v0.7.0...v1.0.0rc1
[0.7.0]: https://github.com/itda-work/django-wireview/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/itda-work/django-wireview/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/itda-work/django-wireview/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/itda-work/django-wireview/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/itda-work/django-wireview/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/itda-work/django-wireview/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/itda-work/django-wireview/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/itda-work/django-wireview/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/itda-work/django-wireview/releases/tag/v0.1.0
