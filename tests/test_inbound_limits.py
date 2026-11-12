"""What one connection may send: a frame's size, how many a second, how many components it holds (#230).

A frame over ``INBOUND_MAX_BYTES`` is closed with 1009 before it is parsed;
past ``INBOUND_RATE_PER_SECOND`` with ``INBOUND_BURST``, 1008; a binary frame,
1003; a join past ``CONNECTION_MAX_COMPONENTS``, 1008. Nothing the refused frame
said is handled, nor anything after it. The unit tests judge the arithmetic,
the integration tests the real consumer's receive path (not ``websocket_connect``,
which a frame never passes), and the E2E ones the close code a real ASGI server
puts on the wire.
"""

from __future__ import annotations

import json
import logging
import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.e2e_server import serve
from testproj.wireview_setting import set_wireview
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from wireview import Component, telemetry
from wireview.consumer import WireviewConsumer
from wireview.core import inbound
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state

MIB = 1024 * 1024


class InboundProbe(Component):
    """Counts the events it handled."""

    count: int = 0

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}>{{ count }}</div>")

    async def bump(self):
        self.count += 1


class Clock:
    """The buckets' clock, moved by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    moved = Clock()
    monkeypatch.setattr(inbound, "monotonic", moved)
    return moved


@pytest.fixture(autouse=True)
def _layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@pytest.fixture
def refused():
    """The ``message_refused`` telemetry events, with telemetry on."""
    events: list[dict[str, t.Any]] = []

    def receive(signal, **kwargs):
        events.append(kwargs)

    telemetry.message_refused.connect(receive, weak=False)
    telemetry.enable()
    try:
        yield events
    finally:
        telemetry.disable()
        telemetry.message_refused.disconnect(receive)


def _state(id: str) -> str:
    return sign_state(InboundProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))


def _join(id: str, pad: int = 0) -> str:
    """A join message; ``pad`` characters of a key the session does not read make it as big as a test needs."""
    message: dict[str, t.Any] = {
        "command": "join",
        "payload": {"name": "InboundProbe", "state": _state(id), "children": {}, "ref": 1},
    }
    if pad:
        message["pad"] = "x" * pad
    return json.dumps(message)


def _sized(id: str, size: int) -> str:
    """A join of exactly ``size`` UTF-8 bytes."""
    bare = len(_join(id, 1).encode("utf-8")) - 1
    text = _join(id, size - bare)
    assert len(text.encode("utf-8")) == size
    return text


def _bump(id: str, ref: int) -> str:
    return json.dumps(
        {
            "command": "user_event",
            "payload": {"id": id, "command": "bump", "implicit_args": {}, "explicit_args": {}, "ref": ref},
        }
    )


class HeldConsumer(WireviewConsumer):
    """Keeps the instance, for a test to read what the connection held."""

    held: t.ClassVar[list[WireviewConsumer]] = []

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        HeldConsumer.held.append(self)


async def _connect(consumer: type[WireviewConsumer] = WireviewConsumer) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(consumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _until(communicator: WebsocketCommunicator, command: str) -> list[dict[str, t.Any]]:
    """The messages up to and including the first ``command``; fails on a close."""
    seen = []
    while True:
        output = await communicator.receive_output(timeout=5)
        assert output["type"] == "websocket.send", output
        seen.append(json.loads(output["text"]))
        if seen[-1]["command"] == command:
            return seen


async def _closed(communicator: WebsocketCommunicator) -> tuple[int | None, list[str]]:
    """The close code the consumer sent, and the commands it sent before it."""
    commands = []
    while True:
        output = await communicator.receive_output(timeout=5)
        if output["type"] == "websocket.close":
            return output.get("code"), commands
        commands.append(json.loads(output["text"])["command"])


async def _end(communicator: WebsocketCommunicator) -> None:
    await communicator.send_input({"type": "websocket.disconnect", "code": 1009})
    await communicator.wait(timeout=5)
    # Raised out of the consumer, the communicator's future holds it
    communicator.future.result()


# --- the arithmetic ----------------------------------------------------------


@pytest.mark.unit
def test_a_text_frame_is_measured_in_utf8_bytes():
    assert inbound.too_big("a" * 10, None, 10) is None
    assert inbound.too_big("a" * 11, None, 10) == 11
    # Four characters, twelve bytes: the character count alone would let it through
    assert inbound.too_big("가" * 4, None, 10) == 12
    assert inbound.too_big("가" * 3, None, 10) is None
    assert inbound.too_big(None, b"\0" * 11, 10) == 11
    assert inbound.too_big("a" * 100, None, None) is None


@pytest.mark.unit
def test_the_bucket_lets_a_burst_through_and_then_the_rate(clock):
    bucket = inbound.Bucket(burst=3, rate=2)
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    clock.now += 0.5  # one token back
    assert [bucket.take(), bucket.take()] == [True, False]
    clock.now += 100  # full again, and no more than full
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]


@pytest.mark.unit
def test_the_defaults_bound_the_size_and_not_the_rate(clock):
    gate = inbound.Gate()
    assert gate.refusal("x" * MIB, None) is None
    assert gate.refusal("x" * (MIB + 1), None) == ("size", MIB + 1, MIB)
    assert all(gate.refusal("{}", None) is None for _ in range(10_000))


@pytest.mark.unit
def test_none_turns_the_size_limit_off(monkeypatch):
    set_wireview(monkeypatch, INBOUND_MAX_BYTES=None)
    assert inbound.Gate().refusal("x" * (2 * MIB), None) is None


# --- the consumer's receive path ---------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_message_at_the_limit_is_handled_and_one_byte_more_closes_with_1009(caplog, refused):
    caplog.set_level(logging.WARNING, logger="wireview")
    communicator = await _connect()
    await communicator.send_to(text_data=_sized("at-limit", MIB))
    await _until(communicator, "joined")

    await communicator.send_to(text_data=_sized("past-limit", MIB + 1))
    # Behind it, a message that would be answered: nothing after the refused frame is handled
    await communicator.send_to(text_data=_join("after"))
    code, commands = await _closed(communicator)
    assert (code, commands) == (1009, [])
    assert await communicator.receive_nothing(timeout=0.2)
    await _end(communicator)

    assert [(e["reason"], e["size"], e["limit"]) for e in refused] == [("size", MIB + 1, MIB)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("INBOUND_MAX_BYTES" in w and "1009" in w for w in warnings), warnings
    # The log says how big, not what it said
    assert not any("xxxx" in w or "past-limit" in w for w in warnings), warnings


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_larger_limit_lets_a_larger_message_through(monkeypatch):
    set_wireview(monkeypatch, INBOUND_MAX_BYTES=3 * MIB)
    communicator = await _connect()
    await communicator.send_to(text_data=_sized("big", 2 * MIB))
    await _until(communicator, "joined")
    await _end(communicator)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_past_the_rate_the_connection_closes_with_1008(monkeypatch, clock, refused):
    set_wireview(monkeypatch, INBOUND_RATE_PER_SECOND=1, INBOUND_BURST=3)
    communicator = await _connect()
    await communicator.send_to(text_data=_join("rated"))  # 1: the join counts too
    await _until(communicator, "joined")
    await communicator.send_to(text_data=_bump("rated", 2))  # 2
    await communicator.send_to(text_data=_bump("rated", 3))  # 3
    await _until(communicator, "render")
    await _until(communicator, "render")
    clock.now += 1  # one back
    await communicator.send_to(text_data=_bump("rated", 4))
    await _until(communicator, "render")
    await communicator.send_to(text_data=_bump("rated", 5))  # none left
    code, commands = await _closed(communicator)
    assert (code, commands) == (1008, [])
    await _end(communicator)
    assert [(e["reason"], e["limit"]) for e in refused] == [("rate", 1)]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_rate_is_unbounded_by_default():
    communicator = await _connect()
    await communicator.send_to(text_data=_join("free"))
    await _until(communicator, "joined")
    for ref in range(2, 302):
        await communicator.send_to(text_data=_bump("free", ref))
    for _ in range(300):
        await _until(communicator, "render")
    await _end(communicator)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_binary_frame_closes_with_1003(refused):
    """It used to raise ``ValueError`` out of the consumer: a traceback, and the server's 1011."""
    communicator = await _connect()
    await communicator.send_to(bytes_data=b'{"command": "join"}')
    code, _ = await _closed(communicator)
    assert code == 1003
    await _end(communicator)
    assert [e["reason"] for e in refused] == ["binary"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_message_that_is_not_json_is_dropped_and_the_connection_goes_on(caplog):
    """It used to raise ``JSONDecodeError`` out of the consumer, ending the connection."""
    caplog.set_level(logging.WARNING, logger="wireview")
    communicator = await _connect()
    await communicator.send_to(text_data="{not json")
    await communicator.send_to(text_data=" ")
    await communicator.send_to(text_data=_join("goes-on"))
    await _until(communicator, "joined")
    await _end(communicator)
    assert sum("not JSON" in r.getMessage() for r in caplog.records) == 2


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_distinct_ids_grow_a_connection_without_bound_by_default():
    """The finding behind ``CONNECTION_MAX_COMPONENTS``: every valid state under a new id is one more instance.

    A page whose components have no id signs a fresh ``rx-`` id on every load,
    so a client can collect as many as it loads the page.
    """
    HeldConsumer.held.clear()
    communicator = await _connect(HeldConsumer)
    for index in range(50):
        await communicator.send_to(text_data=_join(f"many-{index}"))
        await _until(communicator, "joined")
    # What each join added
    assert len(HeldConsumer.held[0].repo.components) == 50
    await _end(communicator)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_join_past_the_components_limit_closes_with_1008(monkeypatch, refused):
    set_wireview(monkeypatch, CONNECTION_MAX_COMPONENTS=2)
    communicator = await _connect()
    for id in ("one", "two"):
        await communicator.send_to(text_data=_join(id))
        await _until(communicator, "joined")
    # The same id again is the page's new DOM for it (boost), not one more
    await communicator.send_to(text_data=_join("one"))
    await _until(communicator, "joined")

    await communicator.send_to(text_data=_join("three"))
    code, commands = await _closed(communicator)
    assert (code, commands) == (1008, [])
    await _end(communicator)
    assert [(e["reason"], e["size"], e["limit"]) for e in refused] == [("components", 2, 2)]


# --- on the wire -------------------------------------------------------------


def _close_code(url: str, frame: str | bytes, before: list[str] = ()) -> int | None:  # type: ignore[assignment]
    """The code a real server closes with after ``before`` and then ``frame``."""
    with connect(url, max_size=None) as socket:
        for message in before:
            socket.send(message)
        socket.send(frame)
        try:
            while True:
                socket.recv(timeout=5)
        except ConnectionClosed:
            pass
        assert socket.close_code is not None, "the server never closed it"
        return socket.close_code


@pytest.mark.e2e
def test_a_real_server_closes_with_the_codes(transactional_db, monkeypatch):
    """Uvicorn, as ``tests/testproj/e2e_server.py`` runs it: the codes reach the page.

    Synchronous: the E2E run's Playwright may hold an event loop on this thread.
    """
    set_wireview(monkeypatch, INBOUND_RATE_PER_SECOND=0.001, INBOUND_BURST=2)
    with serve() as base_url:
        url = base_url.replace("http", "ws", 1) + f"/__wireview__?vsn={PROTOCOL_VERSION}"
        assert _close_code(url, _sized("wire-big", MIB + 1)) == 1009
        assert _close_code(url, b"\0\1") == 1003
        assert _close_code(url, _bump("nobody", 3), before=[_bump("nobody", 1), _bump("nobody", 2)]) == 1008


@pytest.mark.unit
def test_the_reasons_are_a_closed_set_with_a_code_and_a_log_line_each():
    from wireview import session

    assert set(inbound.CLOSE_CODES) == telemetry.MESSAGE_REFUSED_REASONS == set(session._REFUSED_DETAIL)
