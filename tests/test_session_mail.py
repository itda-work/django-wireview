"""A session's mail to itself survives a channel layer that drops it (#211).

A session mails itself (``WireviewMeta.send`` -> ``message_from_component``) to
have something done in its turn: a stream operation, a ``push_event``, the
``joined`` that starts infinite scroll, the release of the Broadcast patches a
join holds, a deferred handler, a crashed task's recovery. The mail rides on the
connection's channel, which it shares with every notification its topics fan
out -- and a full channel drops it. Here the channel is filled for real: an
InMemory layer of ``CAPACITY`` messages, filled with notifications for the
component's topic by the very handler that then mails. Every
mail still reaches the page, once, and in its turn: after the handler, before
the notifications that filled the channel.

channels_redis can also drop a mail without a word -- a full receive buffer
loses its oldest message -- and a mail may be very late. A mail that a later
one overtook, or that stayed away while nothing was being handled, is handled
from the copy the session kept; the layer's copy, if it comes after all, is
dropped (``transport.Mailbox``).
"""

from __future__ import annotations

import asyncio
import typing as t
from dataclasses import dataclass

import pytest
from channels.exceptions import ChannelFull
from channels.layers import InMemoryChannelLayer, get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.template import Template, engines
from testproj.waiting import eventually

from wireview import JS, Broadcast, Component
from wireview.consumer import WireviewConsumer
from wireview.core import transport
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.core.transport import ChannelsBroker, Mailbox
from wireview.utils import _message

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

#: Every channel holds this many messages; the handler's burst fills its own
CAPACITY = 3

TEMPLATES = {
    "sm/box.html": '{% load wireview %}<div {% tag_header %}><ul wire-stream="items"></ul></div>',
    "sm/box_item.html": '<li id="items-{{ item.pk }}">{{ item.text }}</li>',
}


@dataclass
class Post:
    pk: int
    text: str


class SmBox(Component):
    """Fills its own channel with notifications, then mails."""

    heard: int = 0

    class Meta:
        template_name = "sm/box.html"
        subscriptions = {"sm-room"}

    @classmethod
    def _get_template(cls, template_name=None):
        if template_name is None:
            return Template(TEMPLATES[cls._meta.template_name])  # type: ignore[attr-defined]
        return engines["django"].from_string(TEMPLATES[template_name])

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.heard += 1
        await self.push_event("heard", {"n": self.heard})

    async def _fill(self) -> None:
        await _fill(self.wire.channel_name)

    async def push(self, **_rest):
        await self._fill()
        await self.push_event("pushed", {"x": 1})

    async def flash(self, **_rest):
        await self._fill()
        await self.put_flash("info", "after the burst")

    async def title(self, **_rest):
        await self._fill()
        await self.wire.push_title("Burst")

    async def js(self, **_rest):
        await self._fill()
        await self.push_js(JS().add_class("#x", "seen"))

    async def insert(self, **_rest):
        await self._fill()
        await self.stream_insert("items", Post(1, "one"))

    async def focus(self, **_rest):
        await self._fill()
        await self.focus_on("#x")

    async def navigate(self, **_rest):
        await self._fill()
        await self.wire.push_to("/elsewhere/")

    async def go(self, **_rest):
        await self._fill()
        await self.defer(self.bump)

    async def bump(self, **_rest):
        await self.push_event("bumped")

    async def two(self, **_rest):
        await self._fill()
        await self.push_event("first")
        await self.push_event("second")
        # Still handling: what the channel refused waits for this to end
        await asyncio.sleep(0.05)

    async def hold(self, **_rest):
        # Handling this, the connection takes nothing from its channel
        await HELD["until"].wait()

    async def crash(self, **_rest):
        await self.start_async("boom", self._boom())

    async def _boom(self):
        await self._fill()

    async def handle_async(self, name: str, result: t.Any) -> None:
        raise RuntimeError("handle_async went wrong with its channel full")


class SmJoining(SmBox):
    """Fills its channel in joined(): the join's own mail -- its stream, the patch release, ``joined`` -- follows."""

    class Meta:
        template_name = "sm/box.html"
        subscriptions = {"sm-room", "sm-feed"}

    async def joined(self):
        await self._fill()
        await self.stream("items", [Post(1, "one")], reset=True)


#: What ``SmBox.hold`` waits for
HELD: dict[str, asyncio.Event] = {}

#: How many notifications the last ``_fill`` put in the channel
FILLED = {"n": 0}


async def _fill(channel: str) -> None:
    """What a busy room's notifications do: wait in the connection's channel until it takes no more.

    The connection may be waiting on the channel, and takes one off as soon as
    it runs: filled again until a round fits nothing.
    """
    layer = get_channel_layer()
    FILLED["n"] = 0
    for _ in range(CAPACITY + 2):
        added = 0
        while True:
            try:
                await layer.send(channel, _message("sm-room", "notification", {"kwargs": {}}))
            except ChannelFull:
                break
            added += 1
        FILLED["n"] += added
        if not added:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the channel never filled")


def _state(cls: type[Component], id: str) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))


@pytest.fixture
def small_channels(settings):
    """Every channel holds CAPACITY messages. Channels drops its cached layer on the change."""
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer", "CONFIG": {"capacity": CAPACITY}}
    }


async def _connect(user: t.Any = None) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = user or AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _until(communicator: WebsocketCommunicator, last: t.Callable[[dict[str, t.Any]], bool]):
    heard = [await communicator.receive_json_from(timeout=3)]
    while not last(heard[-1]):
        heard.append(await communicator.receive_json_from(timeout=3))
    return heard


async def _join(communicator: WebsocketCommunicator, cls: type[Component], id: str) -> list[dict[str, t.Any]]:
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": cls.__name__, "state": _state(cls, id), "children": {}}}
    )
    return await _until(communicator, lambda m: m["command"] == "joined" and m["payload"]["id"] == id)


async def _event(communicator: WebsocketCommunicator, id: str, handler: str) -> None:
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {"id": id, "command": handler, "implicit_args": {}, "explicit_args": {}, "ref": 1},
        }
    )


def _pushed(event: str) -> t.Callable[[dict[str, t.Any]], bool]:
    return lambda m: m["command"] == "push_event" and m["payload"]["event"] == event


def _heard_all(frame: dict[str, t.Any]) -> bool:
    return _pushed("heard")(frame) and frame["payload"]["payload"]["n"] == FILLED["n"]


MAILED = [
    ("push", _pushed("pushed")),
    ("flash", lambda m: m["command"] == "flash" and m["payload"]["message"] == "after the burst"),
    ("title", lambda m: m == {"command": "title", "payload": {"title": "Burst"}}),
    ("js", lambda m: m["command"] == "exec_js" and m["payload"]["id"] == "box"),
    ("insert", lambda m: m["command"] == "stream_op" and m["payload"]["op"] == "insert"),
    ("focus", lambda m: m == {"command": "focus_on", "payload": {"selector": "#x"}}),
    ("navigate", lambda m: m["command"] == "url_change" and m["payload"]["url"] == "/elsewhere/"),
    # dispatch_event: the deferred handler runs
    ("go", _pushed("bumped")),
]


@pytest.mark.parametrize(("handler", "mailed"), MAILED, ids=[handler for handler, _ in MAILED])
async def test_what_a_handler_mails_into_its_full_channel_reaches_the_page_after_it(small_channels, handler, mailed):
    communicator = await _connect()
    try:
        await _join(communicator, SmBox, "box")
        await _event(communicator, "box", handler)
        frames = await _until(communicator, _heard_all)
    finally:
        await communicator.disconnect()

    answer = next(i for i, m in enumerate(frames) if m["command"] == "render" and m["payload"].get("ref") == 1)
    sent = [i for i, m in enumerate(frames) if mailed(m)]
    heard = [i for i, m in enumerate(frames) if _pushed("heard")(m)]
    # Once, in its turn: after the handler's answer, ahead of the notifications that filled the channel
    assert len(sent) == 1, frames
    assert answer < sent[0] < heard[0], frames
    # Every one that fit was heard
    assert [frames[i]["payload"]["payload"]["n"] for i in heard] == list(range(1, FILLED["n"] + 1))


async def test_mails_the_full_channel_dropped_keep_the_order_they_were_sent_in(small_channels):
    communicator = await _connect()
    try:
        await _join(communicator, SmBox, "box")
        await _event(communicator, "box", "two")
        frames = await _until(communicator, _heard_all)
    finally:
        await communicator.disconnect()

    events = [m["payload"].get("event") or m["command"] for m in frames if m["command"] in ("push_event", "render")]
    # The handler's answer first: what it mailed is handled in a turn of its own, after it
    assert events[events.index("first") - 1] == "render"
    assert [event for event in events if event != "render"] == ["first", "second", *["heard"] * FILLED["n"]]


async def test_a_join_whose_joined_fills_its_channel_still_says_joined_and_lets_its_patches_through(small_channels):
    """``joined()``'s stream, the release of the held Broadcast patches and ``joined`` are all mail (#178, #112)."""
    communicator = await _connect()
    try:
        joined = await _join(communicator, SmJoining, "joining")
        await Broadcast(SmJoining, "sm-feed").push_event("patched").asend()
        # Written at once: the hold was let go, not left for HOLD_SECONDS to close the socket
        later = await _until(communicator, _pushed("patched"))
    finally:
        await communicator.disconnect()

    commands = [m["command"] for m in joined]
    assert commands.index("render") < commands.index("stream_op") < commands.index("joined")
    assert not [m for m in later if m["command"] == "stream_op"]


async def test_a_task_that_raises_into_its_full_channel_is_recovered(small_channels):
    """The ``crashed`` mail of ``start_async``: the page is told to join it again (#94)."""
    communicator = await _connect()
    try:
        await _join(communicator, SmBox, "box")
        await _event(communicator, "box", "crash")
        frames = await _until(communicator, lambda m: m["command"] == "error" and m["payload"].get("id") == "box")
    finally:
        await communicator.disconnect()

    assert frames[-1]["command"] == "error"


async def test_a_template_change_mailed_into_a_full_channel_still_rejoins_the_page(small_channels, settings):
    """``template_changed`` (#180) is the session's own mail too."""
    from wireview.core import template_reload

    settings.WIREVIEW = {**getattr(settings, "WIREVIEW", {}), "REJOIN_ON_TEMPLATE_CHANGE": True}
    communicator = await _connect()
    try:
        await _join(communicator, SmBox, "box")
        await _event(communicator, "box", "push")
        await _until(communicator, _heard_all)
        session = next(iter(template_reload._sessions))  # type: ignore[attr-defined]
        HELD["until"] = asyncio.Event()
        await _event(communicator, "box", "hold")
        await eventually(lambda: session.handling)
        await _fill(session.channel_name)
        await session.templates_changed()
        HELD["until"].set()
        frames = await _until(communicator, lambda m: m["command"] == "rejoin")
    finally:
        await communicator.disconnect()

    assert frames[-1] == {"command": "rejoin", "payload": {}}


# --- what a layer drops without a word, or brings late ---------------------------------------


class _Quiet:
    """``ChannelsBroker.send_to_session`` that loses or delays the session mail it is told to, saying nothing."""

    def __init__(self, monkeypatch, *, lose: set[str] = frozenset(), delay: dict[str, asyncio.Event] | None = None):
        self.lose = set(lose)
        self.delay = delay or {}
        self.late: list[asyncio.Task[None]] = []
        original = ChannelsBroker.send_to_session
        quiet = self

        async def send_to_session(broker, session_id, message):
            event = message.get("kwargs", {}).get("event")
            if event in quiet.lose:
                quiet.lose.discard(event)
                return True  # as channels_redis's receive buffer: taken, then lost
            if event in quiet.delay:
                gate = quiet.delay.pop(event)

                async def later():
                    await gate.wait()
                    await original(broker, session_id, message)

                quiet.late.append(asyncio.ensure_future(later()))
                return True
            return await original(broker, session_id, message)

        monkeypatch.setattr(ChannelsBroker, "send_to_session", send_to_session)


class SmQuiet(SmBox):
    async def two(self, **_rest):
        await self.push_event("first")
        await self.push_event("second")

    async def one(self, **_rest):
        await self.push_event("only")

    async def sentinel(self, **_rest):
        await self.push_event("sentinel")


async def test_a_mail_the_layer_lost_quietly_is_handled_when_a_later_one_overtakes_it(monkeypatch):
    _Quiet(monkeypatch, lose={"first"})
    communicator = await _connect()
    try:
        await _join(communicator, SmQuiet, "box")
        await _event(communicator, "box", "two")
        frames = await _until(communicator, _pushed("second"))
    finally:
        await communicator.disconnect()

    assert [m["payload"]["event"] for m in frames if m["command"] == "push_event"] == ["first", "second"]


async def test_a_mail_the_layer_lost_quietly_is_handled_once_nothing_else_is(monkeypatch):
    monkeypatch.setattr(transport, "MAIL_SECONDS", 0.2)
    _Quiet(monkeypatch, lose={"only"})
    communicator = await _connect()
    try:
        await _join(communicator, SmQuiet, "box")
        await _event(communicator, "box", "one")
        frames = await _until(communicator, _pushed("only"))
    finally:
        await communicator.disconnect()

    assert frames[-1]["payload"]["event"] == "only"


async def test_a_mail_handled_here_is_not_handled_again_when_the_layer_brings_it_late(monkeypatch):
    monkeypatch.setattr(transport, "MAIL_SECONDS", 0.2)
    gate = asyncio.Event()
    quiet = _Quiet(monkeypatch, delay={"only": gate})
    communicator = await _connect()
    try:
        await _join(communicator, SmQuiet, "box")
        await _event(communicator, "box", "one")
        await _until(communicator, _pushed("only"))
        gate.set()
        await eventually(lambda: quiet.late and quiet.late[0].done())
        # Behind the late copy in the channel: once this is out, that was handled
        await _event(communicator, "box", "sentinel")
        frames = await _until(communicator, _pushed("sentinel"))
    finally:
        await communicator.disconnect()

    assert not [m for m in frames if _pushed("only")(m)]


@pytest.mark.unit
async def test_the_wait_for_a_mail_is_shorter_than_a_patch_hold():
    """A hold its mail releases is let go before that hold's deadline closes the socket (#178)."""
    from wireview.core import patches

    assert transport.MAIL_SECONDS < patches.HOLD_SECONDS


@pytest.mark.unit
async def test_a_session_not_served_here_is_mailed_through_the_broker_alone():
    """Another process's session, or one whose adapter keeps no mailbox: as before, ``False`` when dropped."""
    layer = InMemoryChannelLayer(capacity=1)
    broker = ChannelsBroker(layer)
    channel = await layer.new_channel()
    assert await transport.send_mail(broker, channel, {"type": "message_from_component", "n": 1}) is True
    assert await transport.send_mail(broker, channel, {"type": "message_from_component", "n": 2}) is False


# --- the turns mail is handled in, and what it may keep -------------------------------------


class _Probe(WireviewConsumer):
    """A consumer that records the mail it takes and how it closes; nothing else of a session runs."""

    def __init__(self) -> None:
        super().__init__()
        self.connection_id = "probe"
        self.seen: list[t.Any] = []

    async def _take_mail(self, data):
        self.seen.append(data.get("kwargs", {}).get("op") or data["command"])

    async def close(self, code=None):
        self.seen.append(("close", code))


def _box(probe: _Probe, channel: str) -> Mailbox:
    probe._mailbox = Mailbox(channel, probe.hand_over, lambda: probe.handling > 0, probe.mail_overflowed)
    return probe._mailbox


def _stream_op(op: str, html: str = "") -> dict[str, t.Any]:
    return {"type": "message_from_component", "command": "stream_op", "kwargs": {"op": op, "html": html}}


@pytest.mark.unit
async def test_a_mail_found_overdue_is_still_handled_before_a_later_one_the_channel_brings_first():
    """The timer only marks a mail due: a turn the channel's next mail takes first handles it ahead of that one.

    A reset handled after the insert sent behind it would wipe the inserted item.
    """
    probe = _Probe()
    box = _box(probe, "probe")
    layer = InMemoryChannelLayer(capacity=100)
    broker = ChannelsBroker(layer)
    loop = asyncio.get_running_loop()
    try:
        await box.send(broker, _stream_op("reset"))
        late = await layer.receive("probe")  # very late: it comes after all of this
        await box.send(broker, _stream_op("insert"))
        insert = await layer.receive("probe")
        # The reset has been away MAIL_SECONDS with nothing handled. Its timer
        # fires, and in the same tick the channel brings the insert.
        kept, _, size = box._away[1]
        box._away[1] = (kept, loop.time() - transport.MAIL_SECONDS, size)
        assert box._timer is not None
        box._timer.cancel()
        box._timer = None
        loop.call_soon(box._overdue)
        await asyncio.ensure_future(probe.dispatch(insert))
        await eventually(lambda: probe._handing_over is not None and probe._handing_over.done())
        await probe.dispatch(late)
    finally:
        box.close()

    assert probe.seen == ["reset", "insert"]


@pytest.mark.unit
@pytest.mark.parametrize(("limit", "html"), [("MAIL_LIMIT", ""), ("MAIL_BYTES", "x" * 10_000)], ids=["count", "bytes"])
async def test_mail_a_held_turn_cannot_take_is_kept_up_to_a_bound_and_then_the_connection_closes(
    monkeypatch, limit, html
):
    """A handler that mails on and on into a channel it fills: the copies stop at the bound, then 1013.

    One task waits for the turn, however many mails it will take.
    """
    monkeypatch.setattr(transport, "MAIL_LIMIT", 50)
    monkeypatch.setattr(transport, "MAIL_BYTES", 16 * 1024 * 1024 if limit == "MAIL_LIMIT" else 200_000)
    probe = _Probe()
    box = _box(probe, "full")
    layer = InMemoryChannelLayer(capacity=1)
    broker = ChannelsBroker(layer)
    await layer.send("full", {"type": "occupied"})
    # A handler is running: the connection takes nothing from its channel
    await probe._turn.acquire()
    probe.handling = 1
    kept: list[int] = []
    try:
        for _ in range(10_000):
            await box.send(broker, _stream_op("insert", html))
            kept.append(len(box._away))
        waiting = [task for task in asyncio.all_tasks() if task.get_coro().__name__ == "_take_handed_over"]
    finally:
        probe.handling = 0
        probe._turn.release()
    await eventually(lambda: ("close", 1013) in probe.seen)
    await eventually(lambda: probe._handing_over.done())

    assert max(kept) <= 50 and box.closed and not box._away
    assert len(waiting) == 1
    # Closed, the page joins again: nothing is handled from the copies
    assert probe.seen == [("close", 1013)]


async def test_the_task_taking_dropped_mail_ends_with_the_connection(small_channels, settings):
    """It waits for the turn of a handler that never ends; the connection ends instead, and the task with it."""
    from wireview.core import template_reload

    settings.WIREVIEW = {**getattr(settings, "WIREVIEW", {}), "REJOIN_ON_TEMPLATE_CHANGE": True}
    communicator = await _connect()
    HELD["until"] = asyncio.Event()
    session = None
    try:
        await _join(communicator, SmBox, "box")
        session = next(iter(template_reload._sessions))  # type: ignore[attr-defined]
        await _event(communicator, "box", "hold")
        await eventually(lambda: session.handling)
        await _fill(session.channel_name)
        await transport.send_mail(
            ChannelsBroker(),
            session.channel_name,
            {"type": "message_from_component", "command": "template_changed", "kwargs": {}},
        )
        task = session._handing_over
        assert task is not None and not task.done()
    finally:
        # The handler never ends: the app is cancelled, as a server cancels it on its way down
        await communicator.disconnect(timeout=0.5)
        HELD["until"].set()
        # Cancelled, it never ran disconnect(): the tests after this find their own session
        if session is not None:
            template_reload.unregister(session)

    assert task.cancelled()
    assert session is not None and session._mailbox is not None and session._mailbox.closed


# --- a logout, which no copy can stand in for -------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_logout_closes_a_connection_whose_channel_is_full(small_channels, settings):
    """``session_invalidated`` is fan-out, not mail: the logout's process cannot know a full channel dropped it.

    It reaches each process's ``PatchHub`` too, whose channel nothing holds up,
    so a connection busy enough to fill its own is closed all the same.
    """
    from asgiref.sync import sync_to_async

    from wireview.core import template_reload
    from wireview.core.live_session import invalidate_authentication

    settings.WIREVIEW = {**getattr(settings, "WIREVIEW", {}), "REJOIN_ON_TEMPLATE_CHANGE": True}
    # Logged in: an anonymous connection stands on no login a logout could end
    user = await User.objects.acreate(username="sm-logout")
    communicator = await _connect(user)
    try:
        await _join(communicator, SmBox, "box")
        session = next(iter(template_reload._sessions))  # type: ignore[attr-defined]
        await session._subscribe_auth_topic()
        HELD["until"] = asyncio.Event()
        await _event(communicator, "box", "hold")
        await eventually(lambda: session.handling)
        await _fill(session.channel_name)
        await sync_to_async(invalidate_authentication)(session.user, session.repo.session)
        HELD["until"].set()
        output = await communicator.receive_output(timeout=3)
        while output["type"] != "websocket.close":
            output = await communicator.receive_output(timeout=3)
    finally:
        await communicator.disconnect()

    assert output == {"type": "websocket.close", "code": 4001}
