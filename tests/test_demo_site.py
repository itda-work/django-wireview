"""The public demo's project (demo/, #214): its settings pass the deploy checks, and its guards hold.

The demo is a Django project of its own, so its settings are checked in a process of their own
(``manage.py check --deploy``); a deploy-time warning there is a warning on the public site. The
rest runs here, on testproj, which installs the same examples: the socket and request limits, the
guest sign-in, the janitor's schedule. The running stack is checked by demo/smoke.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from demo import limits
from demo.guests import PREFIX, is_guest, sign_in
from demo.management.commands.demo_janitor import next_reset

ROOT = Path(__file__).resolve().parent.parent

LIMITS = {
    "TEXT_MAX": 10,
    "FRAME_MAX": 1000,
    "EVENT_BURST": 3,
    "EVENTS_PER_SECOND": 0.001,
    "POSTS_PER_MINUTE": 2,
    "SOCKETS_PER_MINUTE": 2,
    "GUESTS_PER_HOUR": 1,
}


@pytest.fixture(autouse=True)
def demo_limits():
    cache.clear()
    with override_settings(DEMO_LIMITS=LIMITS):
        yield
    cache.clear()


@pytest.mark.integration
def test_the_settings_pass_the_deploy_checks(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    env.update(DJANGO_SECRET_KEY="a" * 50 + "-not-a-real-key", DEMO_DATA_DIR=str(tmp_path))
    checked = subprocess.run(
        [sys.executable, "demo/manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.integration
def test_the_settings_refuse_to_start_without_a_secret(tmp_path):
    env = {
        key: value for key, value in os.environ.items() if key not in ("DJANGO_SETTINGS_MODULE", "DJANGO_SECRET_KEY")
    }
    env["DEMO_DATA_DIR"] = str(tmp_path)
    checked = subprocess.run(
        [sys.executable, "demo/manage.py", "check"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    assert checked.returncode != 0
    assert "DJANGO_SECRET_KEY" in checked.stderr


class _Socket:
    """An ASGI WebSocket conversation: frames the page sends, and what reached the consumer and the page."""

    def __init__(self, *frames: str):
        self.incoming = [{"type": "websocket.connect"}, *({"type": "websocket.receive", "text": f} for f in frames)]
        self.heard: list[dict] = []
        self.sent: list[dict] = []

    async def receive(self):
        return self.incoming.pop(0) if self.incoming else {"type": "websocket.disconnect", "code": 1000}

    async def send(self, message):
        self.sent.append(message)

    async def consumer(self, scope, receive, send):
        while True:
            message = await receive()
            self.heard.append(message)
            if message["type"] == "websocket.connect":
                await send({"type": "websocket.accept"})
            if message["type"] == "websocket.disconnect":
                return
            await send({"type": "websocket.send", "text": "answer"})

    async def run(self, ip: str = "203.0.113.7"):
        scope = {"type": "websocket", "headers": [(b"x-real-ip", ip.encode())], "client": ("10.0.0.2", 1)}
        await limits.SocketLimits(self.consumer)(scope, self.receive, self.send)


def _event(**args) -> str:
    return json.dumps(
        {"command": "user_event", "payload": {"id": "c", "command": "say", "implicit_args": args, "explicit_args": {}}}
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_events_text_is_cut_to_the_limit():
    socket = _Socket(_event(content="x" * 50, nested=["y" * 50]))
    await socket.run()
    payload = json.loads(socket.heard[1]["text"])["payload"]["implicit_args"]
    assert payload == {"content": "x" * 10, "nested": ["y" * 10]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_events_past_the_bucket_close_the_socket_and_the_consumer_hears_a_disconnect():
    socket = _Socket(*[_event()] * 5)
    await socket.run()
    assert [m["type"] for m in socket.heard] == [
        "websocket.connect",
        *["websocket.receive"] * 3,
        "websocket.disconnect",
    ]
    closes = [m for m in socket.sent if m["type"] == "websocket.close"]
    assert closes == [{"type": "websocket.close", "code": limits.TOO_MANY}]
    # Nothing the consumer sends after the close reaches the page
    assert socket.sent[-1]["type"] == "websocket.close"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_join_does_not_spend_the_event_bucket():
    join = json.dumps({"command": "join", "payload": {"name": "X", "state": "s"}})
    socket = _Socket(*[join] * 10)
    await socket.run()
    assert not [m for m in socket.sent if m["type"] == "websocket.close"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_frame_past_the_limit_closes_the_socket():
    socket = _Socket("x" * 1001)
    await socket.run()
    assert {"type": "websocket.close", "code": limits.TOO_BIG} in socket.sent
    assert socket.heard[-1] == {"type": "websocket.disconnect", "code": limits.TOO_BIG}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_sockets_past_the_minutes_limit_are_refused_per_visitor():
    for _ in range(2):
        await _Socket().run()
    refused = _Socket()
    await refused.run()
    assert refused.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
    other = _Socket()
    await other.run(ip="198.51.100.9")
    assert other.sent[0] == {"type": "websocket.accept"}


def _through_middleware(request) -> HttpResponse:
    return limits.RequestLimitMiddleware(lambda request: HttpResponse("ok"))(request)


@pytest.mark.unit
def test_a_field_past_the_limit_is_refused():
    factory = RequestFactory()
    assert _through_middleware(factory.get("/chat/", {"username": "x" * 11})).status_code == 400
    assert _through_middleware(factory.get("/chat/", {"username": "x" * 10})).status_code == 200


@pytest.mark.unit
def test_posts_past_the_minutes_limit_are_refused():
    factory = RequestFactory()
    statuses = [
        _through_middleware(factory.post("/x/", {"name": "a"}, HTTP_X_REAL_IP="203.0.113.7")).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]


def _signed_in(request):
    SessionMiddleware(lambda request: HttpResponse()).process_request(request)
    from django.contrib.auth.middleware import AuthenticationMiddleware

    AuthenticationMiddleware(lambda request: HttpResponse()).process_request(request)
    return request


@pytest.mark.integration
@pytest.mark.django_db
def test_each_browser_gets_a_guest_of_its_own_and_keeps_it():
    factory = RequestFactory()
    first = _signed_in(factory.post("/chat-lv2/sign-in/alice/", {"next": "/chat-lv2/"}, HTTP_X_REAL_IP="203.0.113.1"))
    response = sign_in(first, example="chat_lv2", username="alice")
    assert response.status_code == 302 and response["Location"] == "/chat-lv2/"
    assert is_guest(first.user) and first.user.username.startswith(PREFIX)
    # The same browser again: the same guest, and no new one counted
    again = _signed_in(factory.post("/chat-lv2/sign-in/guest/", HTTP_X_REAL_IP="203.0.113.1"))
    again.session = first.session
    again.user = first.user
    sign_in(again, example="chat_lv2", username="guest")
    assert again.user == first.user
    # Another browser from the same address is past GUESTS_PER_HOUR (1)
    other = _signed_in(factory.post("/chat-lv2/sign-in/guest/", HTTP_X_REAL_IP="203.0.113.1"))
    assert sign_in(other, example="chat_lv2", username="guest").status_code == 429


@pytest.mark.unit
@pytest.mark.parametrize(
    "now, expected",
    [
        (dt.datetime(2026, 10, 12, 3, 59), dt.datetime(2026, 10, 12, 4, 0)),
        (dt.datetime(2026, 10, 12, 4, 0), dt.datetime(2026, 10, 13, 4, 0)),
        (dt.datetime(2026, 10, 12, 15, 30), dt.datetime(2026, 10, 13, 4, 0)),
    ],
)
def test_the_reset_is_the_next_occurrence_of_its_time(now, expected):
    seoul = ZoneInfo("Asia/Seoul")
    assert next_reset(now.replace(tzinfo=seoul), "04:00") == expected.replace(tzinfo=seoul)
