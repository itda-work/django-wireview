"""ASGI servers side by side: the WebSocket benchmark of bench/ws.py, several rounds per server.

    uv run --with granian python -m bench.servers --label macos
    uv run --with granian python -m bench.servers --label macos --servers granian,uvicorn-nodeflate --rounds 5
    python -m bench.servers --label win10 --servers daphne --connections 600 --rounds 1   # a server that dies

Each round starts fresh servers, and the order of the servers flips every round so none of
them always runs on the warmer machine. A round that fails (daphne at the Windows select()
limit) is recorded with its error, per component size, instead of ending the run. One JSON per invocation:
bench/results/<sha>[-dirty]-servers-<label>[-<suffix>].json, read by bench/servers_chart.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import typing as t
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVERS = "daphne,uvicorn,uvicorn-nodeflate,granian"


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _cpu() -> str:
    if sys.platform == "darwin":
        return _run("sysctl", "-n", "machdep.cpu.brand_string")
    if sys.platform == "win32":
        name = _run("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name")
        if name:
            return name
    return platform.processor()


def environment(args: argparse.Namespace) -> dict[str, t.Any]:
    import psutil

    versions: dict[str, str | None] = {}
    for package in ("django", "channels", "channels-nats", "channels-redis", "daphne", "uvicorn", "granian"):
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = None
    for package in ("websockets", "uvloop", "httptools", "twisted"):
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = None
    return {
        "date": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "commit": _run("git", "rev-parse", "--short=7", "HEAD") or os.environ.get("BENCH_COMMIT", ""),
        "dirty": bool(_run("git", "status", "--porcelain", "--", ":!bench/results")),
        "machine": args.machine or platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "os_version": _run("sw_vers", "-productVersion") or platform.version(),
        "cpu": _cpu(),
        "cpu_count": os.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
        "packages": versions,
        "client": "websockets, same machine as the servers",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="machine label in the file name (macos, win10, ...)")
    parser.add_argument("--machine", default="", help="free-form machine description stored in the result")
    parser.add_argument("--servers", default=DEFAULT_SERVERS, help=f"comma-separated (default {DEFAULT_SERVERS})")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--connections", type=int, default=2000)
    parser.add_argument("--items", default="5,50", help="component sizes, comma-separated")
    parser.add_argument("--layer", choices=["memory", "nats", "redis"], default="memory")
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--suffix", default="", help="extra file-name part (e.g. daphne-600)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    from bench.run import _setup_django

    _setup_django()
    from django.core.management import call_command

    from bench import ws

    servers = args.servers.split(",")
    unknown = [s for s in servers if s not in ws.SERVERS]
    if unknown:
        parser.error(f"unknown server(s) {unknown}; choose from {sorted(ws.SERVERS)}")
    call_command("migrate", verbosity=0, interactive=False)  # sessions table for the consumer

    env = environment(args)
    name = f"{env['commit']}{'-dirty' if env['dirty'] else ''}-servers-{args.label}"
    out = args.out or ROOT / "bench" / "results" / f"{name}{'-' + args.suffix if args.suffix else ''}.json"
    result: dict[str, t.Any] = {
        "environment": env,
        "settings": {
            "connections": args.connections,
            "items": [int(i) for i in args.items.split(",")],
            "layer": args.layer,
            "processes": args.processes,
            "rounds": args.rounds,
            "argv": {s: ws.SERVERS[s][1](0) for s in servers},
        },
        "servers": {s: {"rounds": []} for s in servers},
    }
    for round_ in range(args.rounds):
        for server in servers if round_ % 2 == 0 else reversed(servers):
            print(f"round {round_ + 1}/{args.rounds}: {server}", flush=True)
            measured: dict[str, t.Any] = {}
            for items in result["settings"]["items"]:  # one size at a time: a size that dies keeps the others
                try:
                    measured |= ws.run(
                        connections=args.connections,
                        item_counts=(items,),
                        processes=args.processes,
                        layer=args.layer,
                        server=server,
                    )
                except Exception as exc:  # noqa: BLE001 (a dying server is a result, not the end of the run)
                    measured[f"items_{items}"] = {"error": f"{type(exc).__name__}: {exc}"[:2000]}
                    print(f"  items {items} failed: {exc!s:.300}", flush=True)
            result["servers"][server]["rounds"].append(measured)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=1))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
