"""What each ASGI server does to open WebSockets on SIGTERM (#191). POSIX only.

    uv run --with granian==2.8.4 python -m bench.servers_shutdown --label macos

Every server gets ``--connections`` joined ``BenchLeaving`` components, whose ``leaving()`` writes
"start", sleeps ``BENCH_LEAVING_SECONDS`` and writes "done". Then the server gets SIGTERM. Recorded
per server: the close code each client saw (None: the TCP connection ended without a close frame),
how long until every server process was gone, and how many leaving() calls started and finished.
A deployment that relies on leaving() (presence, a final write) needs started == finished.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import typing as t
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
#: The extra flags each server gets for a graceful stop, beyond bench/ws.py's
GRACE = {
    "daphne": [],
    "uvicorn-nodeflate": ["--timeout-graceful-shutdown", "10"],
    "granian": ["--workers-kill-timeout", "10"],
}


def _state(i: int) -> str:
    from django.contrib.auth.models import AnonymousUser

    from bench.benchapp.live import BenchLeaving
    from wireview.core.meta import WireviewMeta
    from wireview.core.state import sign_state

    return sign_state(BenchLeaving(user=AnonymousUser(), wire=WireviewMeta(params={}), id=f"leaving-{i}"))


async def probe(server: str, connections: int, seconds: float) -> dict[str, t.Any]:
    import websockets

    from bench import ws

    log = Path(tempfile.mkstemp(prefix=f"leaving-{server}-", suffix=".log")[1])
    os.environ["BENCH_LEAVING_LOG"] = str(log)
    os.environ["BENCH_LEAVING_SECONDS"] = str(seconds)
    module, args = ws.SERVERS[server]
    original = ws.SERVERS[server]
    ws.SERVERS[server] = (module, lambda port: [*args(port)[:-1], *GRACE[server], args(port)[-1]])
    port = ws._free_port()
    try:
        proc = ws.start_server(port, server)
    finally:
        ws.SERVERS[server] = original
    tree = ws._tree(proc.pid)
    conns = []
    for i in range(connections):
        sock = await websockets.connect(f"ws://127.0.0.1:{port}/__wireview__")
        await sock.send(
            json.dumps({"command": "join", "payload": {"name": "BenchLeaving", "state": _state(i), "children": {}}})
        )
        msg = json.loads(await asyncio.wait_for(sock.recv(), 30))
        assert msg["command"] == "render", msg
        conns.append(sock)

    async def closed(sock) -> int | None:
        try:
            while True:
                await asyncio.wait_for(sock.recv(), 30)
        except websockets.ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else None

    t0 = time.perf_counter()
    proc.send_signal(signal.SIGTERM)
    codes = asyncio.gather(*(closed(s) for s in conns))
    while any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in tree) and time.perf_counter() - t0 < 30:
        await asyncio.sleep(0.02)
    exited = time.perf_counter() - t0
    close_codes = await codes
    ws._stop(proc)
    lines = log.read_text().splitlines()
    log.unlink()
    return {
        "connections": connections,
        "leaving_seconds": seconds,
        "flags": GRACE[server],
        "close_codes": sorted({str(c) for c in close_codes}),
        "exit_seconds": round(exited, 2),
        "leaving_started": sum(line.startswith("start ") for line in lines),
        "leaving_finished": sum(line.startswith("done ") for line in lines),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--servers", default=",".join(GRACE))
    parser.add_argument("--connections", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        parser.error("SIGTERM is POSIX; Windows stops a process without telling it")

    from bench.run import _setup_django

    _setup_django()
    from django.core.management import call_command

    from bench.servers import environment

    call_command("migrate", verbosity=0, interactive=False)
    args.machine = ""
    env = environment(args)
    out = args.out or ROOT / "bench" / "results" / (
        f"{env['commit']}{'-dirty' if env['dirty'] else ''}-servers-shutdown-{args.label}.json"
    )
    result: dict[str, t.Any] = {"environment": env, "servers": {}}
    for server in args.servers.split(","):
        print(server, flush=True)
        result["servers"][server] = asyncio.run(probe(server, args.connections, args.seconds))
        print(" ", result["servers"][server], flush=True)
    out.write_text(json.dumps(result, indent=1))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
