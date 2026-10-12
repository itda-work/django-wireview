"""The public demo's project (demo/, #214): its settings pass the deploy checks, and its guards hold.

The demo is a Django project of its own, so its settings are checked in a process of their own
(``manage.py check --deploy``); a deploy-time warning there is a warning on the public site. The
rest runs here, on testproj, which installs the same examples: the socket and request limits, the
guest sign-in, the janitor's schedule. The running stack is checked by demo/smoke.py.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from testproj.waiting import eventually

from demo import limits
from demo.guests import PREFIX, is_guest, sign_in
from demo.management.commands.demo_janitor import next_reset

ROOT = Path(__file__).resolve().parent.parent

LIMITS = {
    "TEXT_MAX": 10,
    "FRAME_MAX": 1000,
    "FRAME_BURST": 40,
    "FRAMES_PER_SECOND": 0.001,
    "JUNK_BURST": 2,
    "JUNK_PER_SECOND": 0.001,
    "EVENT_BURST": 3,
    "EVENTS_PER_SECOND": 0.001,
    "JOIN_BURST": 8,
    "JOINS_PER_SECOND": 0.001,
    "RENDER_BURST": 3,
    "RENDERS_PER_SECOND": 0.001,
    "VISITOR_WINDOW": 60,
    "VISITOR_FRAMES": 1000,
    "VISITOR_EVENTS": 5,
    "VISITOR_RENDERS": 1000,
    "SOCKETS_PER_MINUTE": 2,
    "OPEN_SOCKETS_PER_VISITOR": 2,
    "OPEN_SOCKETS_TOTAL": 3,
    "REQUESTS_PER_MINUTE": 1000,
    "POSTS_PER_MINUTE": 2,
    "SESSIONS_PER_HOUR": 2,
    "SESSIONS_MAX": 1000,
    "GUESTS_PER_HOUR": 1,
    "GUESTS_MAX": 1000,
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
        encoding="utf-8",
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.integration
def test_the_settings_refuse_to_start_without_a_secret(tmp_path):
    env = {
        key: value for key, value in os.environ.items() if key not in ("DJANGO_SETTINGS_MODULE", "DJANGO_SECRET_KEY")
    }
    env["DEMO_DATA_DIR"] = str(tmp_path)
    checked = subprocess.run(
        [sys.executable, "demo/manage.py", "check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
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


def _join(state: str, name: str = "X") -> str:
    return json.dumps({"command": "join", "payload": {"name": name, "state": state}})


def _signed(component_id: str, query: str = "") -> str:
    """A join of search's component, signed as a page would carry it."""
    from django.contrib.auth.models import AnonymousUser

    from examples.search.live import XLiveSearch
    from wireview.core.meta import WireviewMeta
    from wireview.core.state import sign_state

    state = sign_state(XLiveSearch(id=component_id, query=query, user=AnonymousUser(), wire=WireviewMeta(params={})))
    return _join(state, "XLiveSearch")


def _closed_with(socket: _Socket) -> int | None:
    codes = [m["code"] for m in socket.sent if m["type"] == "websocket.close"]
    return codes[0] if codes else None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_pages_first_joins_pass_and_do_not_spend_the_event_bucket():
    """A page joins each of its components once: a burst of first joins is the page loading."""
    socket = _Socket(*[_signed(f"search-{i}") for i in range(LIMITS["JOIN_BURST"])], _event())
    await socket.run()
    assert _closed_with(socket) is None
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["JOIN_BURST"] + 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_joining_the_same_state_again_and_again_spends_the_render_bucket():
    """The review's join flood: one valid state joined 60 times, each a restore and a render."""
    socket = _Socket(*[_signed("search")] * 60)
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    # The first join, then RENDER_BURST rejoins
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == 1 + LIMITS["RENDER_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_same_component_signed_afresh_is_a_rejoin_not_a_first_join():
    """The second review's fresh joins: 60 valid states of one component, each signed apart."""
    socket = _Socket(*[_signed("search", query=f"q{i}") for i in range(60)])
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == 1 + LIMITS["RENDER_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_state_that_does_not_verify_is_no_first_join():
    socket = _Socket(*[_join(f"forged-{i}", "XLiveSearch") for i in range(10)])
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["RENDER_BURST"]


@pytest.mark.unit
def test_a_join_names_its_component_by_class_and_id():
    join = json.loads(_signed("search-a", query="x"))["payload"]
    assert limits.join_identity(join) == ("examples.search.live.XLiveSearch", "search-a")
    assert limits.join_identity({**join, "name": "examples.search.live.XLiveSearch"}) == limits.join_identity(join)
    assert limits.join_identity({**join, "state": join["state"][:-2]}) is None
    assert limits.join_identity({**join, "name": "XNotificationBell"}) is None


def _params(q: str) -> str:
    return json.dumps({"command": "params_changed", "payload": {"params": {"q": q}, "uri": f"/search/?q={q}"}})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_params_flood_closes_the_socket():
    """The review's params flood: 100 params_changed, each a search and a render."""
    socket = _Socket(*[_params(str(i)) for i in range(100)])
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["RENDER_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_texts_outside_user_event_are_cut_too_but_a_joins_state_is_not():
    navigated = json.dumps(
        {
            "command": "navigated",
            "payload": {"params": {"q": "가" * 50}, "uri": "/search/?q=" + "가" * 50, "carried": []},
        }
    )
    hook = json.dumps(
        {
            "command": "hook_event",
            "payload": {"component_id": "c", "hook_id": "h", "event": "e", "payload": {"t": "x" * 50}, "ref": None},
        }
    )
    socket = _Socket(_params("y" * 50), navigated, hook, _join("s" * 50))
    await socket.run()
    heard = [json.loads(m["text"])["payload"] for m in socket.heard if m["type"] == "websocket.receive"]
    assert heard[0] == {"params": {"q": "y" * 10}, "uri": "/search/?q=" + "y" * 10}
    assert heard[1]["params"] == {"q": "가" * 10}
    assert parse_qs(urlsplit(heard[1]["uri"]).query) == {"q": ["가" * 10]}
    assert heard[2]["payload"] == {"t": "x" * 10}
    assert heard[3]["state"] == "s" * 50


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_page_s_own_address_goes_on_unchanged():
    socket = _Socket(
        json.dumps({"command": "params_changed", "payload": {"params": {"q": "a b"}, "uri": "/search/?q=a%20b"}})
    )
    await socket.run()
    assert json.loads(socket.heard[1]["text"])["payload"]["uri"] == "/search/?q=a%20b"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_the_server_does_not_read_never_reach_it_and_a_flood_of_them_closes_the_socket():
    socket = _Socket("not json", json.dumps({"command": "nope"}), json.dumps([1]), json.dumps({"command": "sync"}))
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert [m["type"] for m in socket.heard] == ["websocket.connect", "websocket.disconnect"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_frame_spends_the_frame_bucket():
    sync = json.dumps({"command": "sync", "payload": {"ref": 1}})
    socket = _Socket(*[sync] * (LIMITS["FRAME_BURST"] + 5))
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["FRAME_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_visitor_who_reconnects_does_not_start_the_event_window_over():
    """The review's reconnect burst: fresh sockets, each with a fresh bucket, the same window."""
    heard = 0
    for _ in range(2):
        socket = _Socket(*[_event()] * LIMITS["EVENT_BURST"])
        await socket.run()
        heard += len([m for m in socket.heard if m["type"] == "websocket.receive"])
    assert heard == LIMITS["VISITOR_EVENTS"]
    assert _closed_with(socket) == limits.TOO_MANY


class _Holding(_Socket):
    """A socket that stays open until ``done`` is set."""

    def __init__(self):
        super().__init__()
        self.done = asyncio.Event()

    async def receive(self):
        if self.incoming:
            return self.incoming.pop(0)
        await self.done.wait()
        return {"type": "websocket.disconnect", "code": 1000}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_sockets_are_capped_per_visitor_and_in_all_and_given_back_on_close():
    with override_settings(DEMO_LIMITS={**LIMITS, "SOCKETS_PER_MINUTE": 100}):
        held = [_Holding(), _Holding()]
        tasks = [asyncio.create_task(socket.run()) for socket in held]
        await eventually(lambda: all(socket.sent for socket in held))
        third = _Socket()
        await third.run()
        assert third.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
        # Another visitor fits under the demo's total (3), and a fourth socket does not
        other = _Holding()
        tasks.append(asyncio.create_task(other.run(ip="198.51.100.9")))
        await eventually(lambda: other.sent)
        assert other.sent[0] == {"type": "websocket.accept"}
        last = _Socket()
        await last.run(ip="198.51.100.10")
        assert last.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
        for socket in (*held, other):
            socket.done.set()
        await asyncio.gather(*tasks)
        # Closed sockets give their places back
        again = _Socket()
        await again.run()
        assert again.sent[0] == {"type": "websocket.accept"}


class _Backpressured(_Socket):
    """A server whose close waits for the page's answer behind the frames already queued, as uvicorn's does."""

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "websocket.close":
            while self.incoming:
                await asyncio.sleep(0.001)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_closing_a_flooding_socket_reads_out_what_it_already_sent():
    """The review's params flood left 79 frames in uvicorn's queue of 32: the close never finished."""
    socket = _Backpressured(*[_params(str(i)) for i in range(100)])
    async with asyncio.timeout(5):
        await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert not socket.incoming
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["RENDER_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_window_ends_a_window_after_its_first_hit_however_many_follow():
    """Counting from the loop kept pushing the end out (BaseCache.aincr writes back with the default timeout)."""
    assert await limits.acount("t", "v", 1) == 1
    await asyncio.sleep(0.6)
    assert await limits.acount("t", "v", 1) == 2
    await asyncio.sleep(0.6)
    assert await limits.acount("t", "v", 1) == 1


@pytest.mark.unit
def test_an_ipv6_visitor_is_its_64():
    assert (
        limits.visitor_key("2001:db8:1:2:aaaa::1") == limits.visitor_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    )
    assert limits.visitor_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert limits.visitor_key("203.0.113.7") == "203.0.113.7"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unreachable_counters_close_the_socket_rather_than_let_it_through(monkeypatch):
    def down(*args, **kwargs):
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(limits.cache, "incr", down)
    socket = _Socket(_event())
    await socket.run()
    assert socket.sent == [{"type": "websocket.close", "code": limits.TRY_LATER}]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "character, fits",
    [("x", 1), ("가", 3), ("😀", 4)],
    ids=["ascii", "korean", "emoji"],
)
async def test_the_frame_limit_is_in_utf8_bytes(character, fits):
    """FRAME_MAX is bytes: Korean is three a character, an emoji four. One byte over closes."""
    frame = '{"command":"sync","payload":{"ref":"'
    tail = '"}}'
    room = LIMITS["FRAME_MAX"] - len(frame.encode()) - len(tail.encode())
    exact = frame + character * (room // fits) + "x" * (room % fits) + tail
    assert len(exact.encode()) == LIMITS["FRAME_MAX"]
    fitting = _Socket(exact)
    await fitting.run()
    assert _closed_with(fitting) is None
    over = _Socket(exact[:-3] + "x" + tail)
    await over.run()
    assert {"type": "websocket.close", "code": limits.TOO_BIG} in over.sent
    assert over.heard[-1] == {"type": "websocket.disconnect", "code": limits.TOO_BIG}


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


ATTACK = '<script>document.documentElement.dataset.review214="xss"</script>'


@pytest.mark.unit
@pytest.mark.parametrize("method", ["get", "post"])
def test_a_refused_field_is_answered_in_plain_text_without_its_name(method):
    """The review's reflected XSS: a field's name is the visitor's, so the answer never repeats it as HTML."""
    factory = RequestFactory()
    request = getattr(factory, method)("/", {ATTACK: "x" * 11, "<b>bold</b>": "y" * 50})
    response = _through_middleware(request)
    assert response.status_code == 400
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    body = response.content.decode()
    assert "<" not in body and "script" not in body and "bold" not in body


@pytest.mark.unit
def test_every_answer_the_limits_give_is_plain_text():
    factory = RequestFactory()
    answers = [_through_middleware(factory.post("/x/", {"a": "1"}, HTTP_X_REAL_IP="203.0.113.8")) for _ in range(3)]
    assert answers[-1].status_code == 429
    assert answers[-1]["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.unit
def test_requests_past_the_minutes_budget_are_refused_but_not_compose_s_probes():
    factory = RequestFactory()
    with override_settings(DEMO_LIMITS={**LIMITS, "REQUESTS_PER_MINUTE": 2}):
        statuses = [_through_middleware(factory.get("/", HTTP_X_REAL_IP="203.0.113.9")).status_code for _ in range(3)]
        assert statuses == [200, 200, 429]
        # A healthcheck from inside the network carries no X-Real-IP and is not counted
        probes = [_through_middleware(factory.get("/ready/")).status_code for _ in range(5)]
        assert probes == [200] * 5
        # Through Caddy, the same path is a visitor's request like any other
        assert _through_middleware(factory.get("/ready/", HTTP_X_REAL_IP="203.0.113.9")).status_code == 429


@pytest.mark.unit
def test_unreachable_counters_refuse_the_request(monkeypatch):
    def down(*args, **kwargs):
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(limits.cache, "incr", down)
    response = _through_middleware(RequestFactory().get("/", HTTP_X_REAL_IP="203.0.113.9"))
    assert response.status_code == 503 and response["Content-Type"] == "text/plain; charset=utf-8"


DEMO_STACK = {
    "SESSION_ENGINE": "demo.sessions",
    "MIDDLEWARE": [
        "django.middleware.security.SecurityMiddleware",
        "demo.limits.RequestLimitMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
    ],
}


@pytest.mark.integration
@pytest.mark.django_db
def test_gets_without_a_cookie_stop_making_sessions_at_the_visitors_budget():
    """The review's anonymous session GETs: chat's room makes a stored session on every cookieless GET."""
    from django.contrib.sessions.models import Session
    from django.test import Client

    from examples.chat.models import Room

    room = Room.objects.create(name="Lobby")
    with override_settings(**DEMO_STACK):
        statuses = []
        for _ in range(4):
            response = Client(HTTP_X_REAL_IP="203.0.113.20").get(f"/chat/room/{room.pk}/")
            statuses.append(response.status_code)
        assert statuses == [200, 200, 429, 429]
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert Session.objects.count() == LIMITS["SESSIONS_PER_HOUR"]
        # A browser that keeps its cookie makes no new session
        browser = Client(HTTP_X_REAL_IP="203.0.113.21")
        assert [browser.get(f"/chat/room/{room.pk}/").status_code for _ in range(4)] == [200] * 4
        assert Session.objects.count() == LIMITS["SESSIONS_PER_HOUR"] + 1


@pytest.mark.integration
@pytest.mark.django_db
def test_no_session_is_made_past_the_demo_s_rows():
    from django.test import Client

    from examples.chat.models import Room

    room = Room.objects.create(name="Lobby")
    with override_settings(**DEMO_STACK, DEMO_LIMITS={**LIMITS, "SESSIONS_MAX": 1}):
        assert Client(HTTP_X_REAL_IP="203.0.113.22").get(f"/chat/room/{room.pk}/").status_code == 200
        full = Client(HTTP_X_REAL_IP="203.0.113.23").get(f"/chat/room/{room.pk}/")
        assert full.status_code == 503 and full["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.integration
@pytest.mark.django_db
def test_signing_in_replaces_a_session_without_spending_the_budget():
    """login() gives the session a new key and drops the old row: nothing is added, nothing is spent."""
    from django.contrib.sessions.models import Session

    from demo.sessions import SessionStore

    with override_settings(**DEMO_STACK, DEMO_LIMITS={**LIMITS, "SESSIONS_PER_HOUR": 1}):
        token = limits.current_visitor.set("203.0.113.24")
        try:
            store = SessionStore()
            store.create()
            store.cycle_key()
            store.cycle_key()
        finally:
            limits.current_visitor.reset(token)
    assert Session.objects.count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_no_guest_is_made_past_the_demo_s_guests():
    with override_settings(DEMO_LIMITS={**LIMITS, "GUESTS_PER_HOUR": 100, "GUESTS_MAX": 1}):
        factory = RequestFactory()
        first = _signed_in(factory.post("/chat-lv2/sign-in/guest/", HTTP_X_REAL_IP="203.0.113.30"))
        assert sign_in(first, example="chat_lv2", username="guest").status_code == 302
        other = _signed_in(factory.post("/chat-lv2/sign-in/guest/", HTTP_X_REAL_IP="203.0.113.31"))
        response = sign_in(other, example="chat_lv2", username="guest")
        assert response.status_code == 503 and response["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_readiness_says_only_unready_and_logs_why(monkeypatch, caplog):
    from demo import views

    monkeypatch.setattr(views, "get_channel_layer", lambda: None)
    with caplog.at_level("ERROR", logger="demo.ready"):
        response = await views.ready(RequestFactory().get("/ready/"))
    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "unready"}
    assert "no channel layer" in caplog.text


@pytest.mark.unit
def test_the_janitor_resets_early_when_the_database_outgrows_its_budget(monkeypatch):
    from demo.management.commands import demo_janitor

    done = []
    monkeypatch.setattr(demo_janitor, "checkpoint", lambda: done.append("checkpoint"))
    monkeypatch.setattr(demo_janitor, "free_bytes", lambda: 0)
    monkeypatch.setattr(demo_janitor, "reset_and_compact", lambda: done.append("reset"))
    with override_settings(DEMO_DB_MAX_MB=1):
        monkeypatch.setattr(demo_janitor, "database_bytes", lambda: 2**20)
        demo_janitor.keep_within_budget()
        assert done == ["checkpoint"]
        monkeypatch.setattr(demo_janitor, "database_bytes", lambda: 2**20 + 1)
        demo_janitor.keep_within_budget()
        assert done == ["checkpoint", "checkpoint", "reset"]


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_the_janitor_s_checkpoint_and_compaction_run_on_sqlite():
    from demo.management.commands import demo_janitor

    demo_janitor.compact()
    assert demo_janitor.free_bytes() == 0


@pytest.mark.unit
def test_the_janitor_compacts_what_a_trimmed_flood_left_free(monkeypatch):
    from demo.management.commands import demo_janitor

    done = []
    monkeypatch.setattr(demo_janitor, "checkpoint", lambda: None)
    monkeypatch.setattr(demo_janitor, "compact", lambda: done.append("compact"))
    monkeypatch.setattr(demo_janitor, "database_bytes", lambda: 0)
    with override_settings(DEMO_DB_MAX_MB=1):
        monkeypatch.setattr(demo_janitor, "free_bytes", lambda: demo_janitor.FREE_BYTES_TO_COMPACT)
        demo_janitor.keep_within_budget()
        assert done == []
        monkeypatch.setattr(demo_janitor, "free_bytes", lambda: demo_janitor.FREE_BYTES_TO_COMPACT + 1)
        demo_janitor.keep_within_budget()
        assert done == ["compact"]


@pytest.fixture(scope="module")
def collected(tmp_path_factory):
    """The demo's settings in a process of their own, after the image's collectstatic (demo/Dockerfile)."""
    tmp = tmp_path_factory.mktemp("demo-static")
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    env.update(
        DJANGO_SECRET_KEY="a" * 50 + "-not-a-real-key", DEMO_DATA_DIR=str(tmp), DEMO_STATIC_ROOT=str(tmp / "static")
    )

    def manage(*args: str) -> str:
        done = subprocess.run(
            [sys.executable, "demo/manage.py", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    manage("collectstatic", "--noinput", "-v0")
    return tmp / "static", manage


def _shell(manage, script: str) -> str:
    return manage("shell", "--no-imports", "-c", script)


HASHED = r"/[^/]+\.[0-9a-f]{12}\.[A-Za-z0-9]+(\?|$)"


@pytest.mark.integration
def test_the_list_shows_every_example_s_screenshot(collected):
    """#213's webp of each example, collected from examples/<name>/media as a static file."""
    _, manage = collected
    script = "from demo.catalog import EXAMPLES; print(*[f'{e.slug} {e.media}' for e in EXAMPLES], sep='\\n')"
    lines = dict(line.split(" ", 1) for line in _shell(manage, script).splitlines() if line)
    assert len(lines) == 14
    for slug, media in lines.items():
        assert re.fullmatch(rf"/static/demo/media/{slug}\.[0-9a-f]{{12}}\.webp", media), media


@pytest.mark.integration
def test_every_static_file_a_page_names_carries_its_content_s_hash(collected):
    """So Caddy can mark it immutable and Cloudflare keep it at its edge (#214)."""
    _, manage = collected
    script = (
        "from django.template.loader import render_to_string; "
        "from django.templatetags.static import static; "
        "print(render_to_string('wireview_header.html', {'HOOK_FILES': ['hooks/hooks/lifecycle.js']})); "
        "print(static('todo.css'))"
    )
    shown = _shell(manage, script)
    named = re.findall(r'(?:src=")?(/static/[^"\s]+)', shown)
    assert sorted(name.split("?")[0].rsplit("/", 1)[-1].split(".")[0] for name in named) == [
        "lifecycle",
        "todo",
        "wireview",
    ], shown
    for name in named:
        assert re.search(HASHED, name), name


def _caddy_hashed_pattern() -> re.Pattern[str]:
    caddyfile = (ROOT / "demo" / "Caddyfile").read_text(encoding="utf-8")
    pattern = re.search(r"@hashed path_regexp (\S+)", caddyfile)
    assert pattern, "the Caddyfile names hashed files with @hashed"
    return re.compile(pattern.group(1))


def test_caddy_keeps_hashed_files_a_year_and_revalidates_the_rest():
    caddyfile = (ROOT / "demo" / "Caddyfile").read_text(encoding="utf-8")
    assert 'header @hashed Cache-Control "public, max-age=31536000, immutable"' in caddyfile
    assert 'header @unhashed Cache-Control "public, no-cache"' in caddyfile
    hashed = re.search(r"@hashed path_regexp (\S+)", caddyfile)
    assert hashed and f"@unhashed not path_regexp {hashed.group(1)}\n" in caddyfile
    # Pages are not a shared cache's to keep: they carry the session and the CSRF token
    assert 'header ?Cache-Control "private, no-cache"' in caddyfile


def _caddy_directives(caddyfile: str) -> list[str]:
    return [line.strip() for line in caddyfile.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_direct_front_takes_the_visitor_from_the_connection_only():
    """Direct mode (demo/Caddyfile.direct): no header a client sends names the visitor."""
    lines = _caddy_directives((ROOT / "demo" / "Caddyfile.direct").read_text(encoding="utf-8"))
    # Nobody is trusted to name the client: {client_ip} and {remote_host} are the TCP peer, and
    # Caddy writes X-Forwarded-For and X-Forwarded-Proto afresh
    assert not [line for line in lines if line.startswith(("trusted_proxies", "client_ip_headers"))]
    assert "header_up X-Real-IP {remote_host}" in lines
    # Every other header that names a client is dropped, for whatever comes to read it
    for name in ("CF-Connecting-IP", "Forwarded", "True-Client-IP"):
        assert f"header_up -{name}" in lines, name
    # Only to the stack's loopback port, the one compose.yaml opens
    assert [line for line in lines if line.startswith("reverse_proxy")] == [
        "reverse_proxy 127.0.0.1:{$DEMO_DIRECT_PORT:8081} {"
    ]
    assert "{$DEMO_HOST:wireview-demo.itda.work} {" in lines
    assert "import tls_{$DEMO_TLS:acme}" in lines
    assert "protocols h1 h2 h3" in lines
    assert "admin off" in lines


def test_each_front_door_of_the_stack_trusts_its_own_header_only():
    """web's :8080 takes Cloudflare's CF-Connecting-IP, :8081 the host Caddy's X-Real-IP, never both."""
    import yaml

    caddyfile = (ROOT / "demo" / "Caddyfile").read_text(encoding="utf-8")
    servers = dict(re.findall(r"\tservers (:\d+) \{\n(.*?)\n\t\}", caddyfile, re.S))
    assert {port: _caddy_directives(block) for port, block in servers.items()} == {
        ":8080": ["trusted_proxies static private_ranges", "client_ip_headers CF-Connecting-IP"],
        ":8081": ["trusted_proxies static private_ranges", "client_ip_headers X-Real-IP"],
    }
    lines = _caddy_directives(caddyfile)
    # Both sites are the same front, which passes on what the port's rule read
    assert lines[lines.index(":8080 {") :] == [":8080 {", "import front", "}", ":8081 {", "import front", "}"]
    assert "header_up X-Real-IP {client_ip}" in lines
    base = yaml.safe_load((ROOT / "demo" / "compose.yaml").read_text(encoding="utf-8"))
    direct = yaml.safe_load((ROOT / "demo" / "compose.direct.yaml").read_text(encoding="utf-8"))
    # Whoever reaches a port of web's names any visitor: the host's loopback only, in both modes
    assert base["services"]["web"]["ports"] == ["127.0.0.1:${DEMO_PORT:-8080}:8080"]
    assert direct["services"]["web"] == {"ports": ["127.0.0.1:${DEMO_DIRECT_PORT:-8081}:8081"]}
    # The override starts nothing by default but what compose.yaml does
    for name, service in direct["services"].items():
        assert name in base["services"] or service.get("profiles") == ["ddns"], name


def test_the_dns_token_reaches_the_ddns_updater_only():
    """The token that edits the zone's DNS is in ddns.env, which no service but ddns reads."""
    import yaml

    base = yaml.safe_load((ROOT / "demo" / "compose.yaml").read_text(encoding="utf-8"))
    direct = yaml.safe_load((ROOT / "demo" / "compose.direct.yaml").read_text(encoding="utf-8"))
    ddns = direct["services"]["ddns"]
    assert ddns["env_file"] == [{"path": "ddns.env", "required": False}]
    assert not [name for name in ddns.get("environment", {}) if name.startswith("CLOUDFLARE_")]
    # app and janitor read .env, and it no longer has the token's place
    assert base["x-app"]["env_file"] == ".env"
    for name, service in {**base["services"], **direct["services"]}.items():
        if name != "ddns":
            assert "ddns.env" not in json.dumps(service), name
    example = (ROOT / "demo" / ".env.example").read_text(encoding="utf-8")
    assert "CLOUDFLARE_DNS_TOKEN" not in example and "CLOUDFLARE_ZONE_ID" not in example
    ddns_example = (ROOT / "demo" / "ddns.env.example").read_text(encoding="utf-8")
    assert "CLOUDFLARE_DNS_TOKEN=\n" in ddns_example and "CLOUDFLARE_ZONE_ID=\n" in ddns_example
    # Kept off git and out of the image
    assert "ddns.env" in (ROOT / "demo" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "demo/ddns.env" in (ROOT / "demo" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(not shutil.which("caddy"), reason="needs the caddy binary")
@pytest.mark.parametrize("tls", ["acme", "internal"])
def test_the_direct_front_adapts_to_what_its_comments_say(tls, tmp_path):
    """Caddy's own reading of demo/Caddyfile.direct: what the rules above look for is what it runs."""
    env = {**os.environ, "DEMO_HOST": "demo.example", "DEMO_TLS": tls, "XDG_DATA_HOME": str(tmp_path)}
    adapted = subprocess.run(
        ["caddy", "adapt", "--config", str(ROOT / "demo" / "Caddyfile.direct"), "--adapter", "caddyfile"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    config = json.loads(adapted.stdout)
    (server,) = config["apps"]["http"]["servers"].values()
    assert server["listen"] == [":443"]
    assert "trusted_proxies" not in server and "client_ip_headers" not in server
    assert server["protocols"] == ["h1", "h2", "h3"]
    text = json.dumps(server)
    assert '"X-Real-Ip": ["{http.request.remote.host}"]' in text
    assert '"delete": ["CF-Connecting-IP", "Forwarded", "True-Client-IP"]' in text
    assert '"dial": "127.0.0.1:8081"' in text
    policies = config["apps"].get("tls", {}).get("automation", {}).get("policies", [])
    internal = [issuer["module"] for policy in policies for issuer in policy.get("issuers", [])] == ["internal"]
    assert internal == (tls == "internal")


def test_the_proxy_front_believes_the_nas_and_no_one_else():
    """Proxy mode (demo/Caddyfile.proxy): one trusted peer names the visitor, every other peer gets 403."""
    lines = _caddy_directives((ROOT / "demo" / "Caddyfile.proxy").read_text(encoding="utf-8"))
    # One address, the NAS, given as the import's argument (Homebrew's service passes no environment).
    # No default that would trust a range: imported without it, Caddy refuses to start
    nas = "{args[0]}"
    assert [line for line in lines if line.startswith("trusted_proxies")] == [f"trusted_proxies static {nas}"]
    assert [line for line in lines if line.startswith("client_ip_headers")] == ["client_ip_headers X-Real-IP"]
    # Whoever else reaches the port could write any X-Real-IP: refused on the TCP peer
    assert f"@outsider not remote_ip {nas}" in lines
    assert "respond @outsider 403 {" in lines
    # The servers block is the site's own port
    assert "servers :{$DEMO_PROXY_PORT:8091} {" in lines
    assert ":{$DEMO_PROXY_PORT:8091} {" in lines
    assert "header_up X-Real-IP {client_ip}" in lines
    for name in ("CF-Connecting-IP", "Forwarded", "True-Client-IP"):
        assert f"header_up -{name}" in lines, name
    assert [line for line in lines if line.startswith("reverse_proxy")] == [
        "reverse_proxy 127.0.0.1:{$DEMO_DIRECT_PORT:8081} {"
    ]
    # The README's service Caddyfile gives the NAS's address
    readme = (ROOT / "demo" / "README.md").read_text(encoding="utf-8")
    assert 'echo "import $HOME/srv/django-wireview/demo/Caddyfile.proxy 192.168.10.102"' in readme
    # Plain HTTP from the NAS: no certificate here, and so no HTTP/3
    assert not [line for line in lines if line.startswith(("tls", "protocols", "import"))]
    assert "admin off" in lines


def _proxy_config(tmp_path: Path, *nas: str) -> Path:
    """The one-line Caddyfile that Homebrew's service reads (demo/README.md "NAS 리버스 프록시 뒤")."""
    wrapper = tmp_path / f"Caddyfile-{len(list(tmp_path.glob('Caddyfile-*')))}"
    wrapper.write_text(" ".join(["import", str(ROOT / "demo" / "Caddyfile.proxy"), *nas]) + "\n", encoding="utf-8")
    return wrapper


@pytest.mark.skipif(not shutil.which("caddy"), reason="needs the caddy binary")
def test_the_proxy_front_does_not_start_without_its_nas(tmp_path):
    checked = subprocess.run(
        ["caddy", "validate", "--config", str(_proxy_config(tmp_path)), "--adapter", "caddyfile"],
        env={**os.environ, "XDG_DATA_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert checked.returncode != 0
    assert "{args[0]}" in checked.stderr


@pytest.mark.skipif(not shutil.which("caddy"), reason="needs the caddy binary")
def test_the_proxy_front_adapts_to_what_its_comments_say(tmp_path):
    adapted = subprocess.run(
        ["caddy", "adapt", "--config", str(_proxy_config(tmp_path, "192.168.10.102")), "--adapter", "caddyfile"],
        env={**os.environ, "XDG_DATA_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    config = json.loads(adapted.stdout)
    assert config["admin"] == {"disabled": True}
    (server,) = config["apps"]["http"]["servers"].values()
    assert server["listen"] == [":8091"]
    assert server["trusted_proxies"] == {"source": "static", "ranges": ["192.168.10.102"]}
    assert server["client_ip_headers"] == ["X-Real-IP"]
    assert "protocols" not in server and "tls" not in config["apps"]
    refusal, proxy = server["routes"][1:]
    assert refusal == {
        "match": [{"not": [{"remote_ip": {"ranges": ["192.168.10.102"]}}]}],
        "handle": [{"handler": "static_response", "status_code": 403, "close": True}],
    }
    text = json.dumps(proxy)
    assert '"X-Real-Ip": ["{http.vars.client_ip}"]' in text
    assert '"delete": ["CF-Connecting-IP", "Forwarded", "True-Client-IP"]' in text
    assert '"dial": "127.0.0.1:8081"' in text


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def proxy_front(tmp_path):
    """demo/Caddyfile.proxy running, in front of a web that answers with the headers it got.

    Yields a function that starts Caddy trusting the given NAS address and returns its port.
    """
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Web(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({name.lower(): self.headers.get_all(name) for name in self.headers}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    web = ThreadingHTTPServer(("127.0.0.1", 0), Web)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    caddies: list[subprocess.Popen] = []

    def start(nas: str) -> int:
        port = _free_port()
        env = {
            **os.environ,
            "DEMO_PROXY_PORT": str(port),
            "DEMO_DIRECT_PORT": str(web.server_address[1]),
            "XDG_DATA_HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path),
        }
        caddy = subprocess.Popen(
            ["caddy", "run", "--config", str(_proxy_config(tmp_path, nas)), "--adapter", "caddyfile"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        caddies.append(caddy)
        deadline = time.monotonic() + 15
        while True:
            assert caddy.poll() is None, "caddy exited"
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                return port
            except urllib.error.HTTPError:
                return port
            except OSError:
                assert time.monotonic() < deadline, "caddy did not listen"
                time.sleep(0.05)

    yield start
    for caddy in caddies:
        caddy.terminate()
        caddy.wait(timeout=10)
    web.shutdown()
    web.server_close()


def _through(port: int, headers: dict[str, str]) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"http://127.0.0.1:{port}/", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as refused:
        return refused.code, {}


FORGED = {
    "CF-Connecting-IP": "8.8.8.8",
    "Forwarded": "for=7.7.7.7",
    "True-Client-IP": "6.6.6.6",
}


@pytest.mark.skipif(not shutil.which("caddy"), reason="needs the caddy binary")
def test_the_proxy_front_passes_on_the_visitor_the_nas_names(proxy_front):
    # This test is the NAS: Caddy trusts its address, 127.0.0.1
    port = proxy_front("127.0.0.1")
    status, seen = _through(port, {"X-Real-IP": "203.0.113.7", "X-Forwarded-Proto": "https", **FORGED})
    assert status == 200
    assert seen["x-real-ip"] == ["203.0.113.7"]
    # Django's SECURE_PROXY_SSL_HEADER reads what the NAS said
    assert seen["x-forwarded-proto"] == ["https"]
    for name in FORGED:
        assert name.lower() not in seen, name
    # A NAS that names no one names itself, never a header the visitor could have written
    status, seen = _through(port, {})
    assert (status, seen["x-real-ip"]) == (200, ["127.0.0.1"])


@pytest.mark.skipif(not shutil.which("caddy"), reason="needs the caddy binary")
def test_the_proxy_front_refuses_every_peer_but_the_nas(proxy_front):
    # The NAS is elsewhere (TEST-NET-1); this test is another machine on the LAN, forging the header
    port = proxy_front("192.0.2.1")
    assert _through(port, {"X-Real-IP": "192.0.2.1", "X-Forwarded-Proto": "https"}) == (403, {})


class _Answer:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class _Cloudflare:
    """The trace endpoint and the DNS records API, as demo/ddns.py calls them."""

    def __init__(self, line: str, records: list[dict]):
        self.line = line
        self.records = records
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request, timeout):
        from demo import ddns

        if request == ddns.TRACE:
            return _Answer(f"fl=1\nh=1.1.1.1\n{self.line}\nts=1\n".encode())
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, body))
        assert request.headers["Authorization"] == "Bearer token"
        if request.get_method() == "GET":
            return _Answer(json.dumps({"success": True, "result": self.records}).encode())
        return _Answer(json.dumps({"success": True, "result": {}}).encode())


def test_ddns_changes_only_the_existing_a_record_s_address():
    from demo import ddns

    record = {"id": "r1", "type": "A", "content": "121.159.177.248", "proxied": False}
    api = _Cloudflare("ip=121.159.177.9", [record])
    said = ddns.sync("wireview-demo.itda.work", "zone", "token", opener=api)
    assert said == "wireview-demo.itda.work moved from 121.159.177.248 to 121.159.177.9"
    (get, patch) = api.calls
    assert get[0] == "GET" and "type=A" in get[1] and "name=wireview-demo.itda.work" in get[1]
    # The content and nothing else: never the proxy, the name or the TTL
    assert patch == ("PATCH", ddns.API + "/zones/zone/dns_records/r1", {"content": "121.159.177.9"})

    same = _Cloudflare("ip=121.159.177.248", [record])
    assert ddns.sync("wireview-demo.itda.work", "zone", "token", opener=same) == (
        "wireview-demo.itda.work is 121.159.177.248"
    )
    assert [method for method, _, _ in same.calls] == ["GET"]


@pytest.mark.parametrize(
    "line, records, refusal",
    [
        # No A record: the tunnel's CNAME is still there, or nobody made one. Nothing is created
        ("ip=121.159.177.9", [], "has 0 A records"),
        ("ip=121.159.177.9", [{"id": "a", "content": "1.1.1.1"}, {"id": "b", "content": "1.1.1.2"}], "2 A records"),
        ("ip=2001:db8::1", [{"id": "a", "content": "1.1.1.1"}], "IPv6"),
        ("ip=192.168.10.197", [{"id": "a", "content": "1.1.1.1"}], "non-public"),
        ("warp=off", [{"id": "a", "content": "1.1.1.1"}], "no ip= line"),
    ],
)
def test_ddns_writes_nothing_it_is_not_sure_of(line, records, refusal):
    from demo import ddns

    api = _Cloudflare(line, records)
    with pytest.raises((RuntimeError, ValueError), match=refusal):
        ddns.sync("wireview-demo.itda.work", "zone", "token", opener=api)
    assert [method for method, _, _ in api.calls if method != "GET"] == []


@pytest.mark.integration
def test_caddy_s_pattern_tells_the_collected_hashed_names_from_the_rest(collected):
    static_root, _ = collected
    manifest = json.loads((static_root / "staticfiles.json").read_text(encoding="utf-8"))["paths"]
    pattern = _caddy_hashed_pattern()
    assert len(manifest) > 20
    for original, hashed in manifest.items():
        assert pattern.search("/" + hashed), hashed
        assert not pattern.search("/" + original), original
    assert not pattern.search("/staticfiles.json")


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


class _Clock:
    """Time a test moves by hand: the limits' clocks and the cache's expiry read it."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def moved_time(monkeypatch):
    from types import SimpleNamespace

    from django.core.cache.backends import base, locmem

    clock = _Clock()
    monkeypatch.setattr(limits, "clock", clock)
    monkeypatch.setattr(limits, "wall", clock)
    monkeypatch.setattr(locmem, "time", SimpleNamespace(time=clock))
    monkeypatch.setattr(base, "time", SimpleNamespace(time=clock))
    return clock


def _production_limits(monkeypatch) -> dict:
    """DEMO_LIMITS as demo/settings.py sets them, its defaults and nothing from .env."""
    import importlib

    for name in [name for name in os.environ if name.startswith("DEMO_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("DJANGO_SECRET_KEY", "a" * 50 + "-not-a-real-key")
    module = importlib.import_module("demo.settings")
    return importlib.reload(module).DEMO_LIMITS


class _Typing(_Socket):
    """A page someone types into: search's event and the address it patched, every ``every`` seconds."""

    def __init__(self, clock: _Clock, inputs: int, every: float, with_event: bool = True):
        frames = []
        for i in range(inputs):
            q = "x" * (i % 60 + 1)
            if with_event:
                frames.append(_event(q=q))
            frames.append(_params(q))
        super().__init__(*frames)
        self.clock = clock
        self.every = every
        self.per_input = 2 if with_event else 1
        self.read = 0

    async def receive(self):
        message = await super().receive()
        if message["type"] == "websocket.receive":
            if self.read and self.read % self.per_input == 0:
                self.clock.now += self.every
            self.read += 1
        return message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_someone_typing_into_search_for_five_minutes_is_never_cut_off(monkeypatch, moved_time):
    """The second review's typing: .debounce.300 sends the event, the patched address a params_changed.

    At 350 ms a keystroke the old budget closed the socket after 27 seconds; with the demo's own
    limits, five minutes and more pass.
    """
    inputs = int(6 * 60 / 0.35)
    with override_settings(DEMO_LIMITS=_production_limits(monkeypatch)):
        socket = _Typing(moved_time, inputs, every=0.35)
        await socket.run()
    assert _closed_with(socket) is None
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == 2 * inputs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_params_changed_no_event_asked_for_at_the_same_pace_still_closes(monkeypatch, moved_time):
    with override_settings(DEMO_LIMITS=_production_limits(monkeypatch)):
        socket = _Typing(moved_time, 200, every=0.35, with_event=False)
        await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_event_pays_for_the_address_it_patched_and_no_more():
    """One event, then three params_changed: the first is the event's, the next RENDER_BURST spend renders."""
    socket = _Socket(_event(), *[_params(str(i)) for i in range(1 + LIMITS["RENDER_BURST"] + 1)])
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == 1 + 1 + LIMITS["RENDER_BURST"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_killed_workers_sockets_lapse_and_their_places_come_back(moved_time):
    """The second review's worker crash: SIGKILL skips every release. The places lapse on their own."""
    with override_settings(DEMO_LIMITS={**LIMITS, "SOCKETS_PER_MINUTE": 100}):
        # What a killed worker leaves: leases taken, never given back
        for member in ("killed-1", "killed-2"):
            assert limits.Leases.take({"visitor:203.0.113.7": 2, "all": 3}, member) is None
        refused = _Socket()
        await refused.run()
        assert refused.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
        moved_time.now += limits.LEASE_SECONDS - 1
        still = _Socket()
        await still.run()
        assert still.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
        moved_time.now += 2
        again = _Socket()
        await again.run()
        assert again.sent[0] == {"type": "websocket.accept"}
        # And the set holds the live ones only: none, once that socket closed
        assert limits.Leases.held("visitor:203.0.113.7") == 0 and limits.Leases.held("all") == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_open_socket_renews_its_place_and_keeps_it(monkeypatch, moved_time):
    monkeypatch.setattr(limits, "RENEW_SECONDS", 0.01)
    with override_settings(DEMO_LIMITS={**LIMITS, "SOCKETS_PER_MINUTE": 100, "OPEN_SOCKETS_PER_VISITOR": 1}):
        held = _Holding()
        task = asyncio.create_task(held.run())
        await eventually(lambda: held.sent)
        # Time passes the lease's length while the socket is open, and it renews
        moved_time.now += limits.LEASE_SECONDS * 3
        await eventually(lambda: limits.Leases.held("visitor:203.0.113.7") == 1)
        refused = _Socket()
        await refused.run()
        assert refused.sent == [{"type": "websocket.close", "code": limits.TOO_MANY}]
        held.done.set()
        await task
        assert limits.Leases.held("visitor:203.0.113.7") == 0


@pytest.fixture
def limits_redis(tmp_path):
    """A Redis of the test's own as the cache, configured as compose's ``limits`` is."""
    import shutil
    import socket as socket_
    import time

    from redis import Redis
    from testproj.time_limit import disown, own

    binary = shutil.which("redis-server") or next(
        (path for path in ("/opt/homebrew/bin/redis-server", "/usr/local/bin/redis-server") if Path(path).exists()),
        None,
    )
    if binary is None:
        pytest.skip("redis-server is not installed")
    with socket_.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = own(
        subprocess.Popen(
            [binary, "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(tmp_path),
             "--maxmemory", "32mb", "--maxmemory-policy", "noeviction"],
            stdout=subprocess.DEVNULL,
        )
    )  # fmt: skip
    client = Redis(port=port)
    try:
        for _ in range(100):
            try:
                client.ping()
                break
            except Exception:
                time.sleep(0.05)
        location = f"redis://127.0.0.1:{port}/0"
        with override_settings(
            CACHES={
                "default": {
                    "BACKEND": "django.core.cache.backends.redis.RedisCache",
                    "LOCATION": location,
                    "KEY_PREFIX": "demo",
                }
            }
        ):
            yield client
    finally:
        process.terminate()
        process.wait(timeout=10)
        disown(process)


@pytest.mark.integration
def test_in_redis_no_window_outlives_itself(limits_redis):
    assert [limits.count("t", "v", 5) for _ in range(3)] == [1, 2, 3]
    keys = limits_redis.keys("demo:1:limit:*")
    assert keys and all(0 < limits_redis.ttl(key) <= 5 for key in keys)


@pytest.mark.integration
def test_in_redis_leases_count_the_live_and_leave_no_key_behind(limits_redis, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(limits, "wall", clock)
    places = {"visitor:v": 2, "all": 3}
    assert limits.Leases.take(places, "a") is None
    assert limits.Leases.take(places, "b") is None
    assert limits.Leases.take(places, "c") == "visitor:v"
    assert limits.Leases.take({"visitor:w": 2, "all": 3}, "d") is None
    assert limits.Leases.take({"visitor:x": 2, "all": 3}, "e") == "all"
    keys = limits_redis.keys("demo:1:limit:open:*")
    assert len(keys) == 3 and all(0 < limits_redis.ttl(key) <= limits.LEASE_SECONDS for key in keys)
    # The review's zero keys: a visitor whose sockets all closed holds no key at all
    limits.Leases.release(["visitor:w", "all"], "d")
    assert not limits_redis.exists("demo:1:limit:open:visitor:w")
    # A killed worker's leases lapse; a live socket's renewal keeps its place
    clock.now += limits.LEASE_SECONDS / 2
    limits.Leases.renew(["visitor:v", "all"], "a")
    clock.now += limits.LEASE_SECONDS / 2 + 1
    assert limits.Leases.held("visitor:v") == 1
    assert limits.Leases.take(places, "f") is None
    assert limits.Leases.held("all") == 2


@pytest.mark.integration
def test_a_full_limits_redis_refuses_with_its_own_answer(limits_redis):
    limits.count("t", "v", 60)
    limits_redis.config_set("maxmemory", 1)
    try:
        response = _through_middleware(RequestFactory().get("/", HTTP_X_REAL_IP="203.0.113.9"))
    finally:
        limits_redis.config_set("maxmemory", 32 * 2**20)
    assert response.status_code == 503
    assert "more visitors than it can keep track of" in response.content.decode()


@pytest.mark.integration
def test_the_janitor_raises_the_alarm_when_the_limits_redis_fills(limits_redis, tmp_path, caplog):
    from demo.management.commands import demo_janitor

    with override_settings(DATA_DIR=tmp_path):
        assert demo_janitor.watch_limits() is False
        assert not demo_janitor.limits_alarm_file().exists()
        # A key that lost its expiry, the way the review's counters could not before
        limits_redis.set("demo:1:limit:stray:v", 1)
        used = limits_redis.info("memory")["used_memory"]
        limits_redis.config_set("maxmemory", int(used / 0.85))
        with caplog.at_level("ERROR", logger="demo.janitor"):
            assert demo_janitor.watch_limits() is True
        assert "of its memory" in caplog.text
        assert demo_janitor.limits_alarm_file().exists()
        assert not limits_redis.exists("demo:1:limit:stray:v")
        limits_redis.config_set("maxmemory", 32 * 2**20)
        assert demo_janitor.watch_limits() is False
        assert not demo_janitor.limits_alarm_file().exists()


@pytest.mark.unit
def test_a_full_counter_store_says_so(monkeypatch):
    from redis.exceptions import OutOfMemoryError

    def full(*args, **kwargs):
        # What redis-py raises for Redis's "OOM command not allowed ...": the "OOM" is gone
        raise OutOfMemoryError("command not allowed when used memory > 'maxmemory'.")

    monkeypatch.setattr(limits.cache, "incr", full)
    response = _through_middleware(RequestFactory().get("/", HTTP_X_REAL_IP="203.0.113.9"))
    assert response.status_code == 503
    assert "more visitors than it can keep track of" in response.content.decode()


@pytest.fixture
def retention():
    from demo import retention

    retention.connect()
    try:
        with override_settings(DEMO_KEEP_PER_ROOM=3, DEMO_ROWS_MAX=5):
            yield retention
    finally:
        retention.disconnect()


@pytest.mark.integration
@pytest.mark.django_db
def test_a_room_keeps_its_newest_messages(retention):
    from examples.chat.models import Message, Room

    room, other = Room.objects.create(name="a"), Room.objects.create(name="b")
    for i in range(5):
        Message.objects.create(room=room, username="x", content=str(i))
    Message.objects.create(room=other, username="x", content="other")
    assert list(Message.objects.filter(room=room).values_list("content", flat=True)) == ["2", "3", "4"]
    assert Message.objects.filter(room=other).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_a_table_past_its_rows_refuses_a_new_one_politely(retention):
    from examples.chat.models import Room

    for i in range(5):
        Room.objects.create(name=str(i))
    with pytest.raises(retention.StorageFull):
        Room.objects.create(name="sixth")
    # Changing a row that is there is not a new one
    room = Room.objects.first()
    room.name = "renamed"
    room.save()
    response = limits.RequestLimitMiddleware(lambda request: HttpResponse())
    answer = response.process_exception(RequestFactory().post("/chat/"), retention.StorageFull("chat.Room"))
    assert answer.status_code == 503 and answer["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.integration
@pytest.mark.django_db
def test_the_janitor_trims_what_reached_the_database_without_a_signal(retention):
    from examples.chat.models import Message, Room
    from examples.todo.models import Item

    room = Room.objects.create(name="a")
    Message.objects.bulk_create(Message(room=room, username="x", content=str(i)) for i in range(6))
    Item.objects.bulk_create(Item(text=str(i)) for i in range(4))
    with override_settings(DEMO_KEEP_PER_ROOM=3):
        assert retention.trim_all() == 3
    assert Message.objects.count() == 3
    assert Item.objects.count() == 4  # todo keeps its newest 100


@pytest.mark.integration
@pytest.mark.django_db
def test_the_seed_makes_no_accounts_and_names_no_one_real():
    from django.contrib.auth import get_user_model

    from demo import seed

    seed.reset()
    assert get_user_model().objects.count() == 0
    assert all("-" in person for person in seed.ACTIVITY_PEOPLE)
