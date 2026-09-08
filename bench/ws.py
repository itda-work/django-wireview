"""WebSocket benchmark: ASGI server processes + real connections.

Measures per-connection memory of the server processes, join throughput,
event throughput, the render payload size seen on the wire, and the time one
broadcast takes to reach every connection (every subscribed component
re-renders). With ``layer="nats"`` or ``layer="redis"`` several server processes
share one channel layer, so the broadcast crosses processes.

``server`` is "daphne" (default) or "uvicorn". They differ on Windows: daphne
forces asyncio onto the selector loop, whose ``select()`` is capped at 512
sockets per process, while a single-process uvicorn runs on IOCP.

The state sent on join uses the legacy ``Signer().sign(json)`` format so the
same client works against older wireview versions.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import typing as t
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "bench" / ".data" / "logs"


def _uvicorn_args(port: int, ws: str) -> list[str]:
    return [
        "testproj.asgi:application",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--ws",
        ws,
    ]


# server name -> (python module, argv builder). "uvicorn-wsproto" is uvicorn on its
# alternative WebSocket implementation (pip install wsproto).
SERVERS: dict[str, tuple[str, t.Callable[[int], list[str]]]] = {
    "daphne": ("daphne", lambda port: ["-b", "127.0.0.1", "-p", str(port), "testproj.asgi:application"]),
    "uvicorn": ("uvicorn", lambda port: _uvicorn_args(port, "websockets")),
    "uvicorn-wsproto": ("uvicorn", lambda port: _uvicorn_args(port, "wsproto")),
}


def _raise_fd_limit(target: int = 8192) -> None:
    if sys.platform == "win32":
        return  # no RLIMIT_NOFILE; the client runs on IOCP, which is not fd-limited
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = min(target, hard) if hard != resource.RLIM_INFINITY else target
    if soft < wanted:
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tree(pid: int) -> list[psutil.Process]:
    """The process and its descendants. On Windows a venv's python.exe is a launcher
    whose child is the real interpreter, so the server's memory lives one level down."""
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def _rss_kb(pid: int) -> int:
    total = 0
    for proc in _tree(pid):
        try:
            total += proc.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total // 1024


def _wait_for_port(port: int, timeout: float = 30.0, proc: subprocess.Popen | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"process for port {port} exited with {proc.returncode} before listening; see {LOG_DIR}\n"
                + _log_tail(port)
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"nothing listening on port {port} after {timeout:.0f}s")


def _log_tail(port: int, lines: int = 20) -> str:
    logs = sorted(LOG_DIR.glob(f"*-{port}.log"))
    if not logs:
        return "(no log)"
    return "\n".join(logs[-1].read_text(errors="replace").splitlines()[-lines:])


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = os.environ.get("DJANGO_SETTINGS_MODULE", "bench.settings")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests"), env.get("PYTHONPATH", "")])
    return env


def _find_binary(env_var: str, *candidates: str) -> str | None:
    for candidate in (os.environ.get(env_var), *candidates):
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return shutil.which(candidate) or candidate
    return None


def find_nats_server() -> str | None:
    return _find_binary(
        "NATS_SERVER",
        "nats-server",
        str(Path.home() / "go/bin/nats-server"),
        str(Path.home() / "go/bin/nats-server.exe"),
    )


def find_redis_server() -> str | None:
    return _find_binary("REDIS_SERVER", "redis-server", "/opt/homebrew/bin/redis-server", "/usr/local/bin/redis-server")


def start_broker(layer: str) -> subprocess.Popen | None:
    """Start the broker a cross-process layer needs, unless its URL env var already points at one.

    Sets ``NATS_URL`` / ``REDIS_URL`` for the server processes (bench/settings.py reads them).
    Returns the process to stop afterwards, or None when an external broker is used.
    """
    if layer == "nats":
        url_var, scheme, finder, args = "NATS_URL", "nats", find_nats_server, ["-a", "127.0.0.1", "-p"]
    elif layer == "redis":
        # --save "" keeps the throwaway server from dumping dump.rdb into the working directory
        redis_args = ["--bind", "127.0.0.1", "--save", "", "--port"]
        url_var, scheme, finder, args = "REDIS_URL", "redis", find_redis_server, redis_args
    else:
        return None
    if os.environ.get(url_var):
        return None
    binary = finder()
    if binary is None:
        raise RuntimeError(f"layer={layer} needs a {scheme}-server binary on PATH, or {url_var} pointing at one")
    port = _free_port()
    proc = subprocess.Popen([binary, *args, str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_for_port(port, proc=proc)
    os.environ[url_var] = f"{scheme}://127.0.0.1:{port}"
    return proc


def start_server(port: int, server: str = "daphne") -> subprocess.Popen:
    """One daphne or uvicorn process on ``port``. Its output goes to bench/.data/logs.

    The server runs on the same interpreter as the benchmark (``python -m``), never
    on whatever ``daphne``/``uvicorn`` happens to be on PATH. Always a single process
    per port, never ``--workers``: uvicorn's multi-worker mode falls back to the
    selector loop on Windows and inherits the 512-socket cap.
    """
    module, args = SERVERS[server]
    cmd = [sys.executable, "-m", module, *args(port)]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"{server}-{port}.log", "wb")  # noqa: SIM115 (lives as long as the process)
    log.write(f"$ {' '.join(cmd)}\n".encode())
    log.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT / "tests",
        env=_env(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # A cold start on Windows (Defender scanning a fresh venv) can take well over 30 s.
    _wait_for_port(port, timeout=180.0, proc=proc)
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
    try:
        for start in range(0, connections, 50):
            conns += await asyncio.gather(*(open_one(i) for i in range(start, min(start + 50, connections))))
    except Exception as exc:
        # Say how far we got: on Windows a daphne process dies at the select() limit.
        raise RuntimeError(f"joined {len(conns)} of {connections} connections, then: {exc!r}") from exc
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


def _run_client(coro: t.Coroutine[t.Any, t.Any, dict[str, t.Any]]) -> dict[str, t.Any]:
    """Run the client on a loop that can hold thousands of sockets.

    ``daphne`` sits in INSTALLED_APPS, and importing it switches Windows asyncio to the
    selector policy (512 sockets). The client must not inherit that: use IOCP.
    """
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            return runner.run(coro)
    return asyncio.run(coro)


def _stop(*procs: subprocess.Popen | None) -> None:
    """Terminate each process tree, children first (the venv launcher on Windows would
    otherwise leave the real server running)."""
    alive = [p for p in procs if p is not None]
    trees = [_tree(p.pid) for p in alive]
    for tree in trees:
        for proc in reversed(tree):
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
    for tree in trees:
        _, still_alive = psutil.wait_procs(tree, timeout=10)
        for proc in still_alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass


def run(
    connections: int = 500,
    item_counts: tuple[int, ...] = (5, 50),
    processes: int = 1,
    layer: str = "memory",
    server: str = "daphne",
) -> dict[str, t.Any]:
    """``layer`` is "memory" (one process only), "nats" (channels-nats) or "redis" (channels_redis).

    The cross-process layers take any number of server processes; the broker is started here.
    """
    if layer == "memory" and processes > 1:
        raise RuntimeError("the in-memory layer cannot link several processes; use --layer nats or --layer redis")
    if server not in SERVERS:
        raise ValueError(f"unknown server {server!r}; choose from {sorted(SERVERS)}")
    _raise_fd_limit()
    os.environ["BENCH_LAYER"] = layer
    broker = start_broker(layer)
    results: dict[str, t.Any] = {}
    try:
        for items in item_counts:
            ports = [_free_port() for _ in range(processes)]
            procs = [start_server(port, server) for port in ports]  # fresh servers per variant: no memory bleed
            pids = {f"{server}-{i}": proc.pid for i, proc in enumerate(procs)}
            try:
                result = _run_client(measure(ports, pids, connections, items))
                result.update({"layer": layer, "processes": processes, "server": server})
                results[f"items_{items}"] = result
            finally:
                _stop(*procs)
    finally:
        _stop(broker)
    return results
