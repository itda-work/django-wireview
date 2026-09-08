"""The Go-proxy host turns forwarded connections into ASGI websocket sessions."""

import asyncio
import json

import pytest

from wireview.contrib.gohost import GoHost


class FakeReader:
    """Feeds the host a fixed sequence of lines, then waits for ``eof`` before signalling EOF.

    A real proxy socket stays open while sessions run; EOF means the proxy died.
    """

    def __init__(self, lines: list[dict]) -> None:
        self._lines = [json.dumps(line).encode() + b"\n" for line in lines]
        self.eof = asyncio.Event()

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        if self._lines:
            return self._lines.pop(0)
        await self.eof.wait()
        return b""


async def _wait_for(writer: "FakeWriter", predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate(writer.lines):
        assert asyncio.get_event_loop().time() < deadline, writer.lines
        await asyncio.sleep(0.01)


class FakeWriter:
    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data))

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True


async def echo_app(scope, receive, send):
    """Minimal ASGI websocket app: accept, echo one frame, close on disconnect."""
    assert scope["type"] == "websocket"
    assert scope["path"] == "/__wireview__"
    assert (b"cookie", b"sessionid=abc") in scope["headers"]
    message = await receive()
    assert message["type"] == "websocket.connect"
    await send({"type": "websocket.accept"})
    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            return
        await send({"type": "websocket.send", "text": "echo:" + message["text"]})


@pytest.mark.asyncio
@pytest.mark.unit
async def test_forwarded_connection_runs_the_asgi_app_and_answers_over_the_socket():
    reader = FakeReader(
        [
            {"c": "1", "t": "open", "path": "/__wireview__", "query": "", "headers": [["cookie", "sessionid=abc"]]},
            {"c": "1", "t": "frame", "text": "hello"},
            {"c": "1", "t": "close", "code": 1000},
        ]
    )
    writer = FakeWriter()

    backend = asyncio.create_task(GoHost(echo_app, "unused.sock")._backend(reader, writer))
    await _wait_for(writer, lambda lines: any(m["t"] == "frame" for m in lines))
    reader.eof.set()
    await backend

    assert [(m["c"], m["t"], m.get("text")) for m in writer.lines][:2] == [
        ("1", "accept", None),
        ("1", "frame", "echo:hello"),
    ]
    assert writer.closed


@pytest.mark.asyncio
@pytest.mark.unit
async def test_app_close_is_relayed_to_the_proxy():
    async def closing_app(scope, receive, send):
        await receive()
        await send({"type": "websocket.close", "code": 4001})

    reader = FakeReader([{"c": "7", "t": "open", "path": "/x", "query": "", "headers": []}])
    writer = FakeWriter()

    backend = asyncio.create_task(GoHost(closing_app, "unused.sock")._backend(reader, writer))
    await _wait_for(writer, lambda lines: any(m["t"] == "close" for m in lines))
    reader.eof.set()
    await backend

    assert {"c": "7", "t": "close", "code": 4001} in writer.lines
