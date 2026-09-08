"""Run every benchmark and write a JSON result.

    uv run python -m bench.run                 # payload + WebSocket, 500 connections
    uv run python -m bench.run --skip-ws       # in-process only (no daphne)
    uv run python -m bench.run --out bench/results/current.json

Compare two results with ``python -m bench.compare a.json b.json`` or run
``make bench-compare BASE=<ref>`` to benchmark a past commit side by side.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def _setup_django() -> None:
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bench.settings")
    os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
    import django

    django.setup()


def _print(results: dict) -> None:
    meta = results["meta"]
    flag = " (dirty)" if meta.get("dirty") else ""
    print(
        f"wireview @ {meta['sha']}{flag}  {meta['date']}  python {meta['python']}  "
        f"django {meta['django']}  {meta.get('platform', '')}"
    )
    print()
    print(f"{'payload bytes':<32} {'bytes':>10}")
    for key, value in results["payload_bytes"].items():
        print(f"  {key:<30} {value:>10,}")
    print()
    print(f"{'timing (per event)':<32} {'ms':>10}")
    for key, value in results["timing"].items():
        print(f"  {key:<30} {value:>10.3f}")
    print()
    print(f"{'memory':<32} {'KB':>10}")
    for key, value in results["memory"].items():
        print(f"  {key:<30} {value:>10.1f}")
    if results.get("ws"):
        first = next(iter(results["ws"].values()))
        print()
        print(
            f"websocket: {first.get('server', 'daphne')}, {first.get('layer', 'memory')} layer, "
            f"{first.get('processes', 1)} process(es)"
        )
        print(f"{'':<28} {'conns':>6} {'KB/conn':>9} {'joins/s':>9} {'events/s':>9} {'render B':>9} {'broadcast':>10}")
        for key, ws in results["ws"].items():
            print(
                f"  {key:<26} {ws['connections']:>6} {ws['per_connection_kb']:>9.1f} "
                f"{ws['joins_per_s']:>9.0f} {ws['events_per_s']:>9.0f} {ws['render_bytes']:>9,} "
                f"{ws.get('broadcast_ms', 0):>8.0f}ms"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", type=Path, default=None, help="JSON output (default: bench/results/<sha>[-dirty].json)"
    )
    parser.add_argument("--items", type=int, default=50, help="list size for the in-process benchmark")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--connections", type=int, default=500)
    parser.add_argument("--skip-ws", action="store_true", help="skip the daphne/WebSocket benchmark")
    parser.add_argument("--processes", type=int, default=1, help="daphne processes (needs --layer nats when > 1)")
    parser.add_argument("--layer", choices=["memory", "nats"], default="memory", help="channel layer for the servers")
    parser.add_argument(
        "--server",
        choices=["daphne", "uvicorn", "uvicorn-wsproto"],
        default="daphne",
        help="ASGI server to benchmark (uvicorn-wsproto: uvicorn with the wsproto WebSocket implementation)",
    )
    args = parser.parse_args(argv)

    _setup_django()
    import django

    from bench import payload

    sha = _git("rev-parse", "--short=7", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", ":!bench/results"))
    results = {
        "meta": {
            "sha": sha,
            "dirty": dirty,
            "ref": _git("describe", "--all", "--always"),
            "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "django": django.get_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "items": args.items,
            "connections": None if args.skip_ws else args.connections,
            "layer": None if args.skip_ws else args.layer,
            "processes": None if args.skip_ws else args.processes,
            "server": None if args.skip_ws else args.server,
        }
    }
    if args.out is None:
        args.out = ROOT / "bench" / "results" / f"{sha}{'-dirty' if dirty else ''}.json"
    results.update(asyncio.run(payload.run(items=args.items, iterations=args.iterations)))

    if not args.skip_ws:
        from django.core.management import call_command

        from bench import ws

        call_command("migrate", verbosity=0, interactive=False)  # sessions table for the consumer
        results["ws"] = ws.run(
            connections=args.connections, processes=args.processes, layer=args.layer, server=args.server
        )

    _print(results)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=1))
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
