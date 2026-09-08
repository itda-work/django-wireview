"""WebSocket benchmark: daphne processes + real connections.

Measures per-connection memory of the server processes, join throughput,
event throughput, the render payload size seen on the wire, and the time one
broadcast takes to reach every connection (every subscribed component
re-renders). With ``layer="nats"`` several daphne processes share the
channels-nats layer, so the broadcast crosses processes.

The state sent on join uses the legacy ``Signer().sign(json)`` format so the
same client works against older wireview versions.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import time
import typing as t
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _raise_fd_limit(target: int = 8192) -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = min(target, hard) if hard != resource.RLIM_INFINITY else target
    if soft < wanted:
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rss_kb(pid: int) -> int:
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).strip())


def _wait_for_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"nothing listening on port {port}")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = os.environ.get("DJANGO_SETTINGS_MODULE", "bench.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests"), env.get("PYTHONPATH", "")])
    return env


def find_nats_server() -> str | None:
    for candidate in (
        os.environ.get("NATS_SERVER"),
        shutil.which("nats-server"),
        str(Path.home() / "go/bin/nats-server"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def start_nats() -> subprocess.Popen | None:
    """Start a nats-server unless NATS_URL points at one. Sets NATS_URL for the daphne processes."""
    if os.environ.get("NATS_URL"):
        return None
    binary = find_nats_server()
    if binary is None:
        raise RuntimeError("layer=nats needs a nats-server binary (NATS_SERVER, PATH, or ~/go/bin) or NATS_URL")
    port = _free_port()
    proc = subprocess.Popen(
        [binary, "-a", "127.0.0.1", "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    _wait_for_port(port)
    os.environ["NATS_URL"] = f"nats://127.0.0.1:{port}"
    return proc


def start_daphne(port: int) -> subprocess.Popen:
    daphne = shutil.which("daphne")
    cmd = [daphne] if daphne else [sys.executable, "-m", "daphne"]
    proc = subprocess.Popen(
        cmd + ["-b", "127.0.0.1", "-p", str(port), "testproj.asgi:application"],
        cwd=ROOT / "tests",
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return proc


async def measure(ports: list[int], pids: dict[str, int], connections: int, items: int) -> dict[str, t.Any]:
    import websockets
    from django.core.signing import Signer

    async def open_one(i: int):
        port = ports[i % len(ports)]
        ws = await websockets.connect(f"ws://127.0.0.1:{port}/__wireview__", max_size=None, open_timeout=30)
        state = {
            "id": f"bench-{i}",
            "items": [{"name": f"item {k}", "qty": k, "done": k % 3 == 0} for k in range(items)],
        }
        signed = Signer().sign(json.dumps(state))
        await ws.send(
            json.dumps({"command": "join", "payload": {"name": "BenchList", "state": signed, "children": {}}})
        )
        msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
        assert msg["command"] == "render", msg
        return f"bench-{i}", ws

    async def next_render(ws, timeout: float = 60) -> int:
        for _ in range(3):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg["command"] == "render":
                return len(json.dumps(msg))
        raise RuntimeError("no render received")

    async def fire(cid: str, ws, command: str, **explicit) -> int:
        event = {"id": cid, "command": command, "implicit_args": {}, "explicit_args": explicit}
        await ws.send(json.dumps({"command": "user_event", "payload": event}))
        return await next_render(ws)

    await asyncio.sleep(0.5)
    baseline = {name: _rss_kb(pid) for name, pid in pids.items()}
    conns = []
    t0 = time.perf_counter()
    for start in range(0, connections, 50):
        conns += await asyncio.gather(*(open_one(i) for i in range(start, min(start + 50, connections))))
    join_s = time.perf_counter() - t0
    await asyncio.sleep(1.0)
    joined = {name: _rss_kb(pid) for name, pid in pids.items()}

    t0 = time.perf_counter()
    sizes = await asyncio.gather(*(fire(cid, ws, "bump", index=1) for cid, ws in conns))
    burst_s = time.perf_counter() - t0

    # One broadcast: every subscribed component in every process re-renders.
    await asyncio.sleep(0.5)  # group subscriptions settle
    cid0, ws0 = conns[0]
    t0 = time.perf_counter()
    await ws0.send(
        json.dumps(
            {
                "command": "user_event",
                "payload": {"id": cid0, "command": "shout", "implicit_args": {}, "explicit_args": {}},
            }
        )
    )
    await asyncio.gather(*(next_render(ws) for _, ws in conns))
    broadcast_s = time.perf_counter() - t0

    await asyncio.gather(*(ws.close() for _, ws in conns))
    per_process = {name: (joined[name] - baseline[name]) / connections for name in pids}
    return {
        "connections": connections,
        "items": items,
        "per_connection_kb": sum(per_process.values()),
        "per_connection_kb_by_process": per_process,
        "joins_per_s": connections / join_s,
        "events_per_s": connections / burst_s,
        "render_bytes": sizes[0],
        "broadcast_ms": broadcast_s * 1000,
        "broadcast_renders_per_s": connections / broadcast_s,
    }


def _stop(*procs: subprocess.Popen | None) -> None:
    alive = [p for p in procs if p is not None]
    for proc in alive:
        proc.terminate()
    for proc in alive:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def run(
    connections: int = 500,
    item_counts: tuple[int, ...] = (5, 50),
    processes: int = 1,
    layer: str = "memory",
) -> dict[str, t.Any]:
    """``layer`` is "memory" (one process only) or "nats" (channels-nats, any number of daphne processes)."""
    if layer == "memory" and processes > 1:
        raise RuntimeError("the in-memory layer cannot link several processes; use --layer nats")
    _raise_fd_limit()
    os.environ["BENCH_LAYER"] = layer
    nats = start_nats() if layer == "nats" else None
    results: dict[str, t.Any] = {}
    try:
        for items in item_counts:
            ports = [_free_port() for _ in range(processes)]
            procs = [start_daphne(port) for port in ports]  # fresh servers per variant: no memory bleed
            pids = {f"daphne-{i}": proc.pid for i, proc in enumerate(procs)}
            try:
                result = asyncio.run(measure(ports, pids, connections, items))
                result.update({"layer": layer, "processes": processes})
                results[f"items_{items}"] = result
            finally:
                _stop(*procs)
    finally:
        _stop(nats)
    return results
