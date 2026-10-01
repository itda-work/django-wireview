"""Operational telemetry: connections, refused joins, and what the layer refuses (#124).

The first four signals measure the cost of work. These answer what an operator
asks first: how many sockets are open, why joins are being refused, and whether
the channel layer is dropping messages -- all of which used to exist only as log
text, or, for a full channel, as an exception that took the handler down.
"""

import logging
import typing as t

import pytest
from channels.exceptions import ChannelFull
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component, telemetry
from wireview.consumer import WireviewConsumer
from wireview.core.live_session import LiveSession
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.core.transport import ChannelsBroker
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db, pytest.mark.asyncio]

TEMPLATES = {"tel/probe.html": "{% load wireview %}<p {% tag_header %}>{{ this.note }}</p>"}


class HaltingHook:
    @staticmethod
    async def on_mount(component, params, session):
        return {"halt": True}


class BrokenHook:
    @staticmethod
    async def on_mount(component, params, session):
        raise RuntimeError("the hook fell over")


class TelProbe(Component):
    class Meta:
        template_name = "tel/probe.html"

    note: str = "hi"


class TelHalted(Component):
    class Meta:
        template_name = "tel/probe.html"
        on_mount = [HaltingHook]

    note: str = "halted"


class TelBroken(Component):
    class Meta:
        template_name = "tel/probe.html"
        on_mount = [BrokenHook]

    note: str = "broken"


class Recorder:
    SIGNALS = (
        telemetry.connection_opened,
        telemetry.connection_closed,
        telemetry.join_rejected,
        telemetry.publish_failed,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[t.Any, dict[str, t.Any]]] = []

    def _receive(self, signal, **kwargs) -> None:
        self.calls.append((signal, kwargs))

    def of(self, signal) -> list[dict[str, t.Any]]:
        return [kwargs for sig, kwargs in self.calls if sig is signal]


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


@pytest.fixture
def recorder():
    rec = Recorder()
    for signal in Recorder.SIGNALS:
        signal.connect(rec._receive, weak=False)
    try:
        yield rec
    finally:
        for signal in Recorder.SIGNALS:
            signal.disconnect(rec._receive)


@pytest.fixture
def telemetry_on(recorder):
    telemetry.enable()
    try:
        yield recorder
    finally:
        telemetry.disable()


async def started() -> WireviewSession:
    session = WireviewSession(RecordingOutbound(), user=AnonymousUser(), channel_name="tel-1")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    return session


def signed(cls: type[Component], *, page: LiveSession | None = None, **state) -> str:
    component = cls(user=AnonymousUser(), wire=WireviewMeta(params={}, live_session=page), **state)
    return sign_state(component)


# --- connections -------------------------------------------------------------


async def test_a_session_reports_opening_and_closing(telemetry_on):
    session = await started()
    await session.command_join("TelProbe", signed(TelProbe, id="p1"))
    await session.stop(1001)

    (opened,) = telemetry_on.of(telemetry.connection_opened)
    (closed,) = telemetry_on.of(telemetry.connection_closed)
    assert opened["sender"] is WireviewSession
    assert opened["connection_id"] == session.connection_id != ""
    assert closed["connection_id"] == session.connection_id
    assert closed["code"] == 1001
    assert closed["components"] == 1
    assert closed["duration_ms"] >= 0


async def test_a_session_opened_before_telemetry_was_on_closes_without_a_duration(recorder):
    session = await started()
    telemetry.enable()
    try:
        await session.stop()
    finally:
        telemetry.disable()

    assert recorder.of(telemetry.connection_opened) == []
    (closed,) = recorder.of(telemetry.connection_closed)
    assert closed["duration_ms"] is None
    assert closed["code"] is None


async def test_nothing_is_reported_while_telemetry_is_off(recorder):
    session = await started()
    await session.command_join("TelProbe", "not a signature")
    await session.stop(1000)

    assert recorder.calls == []


async def test_the_consumer_passes_the_close_code_on(telemetry_on):
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), "/__wireview__")
    connected, _ = await communicator.connect()
    assert connected
    await communicator.disconnect(code=4000)

    assert len(telemetry_on.of(telemetry.connection_opened)) == 1
    (closed,) = telemetry_on.of(telemetry.connection_closed)
    assert closed["sender"] is WireviewConsumer
    assert closed["code"] == 4000


# --- refused joins -------------------------------------------------------------


async def test_a_state_that_does_not_verify_is_reported_as_invalid(telemetry_on):
    session = await started()
    await session.command_join("TelProbe", "not a signature")

    (rejected,) = telemetry_on.of(telemetry.join_rejected)
    assert rejected["reason"] == "invalid"
    assert rejected["component_name"] == "TelProbe"
    assert rejected["detail"]


async def test_an_expired_state_is_reported_as_expired(telemetry_on):
    token = signed(TelProbe, id="p1")
    session = await started()
    with override_settings(WIREVIEW={"STATE_MAX_AGE": -1}):
        await session.command_join("TelProbe", token)

    assert [r["reason"] for r in telemetry_on.of(telemetry.join_rejected)] == ["expired"]


async def test_a_boundary_refusal_is_reported_as_live_session(telemetry_on):
    session = await started()
    await session.command_join("TelProbe", signed(TelProbe, id="p1", page=LiveSession("tel-unregistered")))

    (rejected,) = telemetry_on.of(telemetry.join_rejected)
    assert rejected["reason"] == "live_session"
    assert "tel-unregistered" in rejected["detail"]


async def test_a_halted_mount_is_reported_as_halted(telemetry_on):
    session = await started()
    await session.command_join("TelHalted", signed(TelHalted, id="h1"))

    assert [r["reason"] for r in telemetry_on.of(telemetry.join_rejected)] == ["halted"]


async def test_a_mount_that_raises_is_reported_as_error(telemetry_on, caplog):
    session = await started()
    with caplog.at_level(logging.CRITICAL, logger="wireview"):
        await session.command_join("TelBroken", signed(TelBroken, id="b1"))

    (rejected,) = telemetry_on.of(telemetry.join_rejected)
    assert (rejected["reason"], rejected["component_name"]) == ("error", "TelBroken")


async def test_a_join_that_goes_through_reports_nothing(telemetry_on):
    session = await started()
    await session.command_join("TelProbe", signed(TelProbe, id="p1"))

    assert telemetry_on.of(telemetry.join_rejected) == []


@override_settings(ALLOWED_HOSTS=["example.com"])
async def test_a_refused_origin_is_reported_and_opens_no_session(telemetry_on):
    communicator = WebsocketCommunicator(
        WireviewConsumer.as_asgi(), "/__wireview__", headers=[(b"origin", b"https://evil.example")]
    )
    connected, _ = await communicator.connect()
    assert not connected

    (rejected,) = telemetry_on.of(telemetry.join_rejected)
    assert (rejected["reason"], rejected["component_name"]) == ("origin", None)
    assert "evil.example" in rejected["detail"]
    assert telemetry_on.of(telemetry.connection_opened) == []
    assert telemetry_on.of(telemetry.connection_closed) == []


# --- what the channel layer refuses ------------------------------------------


class FailingLayer(InMemoryChannelLayer):
    async def group_send(self, group, message):
        raise ConnectionError("broker unreachable")


async def test_a_full_channel_drops_the_message_and_says_so(telemetry_on, caplog):
    broker = ChannelsBroker(InMemoryChannelLayer(capacity=1))
    await broker.send_to_session("specific.tel!one", {"type": "notification", "n": 1})
    with caplog.at_level(logging.WARNING, logger="wireview"):
        # Full: the second message is dropped and the caller carries on
        await broker.send_to_session("specific.tel!one", {"type": "notification", "n": 2})

    (failed,) = telemetry_on.of(telemetry.publish_failed)
    assert failed["sender"] is ChannelsBroker
    assert (failed["kind"], failed["target"], failed["dropped"]) == ("send_to_session", "specific.tel!one", True)
    assert isinstance(failed["error"], ChannelFull)
    assert "full" in caplog.text and "notification" in caplog.text


async def test_a_full_channel_is_logged_while_telemetry_is_off(recorder, caplog):
    broker = ChannelsBroker(InMemoryChannelLayer(capacity=1))
    await broker.send_to_session("specific.tel!one", {"type": "notification"})
    with caplog.at_level(logging.WARNING, logger="wireview"):
        await broker.send_to_session("specific.tel!one", {"type": "notification"})

    assert recorder.calls == []
    assert "full" in caplog.text


async def test_any_other_layer_error_is_reported_and_raised(telemetry_on):
    broker = ChannelsBroker(FailingLayer())
    with pytest.raises(ConnectionError):
        await broker.publish("tel-topic", {"type": "notification"})

    (failed,) = telemetry_on.of(telemetry.publish_failed)
    assert (failed["kind"], failed["target"], failed["dropped"]) == ("publish", "tel-topic", False)
    assert isinstance(failed["error"], ConnectionError)
