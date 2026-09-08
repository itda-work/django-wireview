"""WebSocket benchmark: a real server + real connections.

Measures per-connection memory of the server processes, join throughput,
event throughput and the render payload size seen on the wire, for either
front: daphne (Channels' own server) or goproxy + ``manage.py wireview_gohost``
(Go terminates the sockets, Python runs the same consumer). The state sent on
join uses the legacy ``Signer().sign(json)`` format so the same client works
against older wireview versions.
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
    raise RuntimeError("server did not start")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = os.environ.get("DJANGO_SETTINGS_MODULE", "bench.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests"), env.get("PYTHONPATH", "")])
    return env


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


def start_uvicorn(port: int) -> subprocess.Popen:
    """uvicorn with the `websockets` implementation, the usual daphne alternative."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "testproj.asgi:application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ws",
            os.environ.get("BENCH_UVICORN_WS", "websockets"),
            "--log-level",
            "warning",
        ],
        cwd=ROOT / "tests",
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return proc


def build_goproxy() -> Path:
    """Build goproxy/ into bench/.data/. Requires a Go toolchain."""
    out = ROOT / "bench" / ".data" / "goproxy"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["go", "build", "-o", str(out), "."], cwd=ROOT / "goproxy")
    return out


def start_go_front(port: int) -> tuple[subprocess.Popen, subprocess.Popen]:
    """Python host (manage.py wireview_gohost) with the Go proxy in front of it."""
    binary = build_goproxy()
    sock = ROOT / "bench" / ".data" / f"gohost-{port}.sock"
    if sock.exists():
        sock.unlink()
    host = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "manage.py"), "wireview_gohost", "--socket", str(sock)],
        cwd=ROOT / "tests",
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while not sock.exists() and time.time() < deadline:
        time.sleep(0.1)
    go = subprocess.Popen(
        [str(binary), "-listen", f"127.0.0.1:{port}", "-backend", str(sock)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_port(port)
    return host, go


async def measure(port: int, pids: dict[str, int], connections: int, items: int) -> dict[str, t.Any]:
    import websockets
    from django.core.signing import Signer

    url = f"ws://127.0.0.1:{port}/__wireview__"

    async def open_one(i: int):
        ws = await websockets.connect(url, max_size=None, open_timeout=30)
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

    async def fire(cid: str, ws) -> int:
        event = {"id": cid, "command": "bump", "implicit_args": {}, "explicit_args": {"index": 1}}
        await ws.send(json.dumps({"command": "user_event", "payload": event}))
        for _ in range(3):
            msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if msg["command"] == "render":
                return len(json.dumps(msg))
        raise RuntimeError("no render after event")

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
    sizes = await asyncio.gather(*(fire(cid, ws) for cid, ws in conns))
    burst_s = time.perf_counter() - t0

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
    }


def _stop(*procs: subprocess.Popen) -> None:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def run(connections: int = 500, item_counts: tuple[int, ...] = (5, 50), front: str = "daphne") -> dict[str, t.Any]:
    """``front`` is "daphne" (Channels' own server), "uvicorn", or "go" (goproxy + wireview_gohost)."""
    _raise_fd_limit()
    results: dict[str, t.Any] = {}
    for items in item_counts:
        port = _free_port()
        # A fresh server per variant so memory numbers do not bleed into each other.
        if front == "go":
            host, go = start_go_front(port)
            procs, pids = (host, go), {"python": host.pid, "go": go.pid}
        elif front == "uvicorn":
            server = start_uvicorn(port)
            procs, pids = (server,), {"python": server.pid}
        else:
            daphne = start_daphne(port)
            procs, pids = (daphne,), {"python": daphne.pid}
        try:
            results[f"items_{items}"] = asyncio.run(measure(port, pids, connections, items))
        finally:
            _stop(*procs)
    return results
