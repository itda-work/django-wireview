"""Where one broadcast's time goes, stage by stage, on every connection it reaches (#176).

    uv run --with fastapi==0.142.2 python -m bench.fanout_profile             # both stacks, 5 rounds
    uv run python -m bench.fanout_profile --only wireview --rounds 1          # a quick look
    uv run python -m bench.fanout_profile --only wireview --cprofile          # which functions (times inflated)
    uv run python -m bench.fanout_profile inproc                              # one render by part, trips, threads
    uv run python -m bench.fanout_profile clicks                              # one click's server time, in-process
    uv run --python 3.14t --no-project --with django==6.0 python -m bench.fanout_profile plain   # no GIL

The scenario is ``make bench-fastapi``'s fan-out (bench/compare_fastapi): N WebSocket
clients on one uvicorn process (``--ws websockets``, permessage-deflate on), every one
joined to the same Board; one of them clicks ``announce`` and every client waits for its
update. The server here is that same application, started fresh each round, with probes
put in at runtime: the library's code is not changed, its functions are wrapped from the
outside before uvicorn starts (``install_probes``). The probes only record while armed,
which the driver does for the measured broadcasts alone.

What the probes measure, in the server process:

- the span of one broadcast: from the ``announce`` message leaving ASGI ``receive`` to the
  last WebSocket frame written, and from there to the last client's receive (the client
  side runs in the driver process; ``perf_counter_ns`` is the same clock in both on macOS)
- how long the event loop sat in ``select()`` inside that span, so the rest is the time it
  was busy, and how long the one ``sync_to_async`` worker thread was busy
- per stage: the work each connection does, as the CPU time of the thread that ran it
  (``thread_time``) beside its wall time. ``WireviewMeta.render_diff``'s worker-thread
  trip, ``close_old_connections`` around every message, the diff, the JSON, the frame
  write with its deflate, and so on. The loop and the worker take turns on the GIL, so a
  stage's wall time also holds the other thread's work; the tables use CPU time

Measured broadcasts alternate between armed and unarmed: the unarmed ones give the
client's span alone, as ``make bench-fastapi`` sees it, so what the probes cost shows.
Nothing here runs under tracemalloc. Every number is a median: of the measured broadcasts
in a round, then of the rounds. The FastAPI side serves a built client: run
``make bench-fastapi`` once, or ``npm --prefix bench/compare_fastapi/client ci && npm
--prefix bench/compare_fastapi/client run build``. Results go to ``bench/.data/``;
``--report <file>`` prints the tables of one again, ``--chart <file>`` the Mermaid charts
docs/design/broadcast-fanout.md shows.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import typing as t
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS_PATH = "/__fanout__/stats"
ARM_PATH = "/__fanout__/arm"

IMPLEMENTATIONS = ("wireview", "wireview-shared", "fastapi")
#: ``wireview-shared`` is the same server with ``BENCH_SHARED_RENDER=1``: Board declares
#: ``Meta.shared_render`` (#176, stage 2; bench/compare_fastapi/serve.py)

ns = time.perf_counter_ns
cpu = time.thread_time_ns  # what this thread ran, without the time it waited for the GIL


# -- server side: probes --------------------------------------------------------------------


class Probe:
    """What the wrapped functions record while armed. One writer per dict: the loop or the worker."""

    def __init__(self) -> None:
        self.armed = False
        self.main_thread = threading.get_ident()
        self.profile: str | None = None  # "cprofile": profile the armed span as well (times inflate)
        self.reset()

    def reset(self) -> None:
        # side -> stage -> nanoseconds: wall time, and CPU time of the thread that ran it
        self.wall: dict[str, dict[str, int]] = {"loop": defaultdict(int), "worker": defaultdict(int)}
        self.cpu: dict[str, dict[str, int]] = {"loop": defaultdict(int), "worker": defaultdict(int)}
        self.calls: dict[str, int] = defaultdict(int)
        self.loop_cpu_marks: list[int] = []
        self.marks: dict[str, int] = {}
        self.sends: list[int] = []
        self.dispatched: list[int] = []
        self.selects: list[tuple[int, int]] = []
        self.thread_spans: list[tuple[int, int]] = []
        self.thread_idents: set[int] = set()
        self.profiler = None

    def add(self, stage: str, wall: int, cpu: int) -> None:
        side = "loop" if threading.get_ident() == self.main_thread else "worker"
        self.wall[side][stage] += wall
        self.cpu[side][stage] += cpu
        self.calls[f"{side}.{stage}"] += 1

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, ns())


probe = Probe()
_local = threading.local()


def _timed(stage: str, fn: t.Callable) -> t.Callable:
    """``fn`` (sync), its time added to ``stage`` while armed."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not probe.armed:
            return fn(*args, **kwargs)
        started, used = ns(), cpu()
        try:
            return fn(*args, **kwargs)
        finally:
            probe.add(stage, ns() - started, cpu() - used)

    return wrapper


def _atimed(stage: str, fn: t.Callable) -> t.Callable:
    """``fn`` (async), its wall time from call to return added to ``stage`` while armed.

    Only for coroutines that do not give the loop away in the middle, or whose waits the
    caller subtracts: the wall time of one that does includes other tasks' work.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if not probe.armed:
            return await fn(*args, **kwargs)
        started, used = ns(), cpu()
        try:
            return await fn(*args, **kwargs)
        finally:
            probe.add(stage, ns() - started, cpu() - used)

    return wrapper


def _patch_select() -> None:
    """Time the event loop spends blocked in ``select()``: what is left of a span is busy time."""
    import selectors

    cls = selectors.DefaultSelector
    original = cls.select

    def select(self, timeout=None):
        if not probe.armed or threading.get_ident() != probe.main_thread:
            return original(self, timeout)
        started = ns()
        try:
            return original(self, timeout)
        finally:
            probe.selects.append((started, ns()))

    cls.select = select


def _patch_worker_thread() -> None:
    """The ``sync_to_async`` worker: its busy spans, outermost call only."""
    from asgiref.sync import SyncToAsync
    from channels.db import DatabaseSyncToAsync

    def wrap(original):
        @functools.wraps(original)
        def thread_handler(self, loop, *args, **kwargs):
            if not probe.armed:
                return original(self, loop, *args, **kwargs)
            depth = getattr(_local, "depth", 0)
            _local.depth = depth + 1
            started, used = ns(), cpu()
            try:
                return original(self, loop, *args, **kwargs)
            finally:
                ended = ns()
                _local.depth = depth
                if depth == 0:
                    probe.thread_spans.append((started, ended))
                    probe.thread_idents.add(threading.get_ident())
                    probe.add("thread_handler", ended - started, cpu() - used)

        return thread_handler

    # DatabaseSyncToAsync's handler closes connections, then calls SyncToAsync's
    DatabaseSyncToAsync.thread_handler = wrap(DatabaseSyncToAsync.thread_handler)
    SyncToAsync.thread_handler = wrap(SyncToAsync.thread_handler)


def _patch_uvicorn() -> None:
    """Every WebSocket frame written: the wall time of uvicorn's send, and the deflate inside it."""
    from uvicorn.protocols.websockets.websockets_impl import WebSocketProtocol
    from websockets.extensions.permessage_deflate import PerMessageDeflate

    original = WebSocketProtocol.asgi_send

    @functools.wraps(original)
    async def asgi_send(self, message):
        if not probe.armed or message["type"] != "websocket.send":
            return await original(self, message)
        started, used = ns(), cpu()
        try:
            return await original(self, message)
        finally:
            ended, now = ns(), cpu()
            probe.add("ws_send", ended - started, now - used)
            probe.sends.append(ended)
            probe.loop_cpu_marks.append(now)

    WebSocketProtocol.asgi_send = asgi_send
    PerMessageDeflate.encode = _timed("deflate", PerMessageDeflate.encode)


def _patch_wireview() -> None:
    import channels.consumer
    import channels.db
    import channels.layers
    from channels.generic.websocket import AsyncJsonWebsocketConsumer

    import wireview.core.meta as meta
    import wireview.core.state as state
    import wireview.templatetags.wireview as tags
    from wireview.consumer import WireviewConsumer
    from wireview.core.rendered import Rendered, RenderedDiff
    from wireview.core.transport import ChannelsBroker
    from wireview.session import WireviewSession

    # The fan-out: group_send, and each member's copy of the message into its queue
    ChannelsBroker.publish = _atimed("publish (group_send, wall)", ChannelsBroker.publish)
    original_publish = ChannelsBroker.publish

    async def publish(self, topic, message):
        probe.mark("publish")
        return await original_publish(self, topic, message)

    ChannelsBroker.publish = publish
    layer = channels.layers.InMemoryChannelLayer
    layer.send = _atimed("layer.send (deepcopy + put)", layer.send)
    # Every receive (and group_send) walks every queued channel and every group member first
    layer._clean_expired = _timed("layer._clean_expired", layer._clean_expired)

    # Channels' dispatch of each message: close_old_connections on a worker trip first
    original_dispatch = WireviewConsumer.dispatch

    async def dispatch(self, message):
        if not probe.armed or message.get("type") != "notification":
            return await original_dispatch(self, message)
        started, used = ns(), cpu()
        probe.dispatched.append(started)
        try:
            return await original_dispatch(self, message)
        finally:
            probe.add("dispatch (wall)", ns() - started, cpu() - used)

    WireviewConsumer.dispatch = dispatch
    channels.consumer.aclose_old_connections = _atimed(
        "await close_old_connections trip (wall)", channels.consumer.aclose_old_connections
    )
    channels.db.close_old_connections = _timed("close_old_connections", channels.db.close_old_connections)

    # The session: the receiver, the render, the payload, the subscriptions after
    WireviewSession.notification = _atimed("notification handler (wall)", WireviewSession.notification)
    WireviewSession.send_render = _atimed("send_render (wall)", WireviewSession.send_render)
    WireviewSession.after_mutation_chores = _atimed("after_mutation_chores", WireviewSession.after_mutation_chores)
    meta.WireviewMeta.render_diff = _atimed("render_diff (wall)", meta.WireviewMeta.render_diff)

    def timed_trips(label: str, original_db):
        def db(fn):
            trip = original_db(fn)

            async def call(*args, **kwargs):
                if not probe.armed:
                    return await trip(*args, **kwargs)
                started, used = ns(), cpu()
                try:
                    return await trip(*args, **kwargs)
                finally:
                    probe.add(label, ns() - started, cpu() - used)

            return call

        return db

    meta.db = timed_trips("await render trip (wall)", meta.db)

    # On the worker thread: the context (every public attribute), the template, the signature
    meta.WireviewMeta._collect_context = _timed("collect_context", meta.WireviewMeta._collect_context)
    meta.WireviewMeta._render_with_context = _timed("template render", meta.WireviewMeta._render_with_context)
    tags.sign_state = _timed("sign_state", tags.sign_state)
    state.signable_json = _timed("state json (model_dump_json)", state.signable_json)

    # A Meta.shared_render class (#176, stage 2): the key and the wait, the parse once, each
    # connection's token put in, and its diff against its own page
    import wireview.core.shared_render as shared

    shared.key = _timed("shared: key", shared.key)
    # The connections that take the render sign their tokens off the loop, in trips they share:
    # the sign_state that module looks up at the call, apart from the template's in the render
    shared.db = timed_trips("await sign trip (wall)", shared.db)
    state.sign_state = _timed("sign_state (taker's trip)", state.sign_state)
    # shared_render.render waits: for the render trip, or for the render another connection
    # leads. The loop runs the others meanwhile, so both that wait and send_render around it
    # hold their CPU. Two medians of such sums, subtracted, are noise; the CPU of send_render
    # outside shared_render.render is kept per task and recorded as a stage of its own.
    inside: dict[t.Any, int] = defaultdict(int)
    render = shared.render

    async def timed_render(*args, **kwargs):
        if not probe.armed:
            return await render(*args, **kwargs)
        used = cpu()
        try:
            return await render(*args, **kwargs)
        finally:
            inside[asyncio.current_task()] += cpu() - used

    shared.render = timed_render
    send_render = WireviewSession.send_render

    async def timed_send_render(self, *args, **kwargs):
        if not probe.armed:
            return await send_render(self, *args, **kwargs)
        task, used = asyncio.current_task(), cpu()
        try:
            return await send_render(self, *args, **kwargs)
        finally:
            spent = cpu() - used - inside.pop(task, 0)
            probe.add("send_render outside the shared render", spent, spent)

    WireviewSession.send_render = timed_send_render
    shared.Shared.parse = classmethod(_timed("shared: parse", shared.Shared.parse.__func__))
    shared.Shared.with_state = _timed("shared: with_state", shared.Shared.with_state)
    meta.WireviewMeta._diff_against_last = _timed("diff: against last", meta.WireviewMeta._diff_against_last)

    # Back on the loop: the diff, and the frame's JSON
    meta.WireviewMeta._compute_rendered_diff = _timed("diff", meta.WireviewMeta._compute_rendered_diff)
    Rendered.from_marked_html = classmethod(_timed("diff: parse markers", Rendered.from_marked_html.__func__))
    Rendered.settle = _timed("diff: settle", Rendered.settle)
    Rendered.get_diff = _timed("diff: compare", Rendered.get_diff)
    RenderedDiff.to_payload = _timed("diff: to_payload", RenderedDiff.to_payload)
    AsyncJsonWebsocketConsumer.encode_json = classmethod(
        _atimed("encode_json", AsyncJsonWebsocketConsumer.encode_json.__func__)
    )


def _patch_fastapi() -> None:
    import types

    from starlette.websockets import WebSocket

    import bench.compare_fastapi.fastapi_app.main as main

    main.json = types.SimpleNamespace(**{**vars(json), "dumps": _timed("json.dumps", json.dumps)})
    WebSocket.send_text = _atimed("starlette send_text (wall)", WebSocket.send_text)


def install_probes(name: str) -> None:
    _patch_select()
    _patch_worker_thread()
    _patch_uvicorn()
    if name.startswith("wireview"):
        _patch_wireview()
    else:
        _patch_fastapi()


class Probed:
    """The served application, with the probe's arm and stats endpoints and the span's start."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in (ARM_PATH, STATS_PATH):
            body = json.dumps(self._arm() if scope["path"] == ARM_PATH else self._stats()).encode()
            headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        async def marked_receive():
            message = await receive()
            if probe.armed and message["type"] == "websocket.receive" and "announce" in (message.get("text") or ""):
                if "receive" not in probe.marks:
                    probe.mark("receive")
                    probe.marks["receive_cpu"] = cpu()
            return message

        await self.app(scope, marked_receive, send)

    def _arm(self) -> dict:
        probe.reset()
        probe.armed = True
        if probe.profile:
            import cProfile

            # Since 3.12 cProfile rides on sys.monitoring: one profiler, every thread
            probe.profiler = cProfile.Profile()
            probe.profiler.enable()
        return {"armed": True}

    def _stats(self) -> dict:
        probe.armed = False
        if probe.profiler is not None:
            probe.profiler.disable()
        start = probe.marks.get("receive")
        if start is None or not probe.sends:
            return {"error": "no broadcast seen", "marks": probe.marks, "sends": len(probe.sends)}
        end = max(probe.sends)
        span = end - start
        idle = _overlap(probe.selects, start, end)
        worker = _overlap(probe.thread_spans, start, end)
        both = _both_busy(probe.selects, probe.thread_spans, start, end)
        result = {
            "server_span_ns": span,
            "first_send_ns": min(probe.sends) - start,
            "publish_ns": probe.marks.get("publish", start) - start,
            "frames": len(probe.sends),
            "notifications": len(probe.dispatched),
            "first_dispatch_ns": (min(probe.dispatched) - start) if probe.dispatched else None,
            "last_dispatch_ns": (max(probe.dispatched) - start) if probe.dispatched else None,
            "loop_idle_ns": idle,
            "loop_busy_ns": span - idle,
            "loop_cpu_ns": probe.loop_cpu_marks[probe.sends.index(end)] - probe.marks["receive_cpu"],
            "worker_busy_ns": worker,
            "worker_cpu_ns": probe.cpu["worker"].get("thread_handler", 0),
            "both_busy_ns": both,
            "worker_threads": len(probe.thread_idents),
            "wall": {side: dict(stages) for side, stages in probe.wall.items()},
            "cpu": {side: dict(stages) for side, stages in probe.cpu.items()},
            "calls": dict(probe.calls),
            "last_send_clock_ns": end,
            "receive_clock_ns": start,
        }
        if probe.profile:
            result["profile"] = _profile_text(probe.profiler)
        return result


def _overlap(spans: list[tuple[int, int]], start: int, end: int) -> int:
    """How much of [start, end] the spans cover (they do not overlap one another)."""
    return sum(max(0, min(b, end) - max(a, start)) for a, b in spans)


def _both_busy(selects: list[tuple[int, int]], threads: list[tuple[int, int]], start: int, end: int) -> int:
    """How long the loop was out of select() while the worker was in a call: the two wanting the GIL."""
    total = 0
    for a, b in threads:
        a, b = max(a, start), min(b, end)
        if b > a:
            total += (b - a) - _overlap(selects, a, b)
    return total


def _profile_text(profiler, limit: int = 40) -> dict[str, str]:
    if profiler is None:
        return {}
    import io
    import pstats

    out = {}
    for key in ("tottime", "cumulative"):
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(key).print_stats(limit)
        out[key] = stream.getvalue()
    return out


def serve(name: str, port: int, profile: bool) -> None:
    """The server process: the application ``make bench-fastapi`` serves, with the probes in."""
    import uvicorn

    os.environ.setdefault("BENCH_CLIENT", "vanilla")
    probe.profile = "cprofile" if profile else None
    import bench.compare_fastapi.serve as served

    app = getattr(served, "wireview" if name.startswith("wireview") else name)  # Django is set up here,
    # so the probes find what they wrap
    install_probes(name)
    uvicorn.run(Probed(app), host="127.0.0.1", port=port, log_level="warning", ws="websockets")


# -- driver ---------------------------------------------------------------------------------


class Server:
    def __init__(self, name: str, profile: bool) -> None:
        from bench.ws import LOG_DIR, _free_port, _wait_for_port

        self.name = name
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
        env.pop("BENCH_SHARED_RENDER", None)
        if name == "wireview-shared":
            env["BENCH_SHARED_RENDER"] = "1"
        cmd = [sys.executable, "-m", "bench.fanout_profile", "serve", name, "--port", str(self.port)]
        if profile:
            cmd.append("--cprofile")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_DIR / f"fanout-profile-{name}-{self.port}.log", "wb")  # noqa: SIM115
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        _wait_for_port(self.port, timeout=60, proc=self.proc)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path) as response:
            return json.loads(response.read())

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *exc: object) -> None:
        from bench.ws import _stop

        _stop(self.proc)


async def _round(server: Server, connections: int, warmup: int, broadcasts: int) -> list[dict]:
    """Open the connections as ``make bench-fastapi`` does, then broadcast: ``warmup``, then ``broadcasts`` armed
    ones, each followed by an unarmed one."""
    import re

    import websockets

    wireview = server.name.startswith("wireview")
    state = ""
    if wireview:
        with urllib.request.urlopen(server.url + "/") as response:
            state = re.search(r'data-state="([^"]+)"', response.read().decode()).group(1)

    async def open_one():
        if wireview:
            ws = await websockets.connect(
                f"ws://127.0.0.1:{server.port}/__wireview__?vsn=99", origin=server.url, max_size=None, open_timeout=60
            )
            await ws.send(json.dumps({"command": "join", "payload": {"name": "Board", "state": state, "children": {}}}))
            while json.loads(await asyncio.wait_for(ws.recv(), 60))["command"] != "joined":
                pass
        else:
            ws = await websockets.connect(f"ws://127.0.0.1:{server.port}/ws", origin=server.url, max_size=None)
            assert json.loads(await asyncio.wait_for(ws.recv(), 60))["type"] == "snapshot"
        return ws

    async def update(ws) -> int:
        while True:
            text = await asyncio.wait_for(ws.recv(), 120)
            message = json.loads(text)
            if message.get("command") == "render" or message.get("type") == "announcement":
                return ns()

    conns = []
    for start in range(0, connections, 50):
        conns += await asyncio.gather(*(open_one() for _ in range(start, min(start + 50, connections))))
    await asyncio.sleep(0.5)
    if wireview:
        event = {"id": "board", "command": "announce", "implicit_args": {}, "explicit_args": {}}
        message = json.dumps({"command": "user_event", "payload": event})
    else:
        message = json.dumps({"type": "announce"})
    results = []
    unarmed = []
    try:
        # Measured broadcasts alternate: armed (the probes record) and unarmed (the client's
        # span alone, what make bench-fastapi sees), so the probes' own cost shows
        for i in range(warmup + 2 * broadcasts):
            measured = i >= warmup and (i - warmup) % 2 == 0
            if measured:
                server.get(ARM_PATH)
            sent = ns()
            await conns[0].send(message)
            received = await asyncio.gather(*(update(ws) for ws in conns))
            await asyncio.sleep(0.2)  # what runs after the last frame (subscriptions, query string) is done
            if not measured:
                if i >= warmup:
                    unarmed.append(max(received) - sent)
                continue
            stats = server.get(STATS_PATH)
            if "error" in stats:
                raise RuntimeError(f"{server.name}: {stats}")
            stats["client_span_ns"] = max(received) - sent
            stats["client_to_server_ns"] = stats["receive_clock_ns"] - sent
            stats["last_receive_after_last_send_ns"] = max(received) - stats["last_send_clock_ns"]
            results.append(stats)
    finally:
        await asyncio.gather(*(ws.close() for ws in conns))
    for stats in results:
        stats["client_span_unarmed_ns"] = statistics.median(unarmed)
    return results


SPAN_KEYS = (
    "client_span_ns",
    "client_span_unarmed_ns",
    "client_to_server_ns",
    "server_span_ns",
    "publish_ns",
    "first_dispatch_ns",
    "last_dispatch_ns",
    "first_send_ns",
    "last_receive_after_last_send_ns",
    "loop_busy_ns",
    "loop_idle_ns",
    "loop_cpu_ns",
    "worker_busy_ns",
    "worker_cpu_ns",
    "both_busy_ns",
    "frames",
    "worker_threads",
)


def _nested(entries: list[dict], key: str) -> dict[str, dict[str, float]]:
    sides = {side for e in entries for side in e[key]}
    out = {}
    for side in sorted(sides):
        stages = {s for e in entries for s in e[key].get(side, {})}
        out[side] = {s: statistics.median(e[key].get(side, {}).get(s, 0) for e in entries) for s in sorted(stages)}
    return out


def _median(entries: list[dict]) -> dict[str, t.Any]:
    """The median of each number: of a round's measured broadcasts, or of the rounds."""
    out: dict[str, t.Any] = {}
    for key in SPAN_KEYS:
        values = [e[key] for e in entries if e.get(key) is not None]
        out[key] = statistics.median(values) if values else None
    out["wall"] = _nested(entries, "wall")
    out["cpu"] = _nested(entries, "cpu")
    calls = {k for e in entries for k in e["calls"]}
    out["calls"] = {k: statistics.median(e["calls"].get(k, 0) for e in entries) for k in sorted(calls)}
    return out


def _across(rounds: list[dict]) -> dict[str, t.Any]:
    out = _median(rounds)
    for key in ("server_span_ns", "client_span_ns", "client_span_unarmed_ns"):
        out[key.replace("_ns", "_range_ns")] = [min(r[key] for r in rounds), max(r[key] for r in rounds)]
    return out


def stages(summary: dict[str, t.Any], name: str) -> list[tuple[str, float, float]]:
    """The span's CPU split into exclusive stages: (stage, CPU ms, wall ms). What no probe covers is last.

    CPU is the time the thread ran the stage. Wall adds the time it waited for the GIL
    the other thread held, which is not the stage's cost.
    """
    ms = 1e-6
    rows: list[tuple[str, float, float]] = []

    def get(kind: str, side: str, stage: str) -> float:
        return summary[kind].get(side, {}).get(stage, 0) * ms

    def row(label: str, side: str, stage: str, minus: tuple[str, ...] = ()) -> tuple[str, float, float]:
        return (
            label,
            get("cpu", side, stage) - sum(get("cpu", side, m) for m in minus),
            get("wall", side, stage) - sum(get("wall", side, m) for m in minus),
        )

    if name == "wireview-shared":
        # The board renders once per message (the worker rows); each connection takes the key,
        # its token and its diff on the loop. The parse happens once, in the render that leads.
        rows = [
            row("publish: group_send의 member마다 deepcopy + put", "loop", "layer.send (deepcopy + put)"),
            row("loop: InMemory 레이어 _clean_expired (receive마다 전체 순회)", "loop", "layer._clean_expired"),
            row("worker: close_old_connections (메시지마다 3번)", "worker", "close_old_connections"),
            row("worker: 컨텍스트 읽기 (_collect_context)", "worker", "collect_context"),
            row("worker: 템플릿 렌더 (서명 제외)", "worker", "template render", ("sign_state",)),
            row("worker: 렌더한 연결의 data-state 서명 (렌더 트립 안)", "worker", "sign_state"),
            row("worker: 받은 연결들의 data-state 서명 (함께 쓰는 서명 트립)", "worker", "sign_state (taker's trip)"),
            row(
                "worker: sync_to_async 트립 (스레드 쪽 나머지)",
                "worker",
                "thread_handler",
                ("close_old_connections", "collect_context", "template render", "sign_state (taker's trip)"),
            ),
            row("loop: 공유 렌더의 키 (필드·언어·시간대)", "loop", "shared: key"),
            row("loop: 연결의 토큰 끼우기 (with_state)", "loop", "shared: with_state"),
            row("loop: diff 계산 (마커 파싱은 메시지당 한 번)", "loop", "diff: against last"),
            row("loop: 마커 파싱 (메시지당 한 번)", "loop", "shared: parse"),
            row("loop: JSON 직렬화 (encode_json)", "loop", "encode_json"),
            row("loop: WebSocket 프레임 쓰기 (deflate 제외)", "loop", "ws_send", ("deflate",)),
            row("loop: permessage-deflate 압축", "loop", "deflate"),
            row(
                "loop: 세션 코드 (send_render의 나머지)",
                "loop",
                "send_render outside the shared render",
                ("diff: against last", "encode_json", "ws_send"),
            ),
        ]
    elif name == "wireview":
        rows = [
            row("publish: group_send의 member마다 deepcopy + put", "loop", "layer.send (deepcopy + put)"),
            row("loop: InMemory 레이어 _clean_expired (receive마다 전체 순회)", "loop", "layer._clean_expired"),
            row("worker: close_old_connections (메시지마다 3번)", "worker", "close_old_connections"),
            row("worker: 컨텍스트 읽기 (_collect_context)", "worker", "collect_context"),
            row("worker: 템플릿 렌더 (서명 제외)", "worker", "template render", ("sign_state",)),
            row("worker: data-state 서명 (sign_state)", "worker", "sign_state"),
            row(
                "worker: sync_to_async 트립 (스레드 쪽 나머지)",
                "worker",
                "thread_handler",
                ("close_old_connections", "collect_context", "template render"),
            ),
            row("loop: diff 계산", "loop", "diff"),
            row("loop: JSON 직렬화 (encode_json)", "loop", "encode_json"),
            row("loop: WebSocket 프레임 쓰기 (deflate 제외)", "loop", "ws_send", ("deflate",)),
            row("loop: permessage-deflate 압축", "loop", "deflate"),
            row(
                "loop: 세션 코드 (send_render의 나머지)",
                "loop",
                "send_render (wall)",
                ("await render trip (wall)", "diff", "encode_json", "ws_send"),
            ),
        ]
    else:
        rows = [
            row("loop: JSON 직렬화 (json.dumps, 한 번)", "loop", "json.dumps"),
            row("loop: starlette send_text (uvicorn 제외)", "loop", "starlette send_text (wall)", ("ws_send",)),
            row("loop: WebSocket 프레임 쓰기 (deflate 제외)", "loop", "ws_send", ("deflate",)),
            row("loop: permessage-deflate 압축", "loop", "deflate"),
        ]
    on_loop = [r for r in rows if r[0].startswith(("loop", "publish"))]
    rows.append(
        (
            "loop: 그 밖 (asyncio·Channels 디스패치·트립 예약)",
            summary["loop_cpu_ns"] * ms - sum(r[1] for r in on_loop),
            summary["loop_busy_ns"] * ms - sum(r[2] for r in on_loop),
        )
    )
    return rows


def report(result: dict[str, t.Any]) -> str:
    lines = []
    connections = result["method"]["connections"]
    ms = 1e-6
    for name, data in result["implementations"].items():
        s = data["summary"]
        lines.append(f"\n## {name} — 연결 {connections}개, 회차 {len(data['rounds'])}개")
        lines.append(
            f"클라이언트가 본 팬아웃 {s['client_span_ns'] * ms:.1f} ms "
            f"(회차 {s['client_span_range_ns'][0] * ms:.1f}~{s['client_span_range_ns'][1] * ms:.1f}), "
            f"서버 구간 {s['server_span_ns'] * ms:.1f} ms "
            f"(회차 {s['server_span_range_ns'][0] * ms:.1f}~{s['server_span_range_ns'][1] * ms:.1f}), "
            f"마지막 프레임 뒤 마지막 수신까지 {s['last_receive_after_last_send_ns'] * ms:.1f} ms"
        )
        lines.append(
            f"계측을 끈 브로드캐스트의 클라이언트 팬아웃 {s['client_span_unarmed_ns'] * ms:.1f} ms "
            f"(회차 {s['client_span_unarmed_range_ns'][0] * ms:.1f}~{s['client_span_unarmed_range_ns'][1] * ms:.1f})"
        )
        lines.append(
            f"루프: busy {s['loop_busy_ns'] * ms:.1f} ms (CPU {s['loop_cpu_ns'] * ms:.1f}) / "
            f"select 대기 {s['loop_idle_ns'] * ms:.1f} ms. "
            f"워커: busy {s['worker_busy_ns'] * ms:.1f} ms (CPU {s['worker_cpu_ns'] * ms:.1f}, "
            f"스레드 {s['worker_threads']}개). 둘 다 busy {s['both_busy_ns'] * ms:.1f} ms. "
            f"루프가 GIL을 기다린 시간(busy - CPU) {(s['loop_busy_ns'] - s['loop_cpu_ns']) * ms:.1f} ms"
        )
        if s.get("first_dispatch_ns") is not None:
            lines.append(
                f"publish {s['publish_ns'] * ms:.2f} ms, 첫 디스패치 {s['first_dispatch_ns'] * ms:.2f} ms, "
                f"마지막 디스패치 {s['last_dispatch_ns'] * ms:.1f} ms, 첫 프레임 {s['first_send_ns'] * ms:.2f} ms"
            )
        rows = stages(s, name)
        total = (s["loop_cpu_ns"] + s["worker_cpu_ns"]) * ms
        # CPU only: a stage's wall time also holds the GIL waits the other thread caused
        lines.append("\n| 단계 | CPU ms | 연결당 CPU µs | CPU 비율 |\n|---|---:|---:|---:|")
        for stage, used, _wall in rows:
            lines.append(f"| {stage} | {used:.1f} | {used / connections * 1000:.1f} | {used / total:.1%} |")
        per_connection = total / connections * 1000
        lines.append(f"| **합 (루프 CPU + 워커 CPU)** | **{total:.1f}** | **{per_connection:.1f}** | 100% |")
    return "\n".join(lines)


#: The stages of ``stages()`` in the groups docs/design/broadcast-fanout.md charts
GROUPS = {
    "렌더": (
        "worker: 컨텍스트",
        "worker: 템플릿",
        "worker: data-state",
        "loop: 공유 렌더",
        "loop: data-state",
        "loop: 연결의 토큰",
    ),
    "diff": ("loop: diff", "loop: 마커"),
    "트립·디스패치": ("loop: 그 밖", "worker: sync_to_async", "worker: close_old"),
    "채널 레이어": ("publish:", "loop: InMemory"),
    "세션 코드": ("loop: 세션 코드",),
    "JSON·프레임·압축": ("loop: JSON", "loop: WebSocket", "loop: permessage"),
}


def chart(result: dict[str, t.Any]) -> str:
    """Mermaid charts of a result: the per-connection CPU by group beside FastAPI's, and the fan-out."""
    connections = result["method"]["connections"]
    impls = result["implementations"]
    wireview = impls["wireview"]["summary"]
    per_connection = {}
    for group, prefixes in GROUPS.items():
        used = sum(c for label, c, _ in stages(wireview, "wireview") if label.startswith(prefixes))
        per_connection[group] = used / connections * 1000
    labels = [*per_connection, "FastAPI 전체"]
    values = list(per_connection.values())
    if "fastapi" in impls:
        fastapi = impls["fastapi"]["summary"]
        values.append((fastapi["loop_cpu_ns"] + fastapi["worker_cpu_ns"]) / connections / 1000)
    else:
        labels.pop()

    def block(title: str, axis: str, names: list[str], numbers: list[float]) -> str:
        quoted = ", ".join(f'"{n}"' for n in names)
        shown = ", ".join(f"{v:.1f}" for v in numbers)
        top = max(numbers) * 1.1
        return (
            f'```mermaid\nxychart-beta horizontal\n    title "{title}"\n    x-axis [{quoted}]\n'
            f'    y-axis "{axis}" 0 --> {top:.0f}\n    bar [{shown}]\n```'
        )

    spans = {"wireview": wireview["client_span_unarmed_ns"] / 1e6}
    if "fastapi" in impls:
        spans["FastAPI"] = impls["fastapi"]["summary"]["client_span_unarmed_ns"] / 1e6
    return "\n\n".join(
        [
            block(f"연결 하나당 CPU, wireview 단계 묶음과 FastAPI 전체 (연결 {connections:,}개)", "µs", labels, values),
            block(f"브로드캐스트 팬아웃 (연결 {connections:,}개, 계측 끔)", "ms", list(spans), list(spans.values())),
        ]
    )


#: The result docs/design/broadcast-fanout.md quotes: the measurement it was written from
RESULT = ROOT / "bench" / "results" / "5a4f037-fanout-profile.json"


def _range(values: t.Iterable[float], digits: int = 1) -> str:
    values = list(values)
    low, high = f"{min(values):.{digits}f}", f"{max(values):.{digits}f}"
    return low if low == high else f"{low}~{high}"


def facts(result: dict[str, t.Any]) -> dict[str, str]:
    """Every number docs/design/broadcast-fanout.md §1-§2 quotes from ``result``, as the document writes it.

    ``result`` is a fan-out measurement with the runs the design took beside it:
    ``inproc_runs`` (``inproc``, three times), ``plain_runs`` (``plain`` with the GIL
    once, then without it), and ``scale_runs`` (one round at each connection count).
    """
    ms = 1e-6
    connections = result["method"]["connections"]
    out: dict[str, str] = {}
    impls = result["implementations"]
    for name, data in impls.items():
        s = data["summary"]
        lo, hi = s["client_span_unarmed_range_ns"]
        out[f"{name}.fanout"] = f"{s['client_span_unarmed_ns'] * ms:.1f} ms"
        out[f"{name}.fanout_rounds"] = f"{lo * ms:.1f}~{hi * ms:.1f}"
        out[f"{name}.server_span"] = f"{s['server_span_ns'] * ms:.1f} ms"
        out[f"{name}.loop_cpu"] = f"{s['loop_cpu_ns'] * ms:.1f} ms"
        out[f"{name}.loop_busy"] = f"{s['loop_busy_ns'] * ms:.1f} ms"
        out[f"{name}.last_receive"] = f"{s['last_receive_after_last_send_ns'] * ms:.2f} ms"
        rows = stages(s, name)
        total = (s["loop_cpu_ns"] + s["worker_cpu_ns"]) * ms
        for label, used, _wall in rows:
            out[f"{name}.stage.{label}"] = f"{used / connections * 1000:.1f}"
            out[f"{name}.share.{label}"] = f"{used / total:.1%}"
        out[f"{name}.per_connection"] = f"{total / connections * 1000:.1f}"
    w = impls["wireview"]["summary"]
    out["wireview.client_span_armed"] = f"{w['client_span_ns'] * ms:.1f} ms"
    out["wireview.worker"] = f"{w['worker_cpu_ns'] * ms:.1f} ms / {w['worker_busy_ns'] * ms:.1f} ms"
    out["wireview.loop_waited"] = f"{(w['loop_busy_ns'] - w['loop_cpu_ns']) * ms:.1f} ms"
    out["wireview.loop_idle"] = f"{w['loop_idle_ns'] * ms:.1f} ms"
    out["wireview.both_busy"] = f"{w['both_busy_ns'] * ms:.1f} ms"
    out["wireview.cpu_sum"] = f"{(w['loop_cpu_ns'] + w['worker_cpu_ns']) * ms:.1f} ms"
    out["wireview.dispatch"] = (
        f"{w['first_dispatch_ns'] * ms:.1f} ms / {w['last_dispatch_ns'] * ms:.1f} ms / {w['first_send_ns'] * ms:.1f} ms"
    )
    out["fastapi.first_send"] = f"{impls['fastapi']['summary']['first_send_ns'] * ms:.2f} ms"
    loop = w["cpu"]["loop"]
    for key, stage in (("parse", "diff: parse markers"), ("compare", "diff: compare"), ("settle", "diff: settle")):
        out[f"wireview.diff.{key}"] = f"{loop.get(stage, 0) / connections / 1000:.1f}"
    out["wireview.diff.payload"] = f"{loop.get('diff: to_payload', 0) / connections / 1000:.1f}"
    out["wireview.calls.thread_handler"] = f"{w['calls']['worker.thread_handler']:,.0f}"
    out["wireview.calls.close_old_connections"] = f"{w['calls']['worker.close_old_connections']:,.0f}"
    total = (w["loop_cpu_ns"] + w["worker_cpu_ns"]) * ms
    for group, prefixes in GROUPS.items():
        used = sum(c for label, c, _ in stages(w, "wireview") if label.startswith(prefixes))
        out[f"group.{group}"] = f"{used / connections * 1000:.1f}"
        out[f"group_share.{group}"] = f"{used / total:.1%}"

    runs = [run["inproc"] for run in result.get("inproc_runs", [])]
    if runs:
        for part in runs[0]["board_us"]:
            out[f"inproc.{part}"] = f"{statistics.median(r['board_us'][part] for r in runs):.1f}"
        for trip in runs[0]["trip_us"]:
            if trip.startswith("sync_to_async"):
                continue  # measured beside the others, not quoted
            out[f"trip.{trip}"] = f"{statistics.median(r['trip_us'][trip] for r in runs):.1f} µs"
        out["inproc.render_diff"] = f"{statistics.median(r['render_diff_us_one_after_another'] for r in runs):.1f}"
        for threads in ("1", "2", "4", "8"):
            out[f"threads.gil.{threads}"] = _range(r["plain_render_us_by_threads"][threads] for r in runs)
    plain = [run["plain"] for run in result.get("plain_runs", [])]
    if plain:
        gil = next(p for p in plain if p["gil"])
        nogil = [p for p in plain if not p["gil"]]
        for threads in ("1", "2", "4", "8"):
            out[f"threads.plain.{threads}"] = f"{gil['plain_render_us_by_threads'][threads]:.1f}"
            out[f"threads.nogil.{threads}"] = _range(p["plain_render_us_by_threads"][threads] for p in nogil)
        one = gil["plain_render_us_by_threads"]["1"]
        slower = [p["plain_render_us_by_threads"]["1"] / one - 1 for p in nogil]
        out["threads.nogil.slower"] = f"{min(slower):.0%}~{max(slower):.0%}".replace("%~", "~")
        gain = [p["plain_render_us_by_threads"]["1"] / p["plain_render_us_by_threads"]["4"] for p in nogil]
        out["threads.nogil.gain4"] = f"{_range(gain)}배"
    for run in result.get("scale_runs", []):
        n = run["method"]["connections"]
        for name, data in run["implementations"].items():
            span = data["summary"]["client_span_unarmed_ns"]
            out[f"scale.{n}.{name}"] = f"{span * ms:,.1f} ms"
            out[f"scale.{n}.{name}.per_connection"] = f"{span / n / 1000:.{0 if name == 'wireview' else 1}f} µs"
            if name == "wireview":
                expired = data["summary"]["cpu"]["loop"].get("layer._clean_expired", 0)
                out[f"scale.{n}.clean_expired"] = f"{expired / n / 1000:.1f} µs"
    return out


#: Stage 1 (B) of #176, measured: docs/design/broadcast-fanout.md §6 quotes it
RESULT_B = ROOT / "bench" / "results" / "a6994e5-fanout-profile.json"
#: The inproc parts §6 follows commit by commit
STEP_PARTS = {
    "context": "collect_context",
    "marked": "template render, marked (wireview)",
    "plain": "template render, plain Django",
    "localize": "localize() of the ints the template prints",
    "ints": "the same ints as a marked variable prints them",
    "parse": "diff: parse markers",
}


def _per_connection(result: dict[str, t.Any]) -> float:
    s = result["implementations"]["wireview"]["summary"]
    return (s["loop_cpu_ns"] + s["worker_cpu_ns"]) / result["method"]["connections"] / 1000


def _fanout_ms(result: dict[str, t.Any], name: str = "wireview") -> float:
    return result["implementations"][name]["summary"]["client_span_unarmed_ns"] / 1e6


def _sides(result: dict[str, t.Any]) -> dict[str, list[dict[str, t.Any]]]:
    """The fan-out runs before B and after it: the same day, alternating."""
    return {"before": result["before"]["fanout"], "after": [result, *result["after_more"]["fanout"]]}


def progress_facts(result: dict[str, t.Any]) -> dict[str, str]:
    """Every number docs/design/broadcast-fanout.md §6 quotes from the stage 1 (B) result."""
    out: dict[str, str] = {}
    steps = result["steps"]
    medians: dict[str, dict[str, float]] = {}
    for label in steps["order"]:
        runs = steps["inproc"][label]
        row = {
            key: statistics.median(r["board_us"][part] for r in runs)
            for key, part in STEP_PARTS.items()
            if part in runs[0]["board_us"]
        }
        row["render_diff"] = statistics.median(r["render_diff_us_one_after_another"] for r in runs)
        medians[label] = row
        out[f"step.{label}.commit"] = steps["commits"][label]
        ends = label in (steps["order"][0], steps["order"][-1])
        for key, value in row.items():
            if key in ("plain", "localize") and not ends:
                continue  # the overhead's terms: §6 quotes them before B and after it
            out[f"step.{label}.{key}"] = f"{value:.1f}"
    first, last = medians[steps["order"][0]], medians[steps["order"][-1]]
    out["step.load"] = f"{min(x[0] for x in steps['loads']):.1f}~{max(x[0] for x in steps['loads']):.1f}"
    out["step.render_diff_cut"] = f"{1 - last['render_diff'] / first['render_diff']:.0%}"
    out["overhead.before"] = f"{first['marked'] - first['plain']:.1f}"
    out["overhead.after"] = f"{last['marked'] - (last['plain'] - last['localize'] + last['ints']):.1f}"

    sides = _sides(result)
    for side, runs in sides.items():
        spans = [_fanout_ms(r) for r in runs]
        cpu = [_per_connection(r) for r in runs]
        out[f"{side}.fanout"] = f"{statistics.median(spans):.1f} ms"
        out[f"{side}.fanout_runs"] = " · ".join(f"{v:.1f}" for v in spans)
        out[f"{side}.per_connection"] = f"{statistics.median(cpu):.1f} µs"
        out[f"{side}.per_connection_runs"] = " · ".join(f"{v:.1f}" for v in cpu)
        out[f"{side}.load"] = _range(r["environment"]["load_average"][0] for r in runs)
    out["fastapi.fanout"] = f"{_fanout_ms(result, 'fastapi'):.1f} ms"
    fastapi = result["implementations"]["fastapi"]["summary"]
    connections = result["method"]["connections"]
    out["fastapi.per_connection"] = f"{(fastapi['loop_cpu_ns'] + fastapi['worker_cpu_ns']) / connections / 1000:.1f} µs"
    before_span = statistics.median(map(_fanout_ms, sides["before"]))
    before_cpu = statistics.median(map(_per_connection, sides["before"]))
    out["cut.fanout"] = f"{1 - statistics.median(map(_fanout_ms, sides['after'])) / before_span:.0%}"
    out["cut.per_connection"] = f"{1 - statistics.median(map(_per_connection, sides['after'])) / before_cpu:.0%}"

    # One pair measured back to back, stage by stage (CPU per connection)
    before, after = sides["before"][0], result
    for side, run in (("before", before), ("after", after)):
        summary = run["implementations"]["wireview"]["summary"]
        connections = run["method"]["connections"]
        for group, prefixes in GROUPS.items():
            used = sum(c for label, c, _ in stages(summary, "wireview") if label.startswith(prefixes))
            out[f"pair.{side}.{group}"] = f"{used / connections * 1000:.1f}"
        out[f"pair.{side}.total"] = f"{_per_connection(run):.1f}"

    for side in ("before", "after"):
        clicks = result["clicks"][side]["implementations"]["wireview"]["summary"]
        for scenario in ("change_value", "insert_front"):
            server = clicks[scenario]["server_ms"]
            out[f"clicks.{side}.{scenario}"] = f"{server['median']:.2f} ms"
            out[f"clicks.{side}.{scenario}.rounds"] = _range((r["median"] for r in server["rounds"]), 2)
        out[f"clicks.{side}.fanout"] = f"{clicks['fanout_ms']['median']:.1f} ms"
        runs = [run["clicks"]["actions"] for run in result["clicks_inproc"][side]]
        for action in ("increment", "insert"):
            awake = [run[action]["0.0"]["median_ms"] for run in runs]
            gapped = [run[action]["0.002"]["median_ms"] for run in runs]
            out[f"inproc_clicks.{side}.{action}"] = f"{_range(awake, 2)} ms"
            out[f"inproc_clicks.{side}.{action}.gapped"] = f"{_range(gapped, 2)} ms"
    return out


#: ``make bench-compare BASE=5a4f037`` against stage 1 (B): what §6 quotes of it
BENCH_COMPARE = ("5a4f037", "a6994e5")
COMPARED = (
    "timing.flat.event_ms",
    "timing.list.event_ms",
    "timing.list.template_render_ms",
    "timing.list500.rotate_ms",
    "ws.items_50.broadcast_ms",
    "ws.items_50.events_per_s",
)


def compare_facts() -> dict[str, str]:
    """The ``make bench-compare`` numbers §6 quotes, from the two results it wrote."""
    out = {}
    before, after = (json.loads((ROOT / "bench" / "results" / f"{sha}.json").read_text()) for sha in BENCH_COMPARE)

    def get(result: dict[str, t.Any], key: str) -> float:
        section, rest = key.split(".", 1)
        if section == "ws":  # nested by scenario: ws.<scenario>.<metric>
            scenario, metric = rest.split(".", 1)
            return result[section][scenario][metric]
        return result[section][rest]  # flat: timing.<scenario.metric>

    for key in COMPARED:
        a, b = get(before, key), get(after, key)
        digits = 0 if key.endswith(("_s", "broadcast_ms")) else 3
        out[f"compare.{key}"] = f"{a:,.{digits}f} → {b:,.{digits}f}"
        out[f"compare.{key}.change"] = f"{b / a - 1:+.0%}"
    return out


def progress_chart(design: dict[str, t.Any], result: dict[str, t.Any]) -> str:
    """Mermaid charts of stage 1 (B): before and after it, beside the design's measurement and FastAPI."""
    sides = _sides(result)
    names = ["설계 측정 (5a4f037)", "B 전 (같은 날)", "B 후", "FastAPI"]
    connections = result["method"]["connections"]
    fastapi = result["implementations"]["fastapi"]["summary"]
    cpu = [
        _per_connection(design),
        statistics.median(map(_per_connection, sides["before"])),
        statistics.median(map(_per_connection, sides["after"])),
        (fastapi["loop_cpu_ns"] + fastapi["worker_cpu_ns"]) / connections / 1000,
    ]
    spans = [
        _fanout_ms(design),
        statistics.median(map(_fanout_ms, sides["before"])),
        statistics.median(map(_fanout_ms, sides["after"])),
        _fanout_ms(result, "fastapi"),
    ]

    def block(title: str, axis: str, numbers: list[float]) -> str:
        quoted = ", ".join(f'"{n}"' for n in names)
        shown = ", ".join(f"{v:.1f}" for v in numbers)
        return (
            f'```mermaid\nxychart-beta horizontal\n    title "{title}"\n    x-axis [{quoted}]\n'
            f'    y-axis "{axis}" 0 --> {max(numbers) * 1.1:.0f}\n    bar [{shown}]\n```'
        )

    return "\n\n".join(
        [
            block(f"연결 하나당 CPU, 1단계(B) 전후 (연결 {connections:,}개)", "µs", cpu),
            block(f"브로드캐스트 팬아웃, 1단계(B) 전후 (연결 {connections:,}개, 계측 끔)", "ms", spans),
        ]
    )


#: Stage 2 (A) of #176, measured: docs/design/broadcast-fanout.md §7 quotes it, with the
#: make bench-fastapi run it names (``bench_fastapi``)
RESULT_A = ROOT / "bench" / "results" / "cef17df-fanout-shared.json"


def _cpu_per_connection(result: dict[str, t.Any], name: str) -> float:
    s = result["implementations"][name]["summary"]
    return (s["loop_cpu_ns"] + s["worker_cpu_ns"]) / result["method"]["connections"] / 1000


def shared_facts(result: dict[str, t.Any]) -> dict[str, str]:
    """Every number docs/design/broadcast-fanout.md §7 quotes from the stage 2 (A) result."""
    ms = 1e-6
    out: dict[str, str] = {}
    connections = result["method"]["connections"]
    impls = result["implementations"]
    for name in ("wireview", "wireview-shared", "fastapi"):
        s = impls[name]["summary"]
        lo, hi = s["client_span_unarmed_range_ns"]
        out[f"a.{name}.fanout"] = f"{s['client_span_unarmed_ns'] * ms:.1f} ms"
        out[f"a.{name}.fanout_rounds"] = f"{lo * ms:.1f}~{hi * ms:.1f}"
        out[f"a.{name}.per_connection"] = f"{_cpu_per_connection(result, name):.1f} µs"
    shared = impls["wireview-shared"]["summary"]
    out["a.wireview-shared.armed"] = f"{shared['client_span_ns'] * ms:.1f} ms"
    for label, used, _wall in stages(shared, "wireview-shared"):
        out[f"a.stage.{label}"] = f"{used / connections * 1000:.1f}"
    for name in ("wireview", "wireview-shared"):
        summary = impls[name]["summary"]
        for group, prefixes in GROUPS.items():
            used = sum(c for label, c, _ in stages(summary, name) if label.startswith(prefixes))
            out[f"a.group.{name}.{group}"] = f"{used / connections * 1000:.1f}"
    out["a.worker_calls"] = f"{shared['calls'].get('worker.template render', 0):.0f}"
    plain = _cpu_per_connection(result, "wireview")
    out["a.cut.per_connection"] = f"{1 - _cpu_per_connection(result, 'wireview-shared') / plain:.0%}"
    out["a.cut.fanout"] = (
        f"{1 - shared['client_span_unarmed_ns'] / impls['wireview']['summary']['client_span_unarmed_ns']:.0%}"
    )
    out["a.load"] = (
        f"{result['environment']['load_average'][0]:.1f} → {result['environment']['load_average_after'][0]:.1f}"
    )

    runs = [run["inproc"] for run in result["inproc_runs"]]
    shared_runs = [r["shared_render_us"] for r in runs]
    out["a.inproc.lead"] = _range(r["render_diff, the connection that renders"] for r in shared_runs)
    out["a.inproc.take"] = _range(r["render_diff, each connection that takes it"] for r in shared_runs)
    out["a.inproc.key"] = _range(r["key"] for r in shared_runs)
    out["a.inproc.render_diff"] = _range(r["render_diff_us_one_after_another"] for r in runs)
    out["a.inproc.load"] = _range(run["environment"]["load_average"][0] for run in result["inproc_runs"])

    compare = json.loads((ROOT / result["bench_fastapi"]).read_text())
    for name, data in compare["implementations"].items():
        s = data["summary"]
        out[f"b.{name}.fanout"] = f"{s['fanout_ms']['median']:.1f} ms"
        out[f"b.{name}.fanout_rounds"] = f"{s['fanout_ms']['median_min']:.1f}~{s['fanout_ms']['median_max']:.1f}"
        out[f"b.{name}.received_bytes"] = f"{s['fanout_received_bytes']:.0f} B"
        for scenario in ("change_value", "insert_front"):
            out[f"b.{name}.{scenario}"] = f"{s[scenario]['server_ms']['median']:.2f} ms"
    loc = compare["loc"]["wireview"]["app"]
    out["b.loc"] = f"{loc} → {loc + 1}"
    env = compare["environment"]
    out["b.load"] = f"{env['load_average'][0]:.1f} → {env['load_average_after'][0]:.1f}"
    return out


def shared_chart(result_b: dict[str, t.Any], result: dict[str, t.Any]) -> str:
    """Mermaid charts of stage 2 (A): the board with and without shared_render, beside B and FastAPI."""
    names = ["B 후 (§6)", "A 끔 (같은 회차)", "A 켬", "FastAPI (같은 회차)"]
    cpu = [
        statistics.median(map(_per_connection, _sides(result_b)["after"])),
        _cpu_per_connection(result, "wireview"),
        _cpu_per_connection(result, "wireview-shared"),
        _cpu_per_connection(result, "fastapi"),
    ]
    spans = [
        statistics.median(map(_fanout_ms, _sides(result_b)["after"])),
        _fanout_ms(result, "wireview"),
        _fanout_ms(result, "wireview-shared"),
        _fanout_ms(result, "fastapi"),
    ]
    connections = result["method"]["connections"]

    def block(title: str, axis: str, numbers: list[float]) -> str:
        quoted = ", ".join(f'"{n}"' for n in names)
        shown = ", ".join(f"{v:.1f}" for v in numbers)
        return (
            f'```mermaid\nxychart-beta horizontal\n    title "{title}"\n    x-axis [{quoted}]\n'
            f'    y-axis "{axis}" 0 --> {max(numbers) * 1.1:.0f}\n    bar [{shown}]\n```'
        )

    return "\n\n".join(
        [
            block(f"연결 하나당 CPU, 2단계(A) (연결 {connections:,}개)", "µs", cpu),
            block(f"브로드캐스트 팬아웃, 2단계(A) (연결 {connections:,}개, 계측 끔)", "ms", spans),
        ]
    )


def shared_performance_chart(result: dict[str, t.Any]) -> str:
    """The fan-out chart docs/PERFORMANCE.md shows: the board with and without shared_render, and FastAPI."""
    names = ["선언하지 않음", "shared_render = True", "FastAPI"]
    spans = [_fanout_ms(result, name) for name in ("wireview", "wireview-shared", "fastapi")]
    quoted = ", ".join(f'"{n}"' for n in names)
    shown = ", ".join(f"{v:.1f}" for v in spans)
    return (
        f'```mermaid\nxychart-beta horizontal\n    title "브로드캐스트 하나가 연결 '
        f'{result["method"]["connections"]:,}개에 닿기까지 (계측 끔)"\n'
        f'    x-axis [{quoted}]\n    y-axis "ms" 0 --> {max(spans) * 1.1:.0f}\n    bar [{shown}]\n```'
    )


async def drive(args: argparse.Namespace) -> dict[str, t.Any]:
    names = args.only or list(IMPLEMENTATIONS)
    out: dict[str, t.Any] = {name: {"rounds": []} for name in names}
    for round_ in range(args.rounds):
        for name in names if round_ % 2 == 0 else list(reversed(names)):
            print(f"round {round_ + 1}/{args.rounds}: {name}", flush=True)
            with Server(name, args.cprofile) as server:
                broadcasts = await _round(server, args.connections, args.warmup, args.broadcasts)
            entry = _median(broadcasts)
            if args.cprofile:
                entry["profile"] = broadcasts[-1].get("profile")
            out[name]["rounds"].append(entry)
    for name in names:
        out[name]["summary"] = _across(out[name]["rounds"])
    return out


def environment() -> dict[str, t.Any]:
    import platform
    from importlib.metadata import version

    from bench.compare_fastapi.measure import _run

    packages = {}
    for package in (
        "django",
        "channels",
        "django-wireview",
        "fastapi",
        "starlette",
        "uvicorn",
        "websockets",
        "asgiref",
    ):
        try:
            packages[package] = version(package)
        except Exception:
            packages[package] = None
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _run("git", "rev-parse", "--short", "HEAD"),
        "dirty": bool(_run("git", "status", "--porcelain")),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "python": platform.python_version(),
        "gil": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "packages": packages,
        "server": "uvicorn, 1 process, --ws websockets, --log-level warning, permessage-deflate on (default)",
    }


# -- in-process: one Board's render by part, worker trips, render threads -------------------

BOARD_TEMPLATE = ROOT / "bench" / "compare_fastapi" / "wv" / "board" / "templates" / "board" / "board.html"


def _plain_board() -> t.Callable[[], str]:
    """Render Board's template without wireview's tags, compiled by Django alone, with a context like Board's.

    Needs nothing but Django, so the same measurement runs on an interpreter without the GIL.
    Each call builds its own Context, as Django's backend does: a Context binds to one render.
    """
    import re

    from django.template import Context, Engine

    source = BOARD_TEMPLATE.read_text()
    source = re.sub(r"{%\s*(load wireview|tag_header|on [^%]*?)\s*%}", "", source)
    template = Engine(autoescape=True).from_string(source)
    from bench.compare_fastapi.store import Store

    store = Store()
    context = {"count": store.count, "announcement": store.announcement, "items": list(store.items)}
    return lambda: template.render(Context(context))


def _per_board_us(render: t.Callable[[], t.Any], boards: int, repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        started = ns()
        for _ in range(boards):
            render()
        samples.append((ns() - started) / boards / 1000)
    return statistics.median(samples)


def _threads_us(render: t.Callable[[], t.Any], boards: int, repeat: int) -> dict[str, float]:
    """``render`` ``boards`` times split over 1, 2, 4, 8 threads: µs of wall time per render."""
    from concurrent.futures import ThreadPoolExecutor

    out = {}
    for workers in (1, 2, 4, 8):
        share = boards // workers

        def run(_):
            for _ in range(share):
                render()

        samples = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run, range(workers)))  # threads started, warm
            for _ in range(repeat):
                started = ns()
                list(pool.map(run, range(workers)))
                samples.append((ns() - started) / (share * workers) / 1000)
        out[str(workers)] = statistics.median(samples)
    return out


def plain(args: argparse.Namespace) -> dict[str, t.Any]:
    """Django's own render of Board's template, alone and on several threads (no wireview, no markers)."""
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(USE_TZ=True, USE_I18N=True)
        django.setup()
    render = _plain_board()
    for _ in range(50):
        render()
    return {
        "python": sys.version.split()[0],
        "gil": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "plain_render_us": _per_board_us(render, args.boards, args.repeat),
        "plain_render_us_by_threads": _threads_us(render, args.boards, args.repeat),
    }


async def inproc(args: argparse.Namespace) -> dict[str, t.Any]:
    """One Board's live render taken apart, what one worker trip costs, and what threads would buy.

    Runs in this process against the compare_fastapi settings: Board mounted with a live
    repository and rendered through the code the consumer's render runs, one after another.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.compare_fastapi.wv.settings")
    import django

    django.setup()
    from asgiref.sync import sync_to_async
    from channels.db import aclose_old_connections, database_sync_to_async
    from django.template import Context
    from django.utils import formats

    from bench.compare_fastapi.store import store
    from bench.compare_fastapi.wv.board.live import Board
    from bench.payload import _live
    from wireview.core.rendered import Rendered
    from wireview.core.state import sign_state
    from wireview.template_engine import render_value

    repeat, boards = args.repeat, args.boards
    out: dict[str, t.Any] = {"gil": getattr(sys, "_is_gil_enabled", lambda: True)(), "boards": boards, "repeat": repeat}

    async def per_call_us(make, calls: int, gathered: bool) -> float:
        samples = []
        for _ in range(repeat):
            started = ns()
            if gathered:
                await asyncio.gather(*(make() for _ in range(calls)))
            else:
                for _ in range(calls):
                    await make()
            samples.append((ns() - started) / calls / 1000)
        return statistics.median(samples)

    noop = database_sync_to_async(lambda: None)
    out["trip_us"] = {
        "database_sync_to_async(noop), one after another": await per_call_us(noop, 1000, False),
        "sync_to_async(noop), one after another": await per_call_us(sync_to_async(lambda: None), 1000, False),
        "aclose_old_connections(), one after another": await per_call_us(aclose_old_connections, 1000, False),
        "database_sync_to_async(noop), 1000 gathered": await per_call_us(noop, 1000, True),
        "aclose_old_connections(), 1000 gathered": await per_call_us(aclose_old_connections, 1000, True),
    }

    view = await _live(Board, id="board")
    wire, component, repo = view.wire, view.component, view._repo
    await wire.render_diff(component, repo)  # first render: template compiled and prepared, token issued

    def collect():
        return wire._collect_context(component, repo, None)

    context = collect()

    def marked():
        return wire._render_with_context(component, context, None)

    plain_render = _plain_board()
    bare = Context()
    previous = wire._last_rendered
    store.announce()
    html = str(marked())

    def parse():
        return Rendered.from_marked_html(html)

    def diff():
        rendered = Rendered.from_marked_html(html)
        rendered.settle(previous)
        result = rendered.get_diff(previous, repo.vsn)
        return result.to_payload() if result is not None else None

    payload = {"command": "render", "payload": {"id": "board", "diff": diff()}}
    ints = sum(1 for _ in store.items) + 2  # {{ item.qty }} per item, {{ count }}, {{ announcement }}

    def run(fn):
        return _per_board_us(fn, boards, repeat)

    parts = {
        "collect_context": run(collect),
        "template render, marked (wireview)": run(marked),
        "template render, plain Django": run(plain_render),
        "localize() of the ints the template prints": run(lambda: [formats.localize(i) for i in range(ints)]),
        "the same ints as a marked variable prints them": run(lambda: [render_value(i, bare) for i in range(ints)]),
        "sign_state": run(lambda: sign_state(component)),
        "diff: parse markers": run(parse),
        "diff: parse + settle + compare + to_payload": run(diff),
        "json.dumps of the render frame": run(lambda: json.dumps(payload)),
    }
    out["board_us"] = parts
    out["ints_localized_per_render"] = ints

    async def full():
        await wire.render_diff(component, repo)

    samples = []
    for _ in range(repeat):
        started = ns()
        for _ in range(boards):
            store.announce()
            await full()
        samples.append((ns() - started) / boards / 1000)
    out["render_diff_us_one_after_another"] = statistics.median(samples)
    out["shared_render_us"] = await _shared_inproc(Board, store, boards, repeat)
    if args.cprofile:
        # Which functions one live render spends its time in (the times are inflated, the shares hold)
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(boards):
            store.announce()
            await full()
            await database_sync_to_async(lambda: None)()  # the trip close_old_connections takes
        profiler.disable()
        out["render_diff_profile"] = _profile_text(profiler, limit=60)
    out["plain_render_us_by_threads"] = _threads_us(plain_render, boards, repeat)
    return out


async def _shared_inproc(board: t.Any, store: t.Any, boards: int, repeat: int) -> dict[str, float]:
    """Board declaring ``Meta.shared_render`` (#176, stage 2), ``boards`` connections handling one message.

    The first renders and parses; every other takes that render, signs its own state,
    puts the token in and diffs against its own page. Timed one after another.
    """
    import dataclasses

    from bench.payload import _live
    from wireview.core import shared_render

    plain, board._meta = board._meta, dataclasses.replace(board._meta, shared_render=True)
    try:
        views = [await _live(board, id="board") for _ in range(boards)]
        for view in views:
            await view.wire.render_diff(view.component, view._repo)
        # The render that leads, one message after another as render_diff_us_one_after_another is:
        # timed alone, each paid the wake of a worker thread that slept through the others
        lead, follow = [], []
        for n in range(repeat):
            started = ns()
            for m in range(boards):
                store.announce()
                with shared_render.handling(f"inproc-lead-{n}-{m}"):
                    await views[0].wire.render_diff(views[0].component, views[0]._repo)
            lead.append((ns() - started) / boards / 1000)
        for n in range(repeat):
            store.announce()
            with shared_render.handling(f"inproc-{n}"):
                await views[0].wire.render_diff(views[0].component, views[0]._repo)
                started = ns()
                for view in views[1:]:
                    await view.wire.render_diff(view.component, view._repo)
                follow.append((ns() - started) / (boards - 1) / 1000)
        first = views[1]
        return {
            "render_diff, the connection that renders": statistics.median(lead),
            "render_diff, each connection that takes it": statistics.median(follow),
            "key": _per_board_us(lambda: shared_render.key(first.component), boards, repeat),
        }
    finally:
        board._meta = plain


# -- in-process: one click's server time ---------------------------------------------------


async def clicks(args: argparse.Namespace) -> dict[str, t.Any]:
    """The server time of a click on Board, in this process: receive to the frame, no uvicorn, no socket.

    ``make bench-fastapi`` times the same span around the same application, but
    through a browser and a server process, where a click's time also holds how
    long the idle threads take to wake. Here the clicks come back to back
    (``gap`` 0: the threads stay awake) or ``gap`` seconds apart.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.compare_fastapi.wv.settings")
    os.environ.setdefault("BENCH_CLIENT", "vanilla")
    import django

    django.setup()
    from asgiref.testing import ApplicationCommunicator
    from django.contrib.auth.models import AnonymousUser

    import bench.compare_fastapi.serve as served
    from bench.compare_fastapi.wv.board.live import Board
    from wireview.core.meta import WireviewMeta
    from wireview.core.state import sign_state

    app = served.wireview  # behind the Timed wrapper make bench-fastapi reads
    scope = {
        "type": "websocket",
        "path": "/__wireview__",
        "query_string": b"vsn=99",
        "headers": [(b"host", b"127.0.0.1"), (b"origin", b"http://127.0.0.1")],
        "subprotocols": [],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 80),
    }
    communicator = ApplicationCommunicator(app, scope)

    async def send(payload: dict[str, t.Any]) -> None:
        await communicator.send_input({"type": "websocket.receive", "text": json.dumps(payload)})

    async def until(command: str) -> None:
        while True:
            out = await communicator.receive_output(5)
            if out["type"] == "websocket.send" and json.loads(out["text"])["command"] == command:
                return

    await communicator.send_input({"type": "websocket.connect"})
    assert (await communicator.receive_output(5))["type"] == "websocket.accept"
    state = sign_state(Board(user=AnonymousUser(), wire=WireviewMeta(params={}), id="board"))
    await send({"command": "join", "payload": {"name": "Board", "state": state, "children": {}}})
    await until("joined")

    async def click(action: str, count: int, gap: float) -> list[float]:
        event = {"id": "board", "command": action, "implicit_args": {}, "explicit_args": {}}
        app.records.clear()
        for _ in range(count):
            await send({"command": "user_event", "payload": event})
            await until("render")
            if gap:
                await asyncio.sleep(gap)
        return [ms for _text, ms in app.records]

    out: dict[str, t.Any] = {"clicks": args.clicks, "actions": {}}
    for action in ("increment", "insert"):
        for gap in args.gaps:
            await click(action, args.warmup, gap)
            ms = sorted(await click(action, args.clicks, gap))
            out["actions"].setdefault(action, {})[str(gap)] = {
                "median_ms": statistics.median(ms),
                "p95_ms": ms[int(len(ms) * 0.95)],
            }
    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await communicator.wait(5)
    return out


# -- main -----------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode")
    serve_parser = sub.add_parser("serve", help="(internal) the probed server process")
    serve_parser.add_argument("name", choices=IMPLEMENTATIONS)
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--cprofile", action="store_true")
    for mode, help in (
        ("inproc", "one Board's render by part, worker trips and render threads, in-process"),
        ("plain", "Django's render of Board's template on 1-8 threads; needs only Django (try python 3.14t)"),
    ):
        mode_parser = sub.add_parser(mode, help=help)
        mode_parser.add_argument("--boards", type=int, default=400)
        mode_parser.add_argument("--repeat", type=int, default=7)
        mode_parser.add_argument("--output", type=Path)
        mode_parser.add_argument("--cprofile", action="store_true", help="inproc: also profile render_diff")
    clicks_parser = sub.add_parser("clicks", help="one click's server time on Board, in-process, by action and gap")
    clicks_parser.add_argument("--clicks", type=int, default=300)
    clicks_parser.add_argument("--warmup", type=int, default=50)
    clicks_parser.add_argument("--gaps", type=float, nargs="*", default=[0.0, 0.002])
    clicks_parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--connections", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=3, help="broadcasts before measuring, per round")
    parser.add_argument("--broadcasts", type=int, default=10, help="measured broadcasts per round")
    parser.add_argument("--only", nargs="*", choices=IMPLEMENTATIONS)
    parser.add_argument("--cprofile", action="store_true", help="profile the functions; the times are then inflated")
    parser.add_argument("--output", type=Path, help="default: bench/.data/fanout-profile-<commit>[-dirty].json")
    parser.add_argument("--report", type=Path, help="print the tables of a result written before, and exit")
    parser.add_argument("--chart", type=Path, help="print the Mermaid charts of a result written before, and exit")
    args = parser.parse_args()

    if args.mode == "serve":
        serve(args.name, args.port, args.cprofile)
        return
    if args.mode in ("inproc", "plain", "clicks"):
        if args.mode == "plain":
            result = {"plain": plain(args)}
        elif args.mode == "clicks":
            result = {"environment": environment(), "clicks": asyncio.run(clicks(args))}
        else:
            result = {"environment": environment(), "inproc": asyncio.run(inproc(args))}
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(text + "\n")
        print(text)
        return

    if args.report:
        print(report(json.loads(args.report.read_text())))
        return
    if args.chart:
        print(chart(json.loads(args.chart.read_text())))
        return

    from bench.ws import _raise_fd_limit

    _raise_fd_limit()
    env = environment()
    implementations = asyncio.run(drive(args))
    env["load_average_after"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    result = {
        "environment": env,
        "method": {
            "connections": args.connections,
            "rounds": args.rounds,
            "warmup_broadcasts": args.warmup,
            "measured_broadcasts": args.broadcasts,
            "cprofile": args.cprofile,
        },
        "implementations": implementations,
    }
    suffix = "-cprofile" if args.cprofile else ""
    output = args.output or (
        ROOT / "bench" / ".data" / f"fanout-profile-{env['commit']}{'-dirty' if env['dirty'] else ''}{suffix}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(report(result))
    print(f"\nwrote {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")


if __name__ == "__main__":
    main()
