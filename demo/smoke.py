"""Smoke test of a running demo, in a real browser. Not part of the test suite: it needs the stack up.

    uv run python demo/smoke.py http://127.0.0.1:8080 --out /tmp/demo-smoke
    uv run python demo/smoke.py https://wireview-demo.itda.work --out /tmp/demo-smoke

Over plain http it sends ``X-Forwarded-Proto: https`` with every request, the header Cloudflare
sends, so the stack runs with its production settings (HTTPS redirect, secure cookies; Chromium
keeps a secure cookie on localhost). It checks the list, every example's first page, a page
going live over the WebSocket, #213's scenes that need neither a seed of their own nor a sign-in
(played at full speed, ``paced=False``), chat between two browsers (one anonymous level, one
with guest accounts), and the socket limits. Screenshots go to ``--out``. Exits 1 on the first
failure.

Over plain http it also names itself a visitor of its own with ``CF-Connecting-IP`` (Caddy takes
it from a private address, as it takes Cloudflare's), so runs one after another do not spend each
other's per-visitor limits. Through Cloudflare the header is Cloudflare's.

demo/abuse.py checks that what a script would send is refused.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, expect, sync_playwright

# The repository root, for #213's scenes (scripts/example_media)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.example_media import scenes  # noqa: E402
from scripts.example_media.player import perform  # noqa: E402

LIVE = '[data-is-live="true"]'
#: Marks this run's messages: the demo keeps earlier runs' until the next reset
RUN = format(int(time.time() * 1000) % 10**8, "x")
TIMEOUT = 15_000
#: Over the public address every request goes through Cloudflare and the tunnel (a visitor in
#: Korea may enter at a far edge), and a fresh context's first page loads the bundle from there
REMOTE_TIMEOUT = 45_000
#: This run's visitor address over plain http: one of TEST-NET-2's
VISITOR = f"198.51.100.{random.randrange(1, 255)}"

#: #213's scenes the demo can play as they are: no seed of their own, no sign-in as alice or
#: bob (the demo signs in guests), and steps that hold on the demo's seed (search's books)
SCENES = (scenes.SEARCH, scenes.LIVECOMP, scenes.SLOTS, scenes.HOOKS)


def local_headers(base: str) -> dict[str, str]:
    if not base.startswith("http://"):
        return {}
    return {"X-Forwarded-Proto": "https", "CF-Connecting-IP": VISITOR}


def context(browser: Browser, base: str):
    headers = local_headers(base)
    ctx = browser.new_context(base_url=base, extra_http_headers=headers, viewport={"width": 1280, "height": 800})
    ctx.set_default_timeout(TIMEOUT if base.startswith("http://") else REMOTE_TIMEOUT)
    return ctx


def watch(page: Page, errors: list[str]) -> None:
    page.on("pageerror", lambda error: errors.append(f"{page.url}: {error}"))
    page.on(
        "console",
        lambda message: errors.append(f"{page.url}: console {message.text}") if message.type == "error" else None,
    )


def check_list(browser: Browser, base: str, out: Path, errors: list[str]) -> list[str]:
    page = context(browser, base).new_page()
    watch(page, errors)
    page.goto("/")
    examples = page.locator("li.example")
    expect(examples).to_have_count(14)
    page.screenshot(path=out / "00-list.png", full_page=True)
    hrefs = [examples.nth(i).locator("h2 a").get_attribute("href") or "" for i in range(14)]
    print("list: 14 examples")
    return hrefs


def check_pages(browser: Browser, base: str, hrefs: list[str], out: Path, errors: list[str]) -> None:
    page = context(browser, base).new_page()
    watch(page, errors)
    for i, href in enumerate(hrefs, start=1):
        response = page.goto(href)
        assert response is not None and response.ok, f"{href}: {response and response.status}"
        page.wait_for_load_state("networkidle")
        page.screenshot(path=out / f"{i:02d}-{href.strip('/').replace('/', '-') or 'root'}.png", full_page=True)
        live = page.locator(LIVE).count()
        print(f"page {href}: {response.status}, {live} live components")


def check_live_examples(browser: Browser, base: str, out: Path, errors: list[str]) -> None:
    page = context(browser, base).new_page()
    watch(page, errors)
    sockets: list[str] = []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    for href, name in (("/todo/todo", "XTodoList"), ("/dashboard/", "XDashboard"), ("/search/", "XLiveSearch")):
        page.goto(href)
        page.wait_for_selector(f'[data-name="{name}"]{LIVE}')
        print(f"live: {href} {name} joined")
    assert any(urlsplit(url).path == "/__wireview__" for url in sockets), sockets
    print(f"websocket: {sockets[0]}")
    # The seeded lists: their first item's page goes live
    for index, prefix, name in (
        ("/poll/", "/poll/", "XPoll"),
        ("/rating/", "/rating/", "XStarRating"),
        ("/quiz/", "/quiz/", "XQuiz"),
    ):
        page.goto(index)
        page.locator(f'a[href^="{prefix}"][href$="/"]:not([href="{prefix}"])').first.click()
        page.wait_for_selector(f'[data-name="{name}"]{LIVE}')
        page.screenshot(path=out / f"20-{name.lower()}.png", full_page=True)
        print(f"live: {page.url} {name} joined")
    # The dashboard's cards load through assign_async
    page.goto("/dashboard/")
    expect(page.get_by_text("Revenue").first).to_be_visible()
    page.screenshot(path=out / "20-xdashboard.png", full_page=True)


def check_chat(browser: Browser, base: str, out: Path, errors: list[str]) -> None:
    """Level 1: two anonymous browsers in the Lobby; a message from one reaches the other, cut to TEXT_MAX."""
    a, b = context(browser, base).new_page(), context(browser, base).new_page()
    for page in (a, b):
        watch(page, errors)
        page.goto("/chat/")
        page.get_by_role("link", name="Lobby").click()
        page.wait_for_selector(f'[data-name="XChatRoom"]{LIVE}')
    a.locator('input[name="content"]').fill(f"hello from A {RUN}")
    a.locator('input[name="content"]').press("Enter")
    expect(b.locator('li[id^="messages-"]', has_text=f"hello from A {RUN}")).to_have_count(1)
    long = RUN + "x" * 2000
    a.locator('input[name="content"]').fill(long)
    a.locator('input[name="content"]').press("Enter")
    message = b.locator('li[id^="messages-"] p', has_text=RUN + "xxxx")
    expect(message).to_have_count(1)
    length = len(message.inner_text())
    assert length == 500, f"a 2000-character message arrived as {length} characters"
    a.screenshot(path=out / "21-chat-a.png", full_page=True)
    b.screenshot(path=out / "22-chat-b.png", full_page=True)
    print(f"chat: B saw A's message; a 2000-character message arrived as {length}")


def check_scenes(browser: Browser, base: str, out: Path, errors: list[str]) -> None:
    """#213's scenes, step for step, at full speed: what the README's clips show works here too."""
    for scene in SCENES:
        pages = {}
        for actor in scene.actors:
            page = context(browser, base).new_page()
            watch(page, errors)
            pages[actor.name] = page
        for step in (*scene.prepare, *scene.play):
            perform(pages, scene, step, base, {}, paced=False)
        pages[scene.actors[0].name].screenshot(path=out / f"30-scene-{scene.example}.png", full_page=True)
        print(f"scene {scene.example}: {len(scene.prepare) + len(scene.play)} steps")


def guest(page: Page, index: str) -> str:
    page.goto(index)
    page.get_by_role("button", name="Sign in as guest").click()
    name = page.get_by_test_id("signed-in-as").inner_text()
    assert name.startswith("guest-"), name
    return name


def check_guest_chat(browser: Browser, base: str, out: Path, errors: list[str]) -> None:
    """Level 2: each browser one click to a guest of its own, both join the Lobby and talk."""
    a, b = context(browser, base).new_page(), context(browser, base).new_page()
    names = []
    for page in (a, b):
        watch(page, errors)
        names.append(guest(page, "/chat-lv2/"))
        page.locator('[data-room="Lobby"]').get_by_role("button", name="Join").click()
        page.wait_for_selector(f'[data-name="Lv2ChatRoom"]{LIVE}')
    assert names[0] != names[1], names
    a.locator('input[name="content"]').fill(f"hi from the first guest {RUN}")
    a.locator('input[name="content"]').press("Enter")
    item = b.locator('li[id^="messages-"]', has_text=f"hi from the first guest {RUN}")
    expect(item).to_have_count(1)
    expect(item.locator("strong")).to_have_text(names[0])
    a.screenshot(path=out / "23-chat-lv2-a.png", full_page=True)
    b.screenshot(path=out / "24-chat-lv2-b.png", full_page=True)
    print(f"guest chat: {names[0]} and {names[1]} talked in chat_lv2")

    page = context(browser, base).new_page()
    watch(page, errors)
    name = guest(page, "/notifications/")
    page.wait_for_selector(f'[data-name="XNotificationList"]{LIVE}')
    page.screenshot(path=out / "25-notifications.png", full_page=True)
    print(f"notifications: {name} signed in, list live")


def check_socket_limits(base: str) -> None:
    """A frame past FRAME_MAX and events past the bucket close the socket."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    parts = urlsplit(base)
    url = f"{'wss' if parts.scheme == 'https' else 'ws'}://{parts.netloc}/__wireview__?vsn=10"
    origin = f"{parts.scheme}://{parts.netloc}"

    def closed_by(frames: list[str]) -> int | None:
        headers = local_headers(base)
        with connect(url, origin=origin, open_timeout=10, additional_headers=headers) as ws:  # type: ignore[arg-type]
            try:
                for frame in frames:
                    ws.send(frame)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    ws.recv(timeout=deadline - time.monotonic())
            except ConnectionClosed as closed:
                return closed.rcvd.code if closed.rcvd else None
            except TimeoutError:
                return None
        return None

    event = json.dumps(
        {"command": "user_event", "payload": {"id": "nobody", "command": "x", "implicit_args": {}, "explicit_args": {}}}
    )
    burst = closed_by([event] * 60)
    assert burst == 4029, f"60 events at once closed the socket with {burst}"
    big = closed_by([json.dumps({"command": "join", "payload": {"state": "x" * 70_000}})])
    assert big == 1009, f"a 70KB frame closed the socket with {big}"
    print("socket limits: an event burst closed with 4029, an oversized frame with 1009")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", help="the demo's address, e.g. http://127.0.0.1:8080")
    parser.add_argument("--out", type=Path, required=True, help="where screenshots go")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            hrefs = check_list(browser, base, args.out, errors)
            check_pages(browser, base, hrefs, args.out, errors)
            check_live_examples(browser, base, args.out, errors)
            check_scenes(browser, base, args.out, errors)
            check_chat(browser, base, args.out, errors)
            check_guest_chat(browser, base, args.out, errors)
        finally:
            browser.close()
    check_socket_limits(base)
    if errors:
        print("browser errors:\n  " + "\n  ".join(errors))
        return 1
    print("smoke: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
