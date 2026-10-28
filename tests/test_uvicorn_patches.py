"""uvicorn's WebSocket shutdown() and the patch that keeps it from ending a graceful stop (#226).

On a server stop uvicorn calls ``shutdown()`` on every connection. The sansio and
wsproto implementations raise on a connection whose page already sent its close
frame and that ``connection_lost()`` has not reached, and ``Server.shutdown()``
stops there: the connections after it get no 1012 and the app tasks are not waited
for. wireview/core/uvicorn_patches.py has the whole story.

Each protocol here is uvicorn's own, on a real server and a real socket; the page's
close frame is fed to it by hand so the moment between that frame and
``connection_lost()`` is not left to timing.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import typing as t

import pytest
import uvicorn
from django.test import override_settings
from websockets.frames import Close, CloseCode, Frame, Opcode

from wireview.core import uvicorn_patches
from wireview.core.uvicorn_patches import TARGETS, patch_shutdown

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

SANSIO, WSPROTO = TARGETS
IMPLEMENTATIONS = {"websockets-sansio": SANSIO, "wsproto": WSPROTO}


async def _app(scope, receive, send):
    await receive()
    await send({"type": "websocket.accept"})
    await asyncio.Event().wait()  # busy on a message: it has not received the disconnect


class _Running:
    """A uvicorn server on a free port and the raw sockets opened to it."""

    def __init__(self, ws: str) -> None:
        # The app never ends on its own: the graceful wait gives up on it quickly
        config = uvicorn.Config(
            _app, host="127.0.0.1", port=0, ws=ws, log_level="warning", lifespan="off", timeout_graceful_shutdown=0.5
        )
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.writers: list[asyncio.StreamWriter] = []

    async def __aenter__(self) -> _Running:
        self.serving = asyncio.ensure_future(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc: object) -> None:
        for writer in self.writers:
            writer.close()
        self.serving.cancel()
        await asyncio.gather(self.serving, return_exceptions=True)

    async def connect(self) -> t.Any:
        """A socket past its handshake, and uvicorn's protocol object for it."""
        before = set(self.server.server_state.connections)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.writers.append(writer)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        await reader.readuntil(b"\r\n\r\n")
        (connection,) = set(self.server.server_state.connections) - before
        return connection


def _page_closes(connection: t.Any) -> None:
    """The page's close frame arrives and is answered; connection_lost() has not run."""
    connection.data_received(Frame(Opcode.CLOSE, Close(CloseCode.GOING_AWAY, "").serialize()).serialize(mask=True))


def _unpatched(target) -> t.Any:
    """uvicorn's own shutdown(), whether or not a connection in this process patched the class."""
    cls = getattr(__import__(target.module, fromlist=[target.name]), target.name)
    return inspect.unwrap(cls.shutdown)


@pytest.fixture
def patched(request):
    """The class of the implementation under test with the patch on, put back afterwards."""
    target = IMPLEMENTATIONS[request.param]
    cls = getattr(__import__(target.module, fromlist=[target.name]), target.name)
    original = cls.__dict__["shutdown"]
    cls.shutdown = _unpatched(target)
    assert patch_shutdown(cls, target) == "applied"
    yield request.param
    cls.shutdown = original


@pytest.mark.parametrize("ws", IMPLEMENTATIONS)
async def test_uvicorn_still_has_the_defect(ws):
    """Fails once uvicorn's own shutdown() handles a page's close: then uvicorn_patches can go."""
    async with _Running(ws) as running:
        connection = await running.connect()
        _page_closes(connection)

        with pytest.raises(Exception, match="(?i)closing|closed"):
            _unpatched(IMPLEMENTATIONS[ws])(connection)


@pytest.mark.parametrize("patched", IMPLEMENTATIONS, indirect=True)
async def test_a_socket_the_page_is_closing_lets_the_server_stop_the_rest(patched):
    async with _Running(patched) as running:
        closing = await running.connect()
        other = await running.connect()
        _page_closes(closing)

        await running.server.shutdown()

        assert closing.transport.is_closing()
        # The connection after it still got its 1012 and its app the disconnect
        assert other.transport.is_closing()
        assert other.queue.get_nowait() == {"type": "websocket.disconnect", "code": 1012}


@pytest.mark.parametrize("patched", IMPLEMENTATIONS, indirect=True)
async def test_an_open_socket_still_gets_its_1012(patched):
    async with _Running(patched) as running:
        connection = await running.connect()
        written: list[bytes] = []
        write = connection.transport.write
        connection.transport.write = lambda data: (written.append(data), write(data))

        connection.shutdown()

        assert written and written[-1][:2] == b"\x88\x02"  # a close frame carrying a code
        assert int.from_bytes(written[-1][2:4], "big") == 1012
        assert connection.queue.get_nowait() == {"type": "websocket.disconnect", "code": 1012}


@pytest.mark.parametrize("patched", ["websockets-sansio"], indirect=True)
async def test_the_failure_on_an_open_socket_is_not_swallowed(patched):
    from websockets.exceptions import InvalidState

    async with _Running(patched) as running:
        connection = await running.connect()

        def refuse(*args: object) -> None:
            raise InvalidState("something else")

        connection.conn.send_close = refuse
        with pytest.raises(InvalidState, match="something else"):
            connection.shutdown()


class _Fixed:
    def shutdown(self):
        if self.conn.state is not None:  # what upstream's fix would read
            self.queue.put_nowait({})
            self.conn.send_close(1012)
        self.transport.close()


class _Rewritten:
    def shutdown(self):
        self.transport.abort()


async def test_it_goes_only_on_the_code_it_was_written_against():
    assert patch_shutdown(_Fixed, SANSIO) == "not needed: shutdown() checks the connection's state"
    assert patch_shutdown(_Rewritten, SANSIO) == "skipped: shutdown() is not the code this patch wraps"
    assert _Fixed.__dict__["shutdown"].__name__ == "shutdown" and not hasattr(_Fixed.shutdown, "__wrapped__")

    class Defective:
        shutdown = _unpatched(SANSIO)

    assert patch_shutdown(Defective, SANSIO) == "applied"
    assert patch_shutdown(Defective, SANSIO) == "already applied"
    assert inspect.unwrap(Defective.shutdown) is _unpatched(SANSIO)


def _fresh(monkeypatch) -> type:
    """apply() as on a process's first connection, on a class not yet patched."""
    cls = getattr(__import__(SANSIO.module, fromlist=[SANSIO.name]), SANSIO.name)
    monkeypatch.setattr(cls, "shutdown", _unpatched(SANSIO))
    monkeypatch.setattr(uvicorn_patches, "_applied", False)
    return cls


async def test_the_first_connection_patches_what_the_process_loaded(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="wireview")
    cls = _fresh(monkeypatch)

    uvicorn_patches.apply()

    assert hasattr(cls.shutdown, "__wrapped__")
    assert "WebSocketsSansIOProtocol.shutdown() patch (#226): applied" in caplog.text


async def test_the_setting_leaves_uvicorn_alone(monkeypatch):
    cls = _fresh(monkeypatch)

    with override_settings(WIREVIEW={"PATCH_UVICORN_SHUTDOWN": False}):
        uvicorn_patches.apply()

    assert not hasattr(cls.shutdown, "__wrapped__")


@pytest.mark.django_db(transaction=True)
async def test_a_consumer_connect_applies_it(monkeypatch):
    from channels.testing import WebsocketCommunicator

    from wireview.urls import websocket_urlpatterns

    cls = _fresh(monkeypatch)
    communicator = WebsocketCommunicator(websocket_urlpatterns[0].callback, "/__wireview__")
    await communicator.connect()
    await communicator.disconnect()

    assert hasattr(cls.shutdown, "__wrapped__")
