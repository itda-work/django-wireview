"""Measure the same small app built with wireview and with FastAPI, under the same conditions.

    uv run --with fastapi python -m bench.compare_fastapi.measure   (make bench-fastapi)

Three implementations of one page (bench/README.md, "FastAPI와 비교"):

- ``wireview``: the Board component under Django's ASGI handler and Channels' in-memory layer
- ``fastapi-react``: a FastAPI WebSocket endpoint and a React client (Vite production build)
- ``fastapi-vanilla``: the same endpoint and a hand-written client with no framework (Vite build)

Every server is one uvicorn process on the same interpreter with the same flags, started
fresh for each round, and reads the same in-memory store (store.py). The browser parts run
headless Chromium through Playwright on localhost and measure in the page, the same script
for every implementation; the fan-out part opens plain WebSocket clients.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import platform
import random
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

from bench.compare_fastapi import loc
from bench.compare_fastapi.timing import TIMINGS_PATH, kind
from bench.ws import LOG_DIR, _free_port, _raise_fd_limit, _stop, _wait_for_port

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CLIENT = HERE / "client"

IMPLEMENTATIONS = {
    # name -> (uvicorn target, extra environment)
    "wireview": ("bench.compare_fastapi.serve:wireview", {}),
    # The same board declaring Meta.shared_render (#176): not in the README's comparison
    "wireview-shared": ("bench.compare_fastapi.serve:wireview", {"BENCH_SHARED_RENDER": "1"}),
    "fastapi-react": ("bench.compare_fastapi.serve:fastapi", {"BENCH_CLIENT": "react"}),
    "fastapi-vanilla": ("bench.compare_fastapi.serve:fastapi", {"BENCH_CLIENT": "vanilla"}),
}

#: The interactions, by the button they click and the element whose text they wait on.
#: ``timing`` is the action's name in the server timings (wireview's handler, FastAPI's type).
SCENARIOS = {
    "change_value": {"button": "#increment", "watch": "#count", "timing": {"increment"}},
    "insert_front": {"button": "#insert", "watch": "#items li:first-child", "timing": {"insert"}},
    "broadcast": {"button": "#announce", "watch": "#announcement", "timing": {"announce"}},
}

LIVE = '[data-is-live="true"]'
FRAME_MS = 1000 / 60
FEED_SIZE = 50

# Runs in the page from document start: when the list is drawn (the first meaningful
# screen) and when the page can act on a click (live), in ms since navigation start.
FIRST_SCREEN = f"""
(() => {{
  window.__marks = {{}};
  const check = () => {{
    const now = performance.now();
    if (window.__marks.screen === undefined && document.querySelectorAll("#items li").length === {FEED_SIZE}) {{
      window.__marks.screen = now;
    }}
    if (window.__marks.live === undefined && document.querySelector('{LIVE}')) window.__marks.live = now;
    if (window.__marks.screen !== undefined && window.__marks.live !== undefined) observer.disconnect();
  }};
  const observer = new MutationObserver(check);
  observer.observe(document, {{ subtree: true, childList: true, attributes: true, characterData: true }});
  check();
}})();
"""

# Runs in the page from document start: ``__watch(selector)`` resolves when the element's
# text changes, with two times on the clock every page shares (timeOrigin + now):
#
# - dom: when the change is in the DOM
# - paint: when the frame that shows it has been painted (the first task after that frame)
#
# The two differ by design. React writes the DOM as the message arrives and the browser
# paints it in the next frame; wireview's client holds the patch for that same next frame
# (requestAnimationFrame) and writes it there. Which frame paints a change depends on
# whether it happened inside a frame's animation callbacks, so the script marks that span:
# its own requestAnimationFrame callback runs first in every frame (it is always queued
# before any other), and a ResizeObserver, which runs after every animation callback of
# the frame and before its paint, ends it.
WATCH = """
(() => {
  const clock = () => performance.timeOrigin + performance.now();
  let inFrame = false;
  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;left:-10px;top:0;width:1px;height:1px";
  new ResizeObserver(() => { inFrame = false; }).observe(probe);
  const frame = () => requestAnimationFrame(() => {
    inFrame = true;
    probe.style.width = probe.style.width === "1px" ? "2px" : "1px";
    frame();
  });
  addEventListener("DOMContentLoaded", () => { document.documentElement.append(probe); frame(); });
  window.__clock = clock;
  window.__watch = (selector) => new Promise((resolve) => {
    const before = document.querySelector(selector).textContent;
    const observer = new MutationObserver(() => {
      if (document.querySelector(selector)?.textContent === before) return;
      observer.disconnect();
      const dom = clock();
      const painted = () => setTimeout(() => resolve({ dom, paint: clock() }));
      if (inFrame) painted(); else requestAnimationFrame(painted);
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  });
})();
"""

# Click and wait until the watched element's text changes on this page. The click waits
# ``delay`` ms first: Playwright's calls reach the page at the same point of a frame each
# time, and a user's clicks land anywhere in it.
CLICK = """
async ([button, watch, delay]) => {
  await new Promise((resolve) => setTimeout(resolve, delay));
  const seen = window.__watch(watch);
  const at = window.__clock();
  document.querySelector(button).click();
  const { dom, paint } = await seen;
  return { dom: dom - at, paint: paint - at };
}
"""

# The broadcast: the other page arms a watcher, then the clicking page clicks and says when.
ARM = "(watch) => { window.__seen = window.__watch(watch); }"
CLICK_AT = """
async ([button, delay]) => {
  await new Promise((resolve) => setTimeout(resolve, delay));
  const at = window.__clock();
  document.querySelector(button).click();
  return at;
}
"""

#: Every page load starts from another site, as a visitor following a link does. Chromium
#: then gives the page a new renderer process whatever its headers say. Loaded from
#: about:blank instead, only a response with Cross-Origin-Opener-Policy (Django's
#: SecurityMiddleware sends one by default, FastAPI sends none) pays for that switch,
#: about 35 ms on this machine, and the comparison would be of headers.
ELSEWHERE = "http://elsewhere.test/"


# -- servers -----------------------------------------------------------------------------


class Server:
    def __init__(self, name: str) -> None:
        target, extra = IMPLEMENTATIONS[name]
        self.name = name
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")])
        env.update(extra)
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log-level",
            "warning",
            "--ws",
            "websockets",
        ]
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_DIR / f"compare-{name}-{self.port}.log", "wb")  # noqa: SIM115 (lives as long as the process)
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        _wait_for_port(self.port, timeout=60, proc=self.proc)

    def timings(self) -> list[tuple[str, float]]:
        """The server's processing times since the last call, by action."""
        with urllib.request.urlopen(self.url + TIMINGS_PATH) as response:
            return [(kind(text), ms) for text, ms in json.loads(response.read())]

    def stop(self) -> None:
        _stop(self.proc)

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# -- statistics --------------------------------------------------------------------------


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100)."""
    ordered = sorted(values)
    rank = max(1, round(q / 100 * len(ordered) + 0.5 - 1e-9))
    return ordered[min(rank, len(ordered)) - 1]


def summary(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "p95": percentile(values, 95), "n": len(values)}


def across(rounds: list[dict[str, float]]) -> dict[str, t.Any]:
    """Each round's median and p95, and their median over rounds with the spread of the medians."""
    medians = [r["median"] for r in rounds]
    return {
        "median": statistics.median(medians),
        "p95": statistics.median(r["p95"] for r in rounds),
        "median_min": min(medians),
        "median_max": max(medians),
        "rounds": rounds,
    }


def gzipped(data: bytes) -> int:
    """gzip -6 with no name and no time in the header: the same bytes on every run."""
    return len(gzip.compress(data, compresslevel=6, mtime=0))


# -- browser -----------------------------------------------------------------------------


class Frames:
    """WebSocket payload bytes a page sent and received, as Chromium reports them.

    The payload only, for every implementation: no WebSocket frame header and no
    permessage-deflate (uvicorn negotiates it for both stacks; DevTools reports the
    inflated payload).
    """

    def __init__(self) -> None:
        self.sent = self.received = self.sent_frames = self.received_frames = 0

    def reset(self) -> None:
        self.sent = self.received = self.sent_frames = self.received_frames = 0

    async def attach(self, context, page) -> None:
        cdp = await context.new_cdp_session(page)
        cdp.on("Network.webSocketFrameSent", self._sent)
        cdp.on("Network.webSocketFrameReceived", self._received)
        await cdp.send("Network.enable")

    def _sent(self, event: dict) -> None:
        self.sent += len(event["response"]["payloadData"].encode())
        self.sent_frames += 1

    def _received(self, event: dict) -> None:
        self.received += len(event["response"]["payloadData"].encode())
        self.received_frames += 1


async def _settle(page) -> None:
    await page.wait_for_selector(LIVE)
    # wireview tells the page "joined" just after the join's render; let it pass so it is
    # not counted as the reply to the first click
    await asyncio.sleep(0.3)


async def first_load(browser, server: Server, loads: int, warmup: int) -> dict[str, t.Any]:
    """Cold loads, each in a new browser context: what the page downloads and when it is usable."""
    screen, paint, live, downloads = [], [], [], []
    for i in range(warmup + loads):
        context = await browser.new_context()
        page = await context.new_page()
        await page.route(ELSEWHERE, lambda route: route.fulfill(body="<p>elsewhere</p>", content_type="text/html"))
        await page.goto(ELSEWHERE)
        await page.add_init_script(FIRST_SCREEN)
        frames = Frames()
        await frames.attach(context, page)
        bodies: list[tuple[str, str, bytes]] = []
        pending = []

        async def keep(response) -> None:
            ours = response.url.startswith(server.url)
            if ours and response.request.resource_type in ("document", "script", "stylesheet"):
                bodies.append((response.request.resource_type, response.url, await response.body()))

        page.on("response", lambda response: pending.append(asyncio.ensure_future(keep(response))))
        await page.goto(server.url + "/")
        await page.wait_for_selector(LIVE)
        await page.wait_for_function("window.__marks.screen !== undefined && window.__marks.live !== undefined")
        await asyncio.sleep(0.3)  # the frames after the page went live (wireview's "joined")
        await asyncio.gather(*pending)
        marks = await page.evaluate("window.__marks")
        marks["paint"] = await page.evaluate(
            "performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null"
        )
        await context.close()
        if i < warmup:
            continue
        screen.append(marks["screen"])
        paint.append(marks["paint"])
        live.append(marks["live"])
        html = b"".join(body for kind_, _, body in bodies if kind_ == "document")
        scripts = [body for kind_, _, body in bodies if kind_ != "document"]
        downloads.append(
            {
                "html_bytes": len(html),
                "html_gzip_bytes": gzipped(html),
                "js_bytes": sum(map(len, scripts)),
                "js_gzip_bytes": sum(map(gzipped, scripts)),
                "files": sorted(url.removeprefix(server.url) for _, url, _ in bodies),
                "ws_sent_bytes": frames.sent,
                "ws_received_bytes": frames.received,
            }
        )
    # The bytes are the same on every load but for a fresh CSRF-free page they must be: say so if not
    first = downloads[0]
    for other in downloads[1:]:
        for key in ("js_bytes", "files"):
            if other[key] != first[key]:
                raise RuntimeError(f"{server.name}: {key} differs between loads: {first[key]} != {other[key]}")
    return {
        "first_screen_ms": summary(screen),
        "first_contentful_paint_ms": summary(paint),
        "interactive_ms": summary(live),
        "download": {
            key: statistics.median(d[key] for d in downloads)
            for key in (
                "html_bytes",
                "html_gzip_bytes",
                "js_bytes",
                "js_gzip_bytes",
                "ws_sent_bytes",
                "ws_received_bytes",
            )
        }
        | {"files": first["files"]},
    }


async def interactions(browser, server: Server, clicks: int, warmup: int) -> dict[str, t.Any]:
    """Each scenario on one page (the broadcast: one page clicks, a second page in another context watches)."""
    results: dict[str, t.Any] = {}
    contexts = [await browser.new_context() for _ in range(2)]
    pages = [await context.new_page() for context in contexts]
    frames = [Frames(), Frames()]
    for context, page, counter in zip(contexts, pages, frames, strict=True):
        await counter.attach(context, page)
        await page.add_init_script(WATCH)
        await page.goto(server.url + "/")
        await _settle(page)
    clicker, watcher = pages
    try:
        for name, scenario in SCENARIOS.items():
            dom: list[float] = []
            paint: list[float] = []
            # Where in a frame each click lands: the same sequence for every implementation and round
            phase = random.Random(name)
            for i in range(warmup + clicks):
                if i == warmup:
                    await asyncio.sleep(0.1)
                    server.timings()  # drop the warm-up's
                    for counter in frames:
                        counter.reset()
                delay = phase.uniform(0, FRAME_MS)
                if name == "broadcast":
                    await watcher.evaluate(ARM, scenario["watch"])
                    started = await clicker.evaluate(CLICK_AT, [scenario["button"], delay])
                    seen = await watcher.evaluate("window.__seen")
                    ms = {"dom": seen["dom"] - started, "paint": seen["paint"] - started}
                    # the clicking page updates too; wait for it so the next click starts quiet
                    text = await watcher.text_content(scenario["watch"])
                    await clicker.wait_for_function(
                        "([watch, text]) => document.querySelector(watch).textContent === text",
                        arg=[scenario["watch"], text],
                    )
                else:
                    ms = await clicker.evaluate(CLICK, [scenario["button"], scenario["watch"], delay])
                if i >= warmup:
                    dom.append(ms["dom"])
                    paint.append(ms["paint"])
            await asyncio.sleep(0.3)  # trailing frames belong to this scenario
            server_ms = [ms for action, ms in server.timings() if action in scenario["timing"]]
            results[name] = {
                "dom_ms": summary(dom),
                "paint_ms": summary(paint),
                "server_ms": summary(server_ms) if server_ms and name != "broadcast" else None,
                "clicker_sent_bytes": frames[0].sent / clicks,
                "clicker_received_bytes": frames[0].received / clicks,
                "watcher_received_bytes": frames[1].received / clicks,
            }
            for counter in frames:
                counter.reset()
    finally:
        for context in contexts:
            await context.close()
    return results


# -- fan-out -----------------------------------------------------------------------------


async def fanout(server: Server, connections: int) -> dict[str, float]:
    """One broadcast reaching ``connections`` WebSocket clients, until each has its update."""
    import websockets

    origin = server.url
    wireview = server.name == "wireview"
    if wireview:
        with urllib.request.urlopen(server.url + "/") as response:
            state = re.search(r'data-state="([^"]+)"', response.read().decode()).group(1)

    async def open_one():
        if wireview:
            ws = await websockets.connect(
                f"ws://127.0.0.1:{server.port}/__wireview__?vsn=99", origin=origin, max_size=None, open_timeout=60
            )
            await ws.send(json.dumps({"command": "join", "payload": {"name": "Board", "state": state, "children": {}}}))
            while json.loads(await asyncio.wait_for(ws.recv(), 60))["command"] != "joined":
                pass
        else:
            ws = await websockets.connect(f"ws://127.0.0.1:{server.port}/ws", origin=origin, max_size=None)
            assert json.loads(await asyncio.wait_for(ws.recv(), 60))["type"] == "snapshot"
        return ws

    async def update(ws) -> int:
        while True:
            text = await asyncio.wait_for(ws.recv(), 120)
            message = json.loads(text)
            if message.get("command") == "render" or message.get("type") == "announcement":
                return len(text.encode())

    conns = []
    for start in range(0, connections, 50):
        conns += await asyncio.gather(*(open_one() for _ in range(start, min(start + 50, connections))))
    await asyncio.sleep(0.5)
    if wireview:
        event = {"id": "board", "command": "announce", "implicit_args": {}, "explicit_args": {}}
        message = json.dumps({"command": "user_event", "payload": event})
    else:
        message = json.dumps({"type": "announce"})
    started = time.perf_counter()
    await conns[0].send(message)
    sizes = await asyncio.gather(*(update(ws) for ws in conns))
    elapsed = (time.perf_counter() - started) * 1000
    await asyncio.gather(*(ws.close() for ws in conns))
    return {"broadcast_ms": elapsed, "received_bytes": statistics.median(sizes)}


# -- environment -------------------------------------------------------------------------


def _run(*cmd: str, cwd: Path = ROOT) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _npm_version(package: str) -> str:
    path = CLIENT / "node_modules" / package / "package.json"
    return json.loads(path.read_text())["version"] if path.exists() else ""


def environment(browser_version: str) -> dict[str, t.Any]:
    packages = ("django", "channels", "django-wireview", "fastapi", "starlette", "uvicorn", "websockets", "playwright")
    versions = {}
    for package in packages:
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = ""
    for package in ("uvloop", "httptools"):
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = None
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _run("git", "rev-parse", "--short", "HEAD"),
        "dirty": bool(_run("git", "status", "--porcelain")),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "os_version": _run("sw_vers", "-productVersion") or platform.version(),
        "cpu": _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "cpu_count": os.cpu_count(),
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "python": platform.python_version(),
        "packages": versions,
        "browser": f"chromium {browser_version} (headless, Playwright)",
        "node": _run("node", "--version"),
        "client_packages": {
            name: _npm_version(name) for name in ("react", "react-dom", "vite", "@vitejs/plugin-react")
        },
        "server": "uvicorn, 1 process, --ws websockets, --log-level warning, permessage-deflate on (default)",
        "wireview_layer": "channels.layers.InMemoryChannelLayer",
        "debug": False,
    }


# -- main --------------------------------------------------------------------------------


async def measure(args: argparse.Namespace) -> dict[str, t.Any]:
    from playwright.async_api import async_playwright

    names = args.only or list(IMPLEMENTATIONS)
    out: dict[str, t.Any] = {name: {"rounds": []} for name in names}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        env = environment(browser.version)
        try:
            for round_ in range(args.rounds):
                # Alternate the order so neither stack always runs on the warmer machine
                for name in names if round_ % 2 == 0 else reversed(names):
                    print(f"round {round_ + 1}/{args.rounds}: {name}", flush=True)
                    with Server(name) as server:
                        result = {
                            "first_load": await first_load(browser, server, args.loads, warmup=3),
                            "interactions": await interactions(browser, server, args.clicks, warmup=args.warmup),
                        }
                    with Server(name) as server:
                        result["fanout"] = await fanout(server, args.connections)
                    out[name]["rounds"].append(result)
        finally:
            await browser.close()
    env["load_average_after"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    for name in names:
        out[name]["summary"] = summarize(out[name]["rounds"])
    return {
        "environment": env,
        "method": {
            "rounds": args.rounds,
            "clicks_per_scenario": args.clicks,
            "warmup_clicks": args.warmup,
            "loads_per_round": args.loads,
            "warmup_loads": 3,
            "fanout_connections": args.connections,
            "bytes": "WebSocket payload as Chromium reports it (no frame header, before permessage-deflate)",
            "gzip": "gzip -6 of each downloaded file, computed by the benchmark (neither server compresses)",
        },
        "implementations": out,
        "loc": loc.count(),
    }


def summarize(rounds: list[dict[str, t.Any]]) -> dict[str, t.Any]:
    first = rounds[0]
    result: dict[str, t.Any] = {
        "first_screen_ms": across([r["first_load"]["first_screen_ms"] for r in rounds]),
        "first_contentful_paint_ms": across([r["first_load"]["first_contentful_paint_ms"] for r in rounds]),
        "interactive_ms": across([r["first_load"]["interactive_ms"] for r in rounds]),
        "download": first["first_load"]["download"],
        "fanout_ms": {
            "median": statistics.median(r["fanout"]["broadcast_ms"] for r in rounds),
            "median_min": min(r["fanout"]["broadcast_ms"] for r in rounds),
            "median_max": max(r["fanout"]["broadcast_ms"] for r in rounds),
        },
        "fanout_received_bytes": first["fanout"]["received_bytes"],
    }
    for name in SCENARIOS:
        per_round = [r["interactions"][name] for r in rounds]
        result[name] = {
            "dom_ms": across([r["dom_ms"] for r in per_round]),
            "paint_ms": across([r["paint_ms"] for r in per_round]),
            "server_ms": across([r["server_ms"] for r in per_round]) if per_round[0]["server_ms"] else None,
            "clicker_sent_bytes": statistics.median(r["clicker_sent_bytes"] for r in per_round),
            "clicker_received_bytes": statistics.median(r["clicker_received_bytes"] for r in per_round),
            "watcher_received_bytes": statistics.median(r["watcher_received_bytes"] for r in per_round),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--clicks", type=int, default=200, help="measured clicks per scenario per round")
    parser.add_argument("--warmup", type=int, default=20, help="clicks before measuring, per scenario")
    parser.add_argument("--loads", type=int, default=20, help="measured cold page loads per round")
    parser.add_argument("--connections", type=int, default=1000, help="connections the fan-out broadcast reaches")
    parser.add_argument("--only", nargs="*", choices=list(IMPLEMENTATIONS))
    parser.add_argument("--output", type=Path, help="default: bench/results/<commit>[-dirty]-fastapi.json")
    args = parser.parse_args()
    if not (CLIENT / "dist" / "react" / "index.html").exists():
        sys.exit("build the clients first: make bench-fastapi (or npm --prefix bench/compare_fastapi/client run build)")
    _raise_fd_limit()
    result = asyncio.run(measure(args))
    env = result["environment"]
    output = (
        args.output or ROOT / "bench" / "results" / f"{env['commit']}{'-dirty' if env['dirty'] else ''}-fastapi.json"
    )
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")


if __name__ == "__main__":
    main()
