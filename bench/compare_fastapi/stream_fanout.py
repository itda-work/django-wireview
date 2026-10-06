"""One new item in a feed, reaching every open page: wireview's two ways beside FastAPI (#178).

    uv run --with fastapi python -m bench.compare_fastapi.stream_fanout   (make bench-stream-fanout)
    uv run python -m bench.compare_fastapi.stream_fanout --facts          what the documents quote

The scenario of docs/design/broadcast-patch.md §6-1, on the comparison's own apps:

- ``wireview``: ``NotifiedFeed`` -- a notification, and every connection's
  ``notification()`` renders the item with ``stream_insert`` (what an app did before #178)
- ``wireview-broadcast``: ``Feed`` -- ``Broadcast(...).stream_insert(...)``, rendered and
  serialized once, every connection writing it with its component id
- ``fastapi``: the item's HTML in one JSON text, sent to every client in a loop

Each round starts one uvicorn process per implementation and layer, opens the connections,
posts ``--warmup`` items and then ``--posts`` measured ones from the first connection, and
takes the time until every connection has the new item. The round's value is the median
post; the result is the median of the rounds. wireview runs on each channel layer
(memory, redis, nats, one process: the layer's own server is started here and stopped at
the end); FastAPI needs none and runs once per round. Server CPU (user + system) over the
measured posts is divided by posts × connections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import typing as t
import urllib.request
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import psutil

from bench.ws import LOG_DIR, _free_port, _raise_fd_limit, _stop, _wait_for_port, start_broker

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "bench" / "results" / "3ab5818-stream-fanout.json"
#: The same measurement of the first stage (D1, ``cd6a6ae``): every connection in the patch group.
#: Taken from a worktree of that commit with this file's bench, Broadcast and FastAPI only.
D1_RESULT = ROOT / "bench" / "results" / "cd6a6ae-stream-fanout-d1.json"

#: name -> (uvicorn target, page mode); FastAPI has no page to read a state from
IMPLEMENTATIONS = {
    "wireview": ("bench.compare_fastapi.serve:wireview_feed", "notified"),
    "wireview-broadcast": ("bench.compare_fastapi.serve:wireview_feed", "broadcast"),
    "fastapi": ("bench.compare_fastapi.serve:fastapi_feed", None),
}
LAYERS = ("memory", "redis", "nats")
LABELS = {"wireview": "wireview, 알림", "wireview-broadcast": "wireview, Broadcast", "fastapi": "FastAPI"}
LAYER_LABELS = {"memory": "InMemory", "redis": "Redis", "nats": "NATS"}
#: What docs/design/broadcast-patch.md §1-3 asks of a Broadcast at a thousand connections
TARGET_MS = 40.0


class Server:
    def __init__(self, name: str, layer: str) -> None:
        target, _ = IMPLEMENTATIONS[name]
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
        env["BENCH_LAYER"] = layer
        cmd = [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(self.port)]
        cmd += ["--log-level", "warning", "--ws", "websockets"]
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_DIR / f"stream-fanout-{name}-{layer}-{self.port}.log", "wb")  # noqa: SIM115
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        _wait_for_port(self.port, timeout=60, proc=self.proc)

    def cpu(self) -> float:
        times = psutil.Process(self.proc.pid).cpu_times()
        return times.user + times.system

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *exc: object) -> None:
        _stop(self.proc)


def _is_new_item(message: dict[str, t.Any]) -> bool:
    if message.get("command") == "stream_op":
        return message["payload"]["op"] == "insert"
    return message.get("type") == "item"


async def fanout(server: Server, name: str, connections: int, warmup: int, posts: int) -> dict[str, t.Any]:
    import websockets

    _, mode = IMPLEMENTATIONS[name]
    if mode is not None:
        with urllib.request.urlopen(f"{server.url}/feed/?mode={'notified' if mode == 'notified' else ''}") as page:
            html = page.read().decode()
        component = "NotifiedFeed" if mode == "notified" else "Feed"
        state = re.search(r'data-state="([^"]+)"', html).group(1)  # type: ignore[union-attr]

    async def open_one():
        if mode is None:
            ws = await websockets.connect(f"ws://127.0.0.1:{server.port}/ws", origin=server.url, max_size=None)
            assert json.loads(await asyncio.wait_for(ws.recv(), 60))["type"] == "snapshot"
            return ws
        ws = await websockets.connect(
            f"ws://127.0.0.1:{server.port}/__wireview__?vsn=99", origin=server.url, max_size=None, open_timeout=60
        )
        join = {"name": component, "state": state, "children": {}}
        await ws.send(json.dumps({"command": "join", "payload": join}))
        while json.loads(await asyncio.wait_for(ws.recv(), 60))["command"] != "joined":
            pass
        return ws

    async def new_item(ws) -> int:
        while True:
            text = await asyncio.wait_for(ws.recv(), 120)
            if _is_new_item(json.loads(text)):
                return len(text.encode())

    conns = []
    for start in range(0, connections, 50):
        conns += await asyncio.gather(*(open_one() for _ in range(start, min(start + 50, connections))))
    await asyncio.sleep(0.5)
    if mode is None:
        post = json.dumps({"type": "post"})
    else:
        event = {"id": "feed", "command": "post", "implicit_args": {}, "explicit_args": {}}
        post = json.dumps({"command": "user_event", "payload": event})
    times: list[float] = []
    sizes: list[int] = []
    cpu = 0.0
    for i in range(warmup + posts):
        if i == warmup:
            cpu = server.cpu()
        started = time.perf_counter()
        await conns[0].send(post)
        sizes = await asyncio.gather(*(new_item(ws) for ws in conns))
        if i >= warmup:
            times.append((time.perf_counter() - started) * 1000)
    cpu = server.cpu() - cpu
    await asyncio.gather(*(ws.close() for ws in conns))
    return {
        "fanout_ms": statistics.median(times),
        "posts_ms": times,
        "cpu_per_connection_us": cpu / (posts * connections) * 1e6,
        "frame_bytes": statistics.median(sizes),
    }


def environment() -> dict[str, t.Any]:
    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    packages = ("django", "channels", "channels-redis", "channels-nats", "fastapi", "uvicorn", "websockets")
    versions = {}
    for package in packages:
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = ""
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": run("git", "rev-parse", "--short", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "python": platform.python_version(),
        "packages": versions,
        "redis_server": run("redis-server", "--version"),
        "nats_server": run("nats-server", "--version"),
        "server": "uvicorn, 1 process, --ws websockets, permessage-deflate on (default)",
        "layers": "InMemory (default capacity), channels_redis and channels-nats with capacity 1500 (DEPLOYMENT.md)",
    }


async def measure(args: argparse.Namespace) -> dict[str, t.Any]:
    runs = [(name, layer) for layer in args.layers for name in args.only if name != "fastapi"]
    if "fastapi" in args.only:
        runs.append(("fastapi", "none"))
    out: dict[str, dict[str, list[dict[str, t.Any]]]] = {}
    loads = []
    for round_ in range(args.rounds):
        # Alternate the order so no implementation always runs on the warmer machine
        for name, layer in runs if round_ % 2 == 0 else list(reversed(runs)):
            print(f"round {round_ + 1}/{args.rounds}: {name} on {layer}", flush=True)
            loads.append(os.getloadavg()[0] if hasattr(os, "getloadavg") else None)
            with Server(name, layer) as server:
                result = await fanout(server, name, args.connections, args.warmup, args.posts)
            out.setdefault(name, {}).setdefault(layer, []).append(result)
            print(f"  {result['fanout_ms']:.1f} ms, {result['cpu_per_connection_us']:.1f} µs/connection", flush=True)
    summary = {
        name: {
            layer: {
                "fanout_ms": statistics.median(r["fanout_ms"] for r in rounds),
                "fanout_ms_rounds": [r["fanout_ms"] for r in rounds],
                "cpu_per_connection_us": statistics.median(r["cpu_per_connection_us"] for r in rounds),
                "frame_bytes": rounds[0]["frame_bytes"],
            }
            for layer, rounds in layers.items()
        }
        for name, layers in out.items()
    }
    return {
        "method": {
            "connections": args.connections,
            "rounds": args.rounds,
            "warmup_posts": args.warmup,
            "posts": args.posts,
            "load_average_at_each_run": loads,
        },
        "rounds": out,
        "summary": summary,
    }


# -- what the documents quote -------------------------------------------------------------


def _ms(value: float) -> str:
    return f"{value:,.1f} ms"


def _us(value: float) -> str:
    return f"{value:,.1f} µs"


def facts(result: dict[str, t.Any]) -> dict[str, str]:
    """Every number the documents quote from ``result``, as they write it."""
    out = {"connections": f"{result['method']['connections']:,}", "rounds": str(result["method"]["rounds"])}
    for name, layers in result["summary"].items():
        for layer, x in layers.items():
            key = f"{name}.{layer}"
            out[f"{key}.fanout"] = _ms(x["fanout_ms"])
            out[f"{key}.spread"] = "~".join(
                f"{v:,.1f}" for v in (min(x["fanout_ms_rounds"]), max(x["fanout_ms_rounds"]))
            )
            out[f"{key}.cpu"] = _us(x["cpu_per_connection_us"])
            out[f"{key}.bytes"] = f"{x['frame_bytes']:,.0f} B"
    return out


def table(result: dict[str, t.Any]) -> str:
    """The Markdown table the documents show: fan-out (spread) and CPU per connection, by layer."""
    f = facts(result)
    summary = result["summary"]
    layers = [layer for layer in LAYERS if layer in summary.get("wireview-broadcast", {})]
    head = (
        "| | "
        + " | ".join(LAYER_LABELS[layer] for layer in layers)
        + " | 연결당 CPU ("
        + ", ".join(LAYER_LABELS[layer] for layer in layers)
        + ") |"
    )
    rows = [head, "|---|" + "---:|" * (len(layers) + 1)]
    for name in ("wireview", "wireview-broadcast"):
        cells = [f"{f[f'{name}.{layer}.fanout']} ({f[f'{name}.{layer}.spread']})" for layer in layers]
        cpu = " / ".join(f[f"{name}.{layer}.cpu"] for layer in layers)
        rows.append(f"| {LABELS[name]} | " + " | ".join(cells) + f" | {cpu} |")
    if "fastapi" in summary:
        fast = f"{f['fastapi.none.fanout']} ({f['fastapi.none.spread']})"
        rows.append(
            f"| {LABELS['fastapi']} (레이어 없음) | "
            + " | ".join([fast] * len(layers))
            + f" | {f['fastapi.none.cpu']} |"
        )
    return "\n".join(rows)


def chart(result: dict[str, t.Any]) -> str:
    """Mermaid: the Broadcast's fan-out by layer beside the old path and FastAPI, with the 40 ms target."""
    summary = result["summary"]
    layers = [layer for layer in LAYERS if layer in summary.get("wireview-broadcast", {})]
    names = [f"{LAYER_LABELS[layer]}: 알림" for layer in layers] + [
        f"{LAYER_LABELS[layer]}: Broadcast" for layer in layers
    ]
    values = [summary["wireview"][layer]["fanout_ms"] for layer in layers] + [
        summary["wireview-broadcast"][layer]["fanout_ms"] for layer in layers
    ]
    if "fastapi" in summary:
        names.append("FastAPI")
        values.append(summary["fastapi"]["none"]["fanout_ms"])
    top = max(values) * 1.1
    connections = result["method"]["connections"]
    return "\n".join(
        [
            "```mermaid",
            "xychart-beta horizontal",
            f'    title "스트림 항목 하나가 연결 {connections:,}개에 닿기까지, 프로세스 1개 '
            f'(선: 목표 {TARGET_MS:.0f} ms)"',
            "    x-axis [" + ", ".join(f'"{n}"' for n in names) + "]",
            f'    y-axis "ms" 0 --> {top:.0f}',
            "    bar [" + ", ".join(f"{v:.1f}" for v in values) + "]",
            "    line [" + ", ".join(f"{TARGET_MS:.0f}" for _ in values) + "]",
            "```",
        ]
    )


def stages_facts(d1: dict[str, t.Any], d2: dict[str, t.Any]) -> dict[str, str]:
    """What docs/design/broadcast-patch.md §11 quotes: the Broadcast at each stage, by layer."""
    out = {}
    for stage, result in (("d1", d1), ("d2", d2)):
        for layer, x in result["summary"]["wireview-broadcast"].items():
            out[f"{stage}.{layer}.fanout"] = _ms(x["fanout_ms"])
            out[f"{stage}.{layer}.spread"] = "~".join(
                f"{v:,.1f}" for v in (min(x["fanout_ms_rounds"]), max(x["fanout_ms_rounds"]))
            )
            out[f"{stage}.{layer}.cpu"] = _us(x["cpu_per_connection_us"])
        out[f"{stage}.fastapi.fanout"] = _ms(result["summary"]["fastapi"]["none"]["fanout_ms"])
    return out


def stages_table(d1: dict[str, t.Any], d2: dict[str, t.Any]) -> str:
    f = stages_facts(d1, d2)
    rows = [
        "| | "
        + " | ".join(LAYER_LABELS[layer] for layer in LAYERS)
        + " | 연결당 CPU ("
        + ", ".join(LAYER_LABELS[layer] for layer in LAYERS)
        + ") | FastAPI (같은 회차) |",
        "|---|" + "---:|" * (len(LAYERS) + 2),
    ]
    for stage, label in (("d1", "D1: 연결마다 레이어에서 받는다"), ("d2", "D2: 프로세스가 한 번 받는다")):
        cells = [f"{f[f'{stage}.{layer}.fanout']} ({f[f'{stage}.{layer}.spread']})" for layer in LAYERS]
        cpu = " / ".join(f[f"{stage}.{layer}.cpu"] for layer in LAYERS)
        rows.append(f"| {label} | " + " | ".join(cells) + f" | {cpu} | {f[f'{stage}.fastapi.fanout']} |")
    return "\n".join(rows)


def stages_chart(d1: dict[str, t.Any], d2: dict[str, t.Any]) -> str:
    """Mermaid: the Broadcast's fan-out at D1 and at D2, by layer, with the 40 ms target."""
    names, values = [], []
    for stage, result in (("D1", d1), ("D2", d2)):
        for layer in LAYERS:
            names.append(f"{LAYER_LABELS[layer]}: {stage}")
            values.append(result["summary"]["wireview-broadcast"][layer]["fanout_ms"])
    connections = d2["method"]["connections"]
    return "\n".join(
        [
            "```mermaid",
            "xychart-beta horizontal",
            f'    title "Broadcast 하나가 연결 {connections:,}개에 닿기까지, 단계별 (선: 목표 {TARGET_MS:.0f} ms)"',
            "    x-axis [" + ", ".join(f'"{n}"' for n in names) + "]",
            f'    y-axis "ms" 0 --> {max(values) * 1.1:.0f}',
            "    bar [" + ", ".join(f"{v:.1f}" for v in values) + "]",
            "    line [" + ", ".join(f"{TARGET_MS:.0f}" for _ in values) + "]",
            "```",
        ]
    )


def load(path: Path = RESULT) -> dict[str, t.Any]:
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connections", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--posts", type=int, default=10)
    parser.add_argument("--layers", nargs="*", choices=LAYERS, default=list(LAYERS))
    parser.add_argument("--only", nargs="*", choices=list(IMPLEMENTATIONS), default=list(IMPLEMENTATIONS))
    parser.add_argument("--output", type=Path, help="default: bench/results/<commit>[-dirty]-stream-fanout.json")
    parser.add_argument("--facts", action="store_true", help="print what the documents quote from RESULT")
    args = parser.parse_args()
    if args.facts:
        result, d1 = load(), load(D1_RESULT)
        print(json.dumps({**facts(result), **stages_facts(d1, result)}, indent=2, ensure_ascii=False))
        print(table(result))
        print(chart(result))
        print(stages_table(d1, result))
        print(stages_chart(d1, result))
        return
    _raise_fd_limit()
    brokers = [start_broker(layer) for layer in args.layers]
    try:
        env = environment()
        result = {"environment": env, **asyncio.run(measure(args))}
        env["load_average_after"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    finally:
        _stop(*brokers)
    output = args.output or ROOT / "bench" / "results" / (
        f"{env['commit']}{'-dirty' if env['dirty'] else ''}-stream-fanout.json"
    )
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")


if __name__ == "__main__":
    main()
