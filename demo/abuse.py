"""What a script would send to the demo, and that the demo refuses it. Not part of the test suite:
it needs the stack up, on this machine.

    uv run python demo/abuse.py http://127.0.0.1:8080

The #214 review's probes, each from a visitor of its own: Caddy takes ``CF-Connecting-IP`` from a
private address, so over the host's port each check names a fresh test address and spends no
other check's limits (nor demo/smoke.py's). That is also why it runs only against a local stack:
through Cloudflare every check would be the one address of the machine running it.

Each check prints what happened; the script exits 1 if anything got through.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

TOO_MANY = 4029
TOO_BIG = 1009

#: This run's visitors: one of 198.18.0.0/15's blocks (addresses for testing), a block per run,
#: so a second run in the same hour does not meet the first one's counts
_block = f"198.{18 + random.randrange(2)}.{random.randrange(256)}"
_addresses = (f"{_block}.{n}" for n in itertools.count(1))


class Visitor:
    """One address, with its cookies."""

    def __init__(self, base: str):
        self.base = base
        self.address = next(_addresses)
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Forwarded-Proto": "https", "CF-Connecting-IP": self.address}

    def get(self, path: str, cookies: bool = True) -> tuple[int, dict[str, str], str]:
        request = urllib.request.Request(self.base + path, headers=self.headers)
        open_ = self.opener.open if cookies else urllib.request.urlopen
        try:
            response = open_(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, dict(response.headers), response.read().decode()

    def socket(self):
        parts = urllib.parse.urlsplit(self.base)
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.jar)
        headers = {**self.headers, **({"Cookie": cookie} if cookie else {})}
        return connect(
            f"ws://{parts.netloc}/__wireview__?vsn=10",
            origin=f"{parts.scheme}://{parts.netloc}",  # type: ignore[arg-type]
            additional_headers=headers,
            open_timeout=10,
        )


def drain(ws, until: float = 5) -> tuple[dict[str, int], int | None]:
    """The commands the server answered with, and the close code if it closed the socket."""
    answers: dict[str, int] = {}
    deadline = time.monotonic() + until
    try:
        while time.monotonic() < deadline:
            message = json.loads(ws.recv(timeout=deadline - time.monotonic()))
            answers[message["command"]] = answers.get(message["command"], 0) + 1
    except ConnectionClosed as closed:
        return answers, closed.rcvd.code if closed.rcvd else None
    except TimeoutError:
        pass
    return answers, None


def send_all(ws, frames) -> None:
    try:
        for frame in frames:
            ws.send(frame)
    except ConnectionClosed:
        pass


class Components(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "data-state" in attributes:
            self.found.append(attributes)


def joined_search(visitor: Visitor):
    """A socket that joined the search page's component, and that component's join frame."""
    _, _, body = visitor.get("/search/")
    parser = Components()
    parser.feed(body)
    search = next(c for c in parser.found if c.get("data-name") == "XLiveSearch")
    join = json.dumps({"command": "join", "payload": {"name": search["data-name"], "state": search["data-state"]}})
    ws = visitor.socket()
    ws.send(join)
    while json.loads(ws.recv(timeout=10))["command"] != "render":
        pass
    return ws, join


def check_reflected_xss(base: str) -> str | None:
    attack = '<script>document.documentElement.dataset.review214="xss"</script>'
    status, headers, body = Visitor(base).get("/?" + urllib.parse.urlencode({attack: "x" * 501}))
    print(f"reflected_xss: {status} {headers.get('Content-Type')} {body.strip()!r}")
    if status != 400 or not headers.get("Content-Type", "").startswith("text/plain") or "<" in body:
        return "the refused field came back as markup"
    return None


def check_params_flood(base: str) -> str | None:
    ws, _ = joined_search(Visitor(base))
    with ws:
        frames = (
            json.dumps({"command": "params_changed", "payload": {"params": {"q": str(i)}, "uri": f"/search/?q={i}"}})
            for i in range(100)
        )
        send_all(ws, frames)
        answers, code = drain(ws)
    print(f"params_flood: 100 params_changed -> {answers}, closed {code}")
    return None if code == TOO_MANY and answers.get("render", 0) < 100 else "100 params_changed went through"


def check_join_flood(base: str) -> str | None:
    ws, join = joined_search(Visitor(base))
    with ws:
        send_all(ws, [join] * 60)
        answers, code = drain(ws)
    print(f"join_flood: 60 joins of one state -> {answers}, closed {code}")
    return None if code == TOO_MANY and answers.get("render", 0) < 60 else "60 joins of one state went through"


def check_reconnect_burst(base: str) -> str | None:
    visitor = Visitor(base)
    event = json.dumps(
        {
            "command": "user_event",
            "payload": {"id": "missing", "command": "x", "implicit_args": {}, "explicit_args": {}},
        }
    )
    answered = 0
    codes = []
    for _ in range(6):
        with visitor.socket() as ws:
            send_all(ws, [event] * 30)
            answers, code = drain(ws, until=1)
        answered += sum(answers.values())
        codes.append(code)
    print(f"reconnect_burst: 6 sockets x 30 events -> {answered} answers, closes {codes}")
    return None if answered < 180 and TOO_MANY in codes else "a fresh socket started the visitor's events over"


def check_unicode_frame(base: str) -> str | None:
    frame = json.dumps({"command": "sync", "payload": {"ref": "가" * 30000}}, ensure_ascii=False)
    with Visitor(base).socket() as ws:
        send_all(ws, [frame, json.dumps({"command": "sync", "payload": {"ref": 214}})])
        answers, code = drain(ws)
    print(f"unicode_frame: {len(frame)} characters, {len(frame.encode())} bytes -> {answers}, closed {code}")
    return None if code == TOO_BIG else "a frame past FRAME_MAX in bytes went through"


def check_junk_flood(base: str) -> str | None:
    with Visitor(base).socket() as ws:
        send_all(ws, ["not json"] * 20 + [json.dumps({"command": "nope"})] * 20)
        answers, code = drain(ws)
    print(f"junk_flood: 40 frames the server does not read -> closed {code}")
    return None if code == TOO_MANY else "a flood of unreadable frames went through"


def check_open_sockets(base: str, most: int) -> str | None:
    visitor = Visitor(base)
    held = []
    refused = None
    try:
        for n in range(most + 1):
            try:
                ws = visitor.socket()
            except InvalidStatus as error:
                refused = (n, error.response.status_code)
                break
            held.append(ws)
            # A socket the server refuses after the handshake is closed at once
            ws.send(json.dumps({"command": "sync", "payload": {"ref": n}}))
            try:
                ws.recv(timeout=5)
            except ConnectionClosed as closed:
                refused = (n, closed.rcvd.code if closed.rcvd else None)
                break
    finally:
        for ws in held:
            ws.close()
    print(
        f"open_sockets: one visitor's socket number {refused and refused[0] + 1} refused with {refused and refused[1]}"
    )
    return None if refused and refused[0] == most else f"socket {most + 1} of one visitor was let in"


def check_session_gets(base: str, most: int) -> str | None:
    visitor = Visitor(base)
    _, _, body = visitor.get("/chat/")
    room = body.split('href="/chat/room/', 1)[1].split('"', 1)[0]
    statuses: dict[int, int] = {}
    cookies = 0
    for _ in range(most + 20):
        status, headers, _ = visitor.get(f"/chat/room/{room}", cookies=False)
        statuses[status] = statuses.get(status, 0) + 1
        cookies += "sessionid=" in headers.get("Set-Cookie", "")
    print(f"anonymous_session_gets: {most + 20} cookieless GETs -> {statuses}, {cookies} new session cookies")
    return None if cookies == most and statuses.get(429) == 20 else "cookieless GETs made sessions past the budget"


def check_request_budget(base: str, most: int) -> str | None:
    visitor = Visitor(base)
    statuses: dict[int, int] = {}
    for _ in range(most + 5):
        status, _, _ = visitor.get("/health/")
        statuses[status] = statuses.get(status, 0) + 1
    print(f"request_budget: {most + 5} GETs in a minute -> {statuses}")
    return None if statuses.get(429) == 5 else "requests past the minute's budget went through"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", help="the local stack, e.g. http://127.0.0.1:8080")
    parser.add_argument("--open-sockets", type=int, default=40, help="DEMO_OPEN_SOCKETS_PER_VISITOR")
    parser.add_argument("--sessions", type=int, default=60, help="DEMO_SESSIONS_PER_HOUR")
    parser.add_argument("--requests", type=int, default=300, help="DEMO_REQUESTS_PER_MINUTE")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    if not base.startswith("http://"):
        print("abuse.py names its visitors with CF-Connecting-IP, which only a local stack takes from it")
        return 2
    failures = [
        failure
        for failure in (
            check_reflected_xss(base),
            check_params_flood(base),
            check_join_flood(base),
            check_reconnect_burst(base),
            check_unicode_frame(base),
            check_junk_flood(base),
            check_open_sockets(base, args.open_sockets),
            check_session_gets(base, args.sessions),
            check_request_budget(base, args.requests),
        )
        if failure
    ]
    if failures:
        print("got through:\n  " + "\n  ".join(failures))
        return 1
    print("abuse: every probe was refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
