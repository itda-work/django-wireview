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


def _join(state: str) -> str:
    return json.dumps({"command": "join", "payload": {"name": "X", "state": state}})


def _closed_with(socket: _Socket) -> int | None:
    codes = [m["code"] for m in socket.sent if m["type"] == "websocket.close"]
    return codes[0] if codes else None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_pages_first_joins_pass_and_do_not_spend_the_event_bucket():
    """A page joins each of its components once: a burst of new states is the page loading."""
    socket = _Socket(*[_join(f"state-{i}") for i in range(LIMITS["JOIN_BURST"])], _event())
    await socket.run()
    assert _closed_with(socket) is None
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == LIMITS["JOIN_BURST"] + 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_joining_the_same_state_again_and_again_spends_the_render_bucket():
    """The review's join flood: one valid state joined 60 times, each a restore and a render."""
    socket = _Socket(*[_join("the-same")] * 60)
    await socket.run()
    assert _closed_with(socket) == limits.TOO_MANY
    # The first join, then RENDER_BURST rejoins
    assert len([m for m in socket.heard if m["type"] == "websocket.receive"]) == 1 + LIMITS["RENDER_BURST"]


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


@pytest.mark.integration
def test_the_list_shows_every_example_s_screenshot(tmp_path):
    """#213's webp of each example, collected from examples/<name>/media as a static file."""
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    env.update(DJANGO_SECRET_KEY="a" * 50 + "-not-a-real-key", DEMO_DATA_DIR=str(tmp_path))
    script = "from demo.catalog import EXAMPLES; print(*[f'{e.slug} {e.media}' for e in EXAMPLES], sep='\\n')"
    shown = subprocess.run(
        [sys.executable, "demo/manage.py", "shell", "--no-imports", "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert shown.returncode == 0, shown.stderr
    lines = dict(line.split(" ", 1) for line in shown.stdout.splitlines() if line)
    assert len(lines) == 14
    assert lines == {slug: f"/static/demo/media/{slug}.webp" for slug in lines}


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
