"""A page that leaves while its message is answered ends the connection, and fails nothing (#216).

Moving through pages quickly, a page leaves while the server is still answering
its join: the answer is written to a socket that is gone. Uvicorn refuses that
frame with ``ClientDisconnected`` (an ``OSError``, as the ASGI spec asks), or --
once its receive has seen the close, which Channels reads while a mail is being
handled -- a ``RuntimeError``. Daphne drops it without a word. That refusal went
through the recovery for an application's exception: ``Could not join`` at ERROR
with its traceback, a ``join_rejected`` the telemetry counted as an error, and
from a handler or a mail, ``raised; joining it again`` and a second write that
raised out of the consumer.

The transport tells it apart (``ConnectionGone``): the session stops handling
that message, the adapter logs it at DEBUG, and nothing more is written. A join
whose component raises is still ``Could not join`` at ERROR.

The servers here are stand-ins that refuse a frame as Uvicorn does; the last
tests run the real Uvicorn and Daphne.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import typing as t
from pathlib import Path

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.server_process import ROOT, TESTS, free_port, wait_until_serving
from testproj.time_limit import disown, own
from testproj.waiting import eventually
from uvicorn.protocols.utils import ClientDisconnected

from wireview import Component, telemetry
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.core.transport import ChannelsOutbound, ConnectionGone

#: Renders to let through before one blocks until ``RELEASE`` (-1 lets them all through), and renders so far
GATE = {"skip": -1, "renders": 0}
#: Set once a render is blocked
ENTERED = threading.Event()
#: Lets the blocked render go on
RELEASE = threading.Event()
#: What the components did, in order
CALLS: list[str] = []


class GoneProbe(Component):
    """Its render waits when the test says so: the page leaves meanwhile."""

    count: int = 0

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}>{{ count }}{{ waiting }}</div>")

    @property
    def waiting(self) -> str:
        # Read on a worker thread: the loop goes on while it waits
        if GATE["skip"] == 0:
            ENTERED.set()
            RELEASE.wait(10)
        elif GATE["skip"] > 0:
            GATE["skip"] -= 1
        # Each render is new to the page, so each has a frame to write
        GATE["renders"] += 1
        return f" {GATE['renders']}"

    async def bump(self, **_rest):
        self.count += 1

    async def mail_a_render(self, **_rest):
        # Its render comes as a mail, answered while the next receive waits
        await self.wire.send("send_render", id=self.id)

    async def leaving(self):
        CALLS.append(f"leaving {self.id}")


class GoneFailing(Component):
    """A join that fails: still an error."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}></div>")

    async def joined(self):
        raise RuntimeError("joined() went wrong")


class Server:
    """The server's side of the socket: once the page ``left``, a frame is refused as Uvicorn refuses it.

    ``ClientDisconnected`` while Uvicorn has not read the close, a ``RuntimeError``
    once its receive has handed the application ``websocket.disconnect``.
    """

    def __init__(self, inner: t.Any) -> None:
        self.inner = inner
        self.left = False
        self.heard_close = False
        #: Frames the application tried to write once the page left
        self.refused: list[dict[str, t.Any]] = []

    async def __call__(self, scope, receive, send):
        async def receiving():
            message = await receive()
            if message["type"] == "websocket.disconnect":
                self.left = self.heard_close = True
            return message

        async def sending(message):
            if self.left and message["type"] in ("websocket.send", "websocket.close"):
                self.refused.append(message)
                if self.heard_close:
                    raise RuntimeError(
                        f"Unexpected ASGI message '{message['type']}', after sending 'websocket.close' "
                        "or response already completed."
                    )
                raise ClientDisconnected()
            await send(message)

        await self.inner(scope, receiving, sending)


@pytest.fixture(autouse=True)
def _reset(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    GATE.update(skip=-1, renders=0)
    ENTERED.clear()
    RELEASE.clear()
    CALLS.clear()
    yield
    # A render still waiting must not outlive the test
    RELEASE.set()


@pytest.fixture
def rejected():
    """The ``join_rejected`` telemetry events, with telemetry on."""
    events: list[dict[str, t.Any]] = []

    def receive(signal, **kwargs):
        events.append(kwargs)

    telemetry.join_rejected.connect(receive, weak=False)
    telemetry.enable()
    try:
        yield events
    finally:
        telemetry.disable()
        telemetry.join_rejected.disconnect(receive)


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.DEBUG, logger="wireview")
    return caplog


def _errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("wireview") and r.levelno >= logging.WARNING]


def _state(cls: type[Component], id: str) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))


async def _connect() -> tuple[WebsocketCommunicator, Server]:
    server = Server(WireviewConsumer.as_asgi())
    communicator = WebsocketCommunicator(server, f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator, server


async def _send(communicator: WebsocketCommunicator, _command: str, **payload: t.Any) -> None:
    await communicator.send_json_to({"command": _command, "payload": payload})


async def _join(communicator: WebsocketCommunicator, cls: type[Component], id: str) -> None:
    await _send(communicator, "join", name=cls.__name__, state=_state(cls, id), children={}, ref=1)


async def _joined(communicator: WebsocketCommunicator, id: str) -> None:
    await _join(communicator, GoneProbe, id)
    while (await communicator.receive_json_from(timeout=5))["command"] != "joined":
        pass


async def _blocked() -> None:
    await asyncio.to_thread(ENTERED.wait, 5)
    assert ENTERED.is_set(), "the render never began"


async def _ended(communicator: WebsocketCommunicator) -> None:
    """The page's disconnect reaches the application, which ends without raising."""
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})
    await communicator.wait(timeout=5)
    # Raised out of the consumer, the communicator's future holds it
    communicator.future.result()


def _left_quietly(caplog, server: Server, what: str, refused: int = 1) -> None:
    assert _errors(caplog) == [], [r.getMessage() for r in _errors(caplog)]
    assert any("left before" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records), what
    # The refused frame, and nothing written after it
    assert len(server.refused) == refused, server.refused


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_page_that_leaves_while_its_join_is_answered_is_no_error(logged, rejected):
    communicator, server = await _connect()
    GATE["skip"] = 0
    await _join(communicator, GoneProbe, "gone-join")
    await _blocked()

    server.left = True
    RELEASE.set()
    await eventually(lambda: server.refused)
    await _ended(communicator)

    _left_quietly(logged, server, "the join's answer")
    assert not any("Could not join" in r.getMessage() for r in logged.records)
    assert rejected == [], "a page that left is not a rejected join"
    # It joined: its ending is a component's like any other
    assert CALLS == ["leaving gone-join"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_page_that_leaves_while_its_event_is_answered_is_no_error(logged):
    communicator, server = await _connect()
    await _joined(communicator, "gone-event")
    GATE["skip"] = 0
    await _send(communicator, "user_event", id="gone-event", command="bump", implicit_args={}, explicit_args={}, ref=2)
    await _blocked()

    server.left = True
    RELEASE.set()
    await eventually(lambda: server.refused)
    await _ended(communicator)

    _left_quietly(logged, server, "the event's answer")
    assert not any("joining it again" in r.getMessage() for r in logged.records)
    assert CALLS == ["leaving gone-event"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_page_that_leaves_while_a_mail_is_answered_is_no_error(logged):
    """The receive has handed over the close: Uvicorn would refuse the frame with a ``RuntimeError``."""
    communicator, server = await _connect()
    await _joined(communicator, "gone-mail")
    # The event's own render goes through; the mail's waits
    GATE["skip"] = 1
    await _send(
        communicator, "user_event", id="gone-mail", command="mail_a_render", implicit_args={}, explicit_args={}, ref=2
    )
    await _blocked()

    # The close comes while the mail is handled: Channels' receive is waiting, and takes it
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})
    await eventually(lambda: server.heard_close)
    RELEASE.set()
    await communicator.wait(timeout=5)
    communicator.future.result()

    # The consumer heard the close first: the frame is not even tried
    _left_quietly(logged, server, "the mail's render", refused=0)
    assert CALLS == ["leaving gone-mail"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_join_that_raises_is_still_an_error(logged, rejected):
    communicator, _server = await _connect()
    await _join(communicator, GoneFailing, "gone-failing")
    while (message := await communicator.receive_json_from(timeout=5))["command"] != "error":
        pass
    await _ended(communicator)

    assert message["payload"] == {"id": "gone-failing", "during": "join", "ref": 1}
    (record,) = [r for r in logged.records if r.getMessage() == "Could not join gone-failing"]
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert [event["reason"] for event in rejected] == ["error"]


# --- the transport ----------------------------------------------------------


class _Consumer:
    """What ``ChannelsOutbound`` writes through, refusing each frame with ``error``."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.sent: list[t.Any] = []

    async def encode_json(self, content):
        return json.dumps(content)

    async def send(self, text_data=None):
        if self.error is not None:
            raise self.error
        self.sent.append(text_data)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_refusal_for_a_socket_that_is_gone_is_told_apart():
    """An ``OSError`` is a gone socket; so is any error once the socket is known gone; nothing is written after."""
    outbound = ChannelsOutbound(_Consumer(ClientDisconnected()))
    with pytest.raises(ConnectionGone):
        await outbound.send_command("render", {})
    assert outbound.gone

    heard = ChannelsOutbound(_Consumer(RuntimeError("Unexpected ASGI message 'websocket.send'")))
    heard.gone = True
    with pytest.raises(ConnectionGone):
        await heard.send_text("{}")

    writing = _Consumer(None)
    after = ChannelsOutbound(writing)
    after.gone = True
    with pytest.raises(ConnectionGone):
        await after.send_command("render", {})
    assert writing.sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_any_other_refusal_is_the_sessions_to_report():
    outbound = ChannelsOutbound(_Consumer(RuntimeError("something else")))
    with pytest.raises(RuntimeError, match="something else"):
        await outbound.send_command("render", {})
    assert not outbound.gone


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_oserror_while_encoding_is_not_a_gone_socket():
    """Only the ASGI send is the socket's: a payload that raises an ``OSError`` as it is encoded is the session's."""

    class FileBackedPayload(dict):
        def items(self):
            raise FileNotFoundError("the payload's source is missing")

    consumer = WireviewConsumer()
    sent: list[t.Any] = []

    async def send(message):
        sent.append(message)

    consumer.base_send = send
    with pytest.raises(FileNotFoundError):
        await consumer.outbound.send_command("push_event", {"payload": FileBackedPayload(value=1)})
    assert not consumer._socket.gone
    assert sent == []

    await consumer.outbound.send_command("push_event", {"payload": {"value": 1}})
    assert [message["type"] for message in sent] == ["websocket.send"]


def _sansio_closing() -> t.Any:
    """Uvicorn's websockets-sansio protocol after ``handle_close``: the page's close frame closed its transport.

    The disconnect it queued is not received yet -- the consumer is still on a
    message -- so nothing has marked the socket gone.
    """
    from unittest.mock import Mock

    from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol

    protocol = object.__new__(WebSocketsSansIOProtocol)
    protocol.writable = asyncio.Event()
    protocol.writable.set()
    protocol.handshake_complete = True
    protocol.initial_response = None
    protocol.close_sent = False
    protocol.transport = Mock()
    protocol.transport.is_closing.return_value = True
    return protocol


@pytest.mark.unit
@pytest.mark.asyncio
async def test_closing_a_socket_the_page_closed_before_it_was_heard_is_quiet(caplog):
    caplog.set_level(logging.DEBUG, logger="wireview")
    protocol = _sansio_closing()
    consumer = WireviewConsumer()
    consumer.base_send = protocol.send

    await consumer.close(code=1013)

    protocol.transport.write.assert_not_called()
    assert consumer._socket.gone
    assert [r.levelno for r in caplog.records if "before it closed" in r.getMessage()] == [logging.DEBUG]

    # Closed from a task of its own (falling behind, a mailbox overflowing), it leaves no exception unretrieved
    other = WireviewConsumer()
    other.base_send = _sansio_closing().send
    closing = asyncio.ensure_future(other.close(code=1013))
    await closing
    assert closing.exception() is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_close_refused_for_another_reason_is_raised():
    consumer = WireviewConsumer()

    async def send(message):
        raise RuntimeError("something else")

    consumer.base_send = send
    with pytest.raises(RuntimeError, match="something else"):
        await consumer.close(code=1013)


# --- the real servers -------------------------------------------------------


def _serve(server: str, port: int, log: Path, events: Path) -> subprocess.Popen:
    if server == "daphne":
        command = ["daphne", "-b", "127.0.0.1", "-p", str(port), "testproj.endprobe:application"]
    else:
        command = ["uvicorn", "testproj.endprobe:application", "--host", "127.0.0.1", "--port", str(port)]
        command += ["--lifespan", "off", "--ws", server.removeprefix("uvicorn-")]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(TESTS)]),
        "PYTHONUNBUFFERED": "1",
        "ENDPROBE_EVENTS": str(events),
        "WIREVIEW_TEST_LAYER": "memory",
    }
    with log.open("ab") as out:
        return own(
            subprocess.Popen([sys.executable, "-m", *command], cwd=TESTS, env=env, stdout=out, stderr=subprocess.STDOUT)
        )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.parametrize("server", ["uvicorn-websockets", "uvicorn-websockets-sansio", "daphne"])
def test_a_real_server_logs_no_error_for_a_page_that_left_while_its_join_was_answered(server, tmp_path):
    """The page closes its socket while ``joined()`` runs; the server's log has no error and no traceback.

    Synchronous: the E2E run's Playwright holds an event loop on this thread.
    """
    from testproj.endprobe import SlowJoin
    from websockets.sync.client import connect

    port = free_port()
    log = tmp_path / f"{server}.log"
    events = tmp_path / "events"
    events.write_text("", encoding="utf-8")
    proc = _serve(server, port, log, events)
    try:
        wait_until_serving(port, proc, log, 20)
        socket = connect(f"ws://127.0.0.1:{port}/__wireview__?vsn={PROTOCOL_VERSION}")
        state = sign_state(SlowJoin(user=AnonymousUser(), wire=WireviewMeta(params={}), id="slow", seconds=0.5))
        socket.send(json.dumps({"command": "join", "payload": {"name": "SlowJoin", "state": state, "children": {}}}))
        deadline = time.monotonic() + 5
        while "joining slow" not in events.read_text(encoding="utf-8") and time.monotonic() < deadline:
            time.sleep(0.02)
        socket.close()
        deadline = time.monotonic() + 5
        while "<<< DISCONNECT" not in log.read_text(encoding="utf-8") and time.monotonic() < deadline:
            time.sleep(0.05)
        written = log.read_text(encoding="utf-8")
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            disown(proc)

    assert events.read_text(encoding="utf-8").splitlines() == ["joining slow", "joined slow"]
    assert "<<< DISCONNECT" in written, written
    assert ">>> RENDER SlowJoin slow" in written, "the answer was written after the page left"
    assert "Could not join" not in written, written
    assert "Traceback" not in written, written
