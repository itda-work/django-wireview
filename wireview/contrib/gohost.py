"""Host wireview WebSocket sessions behind the Go proxy (``goproxy/``).

The Go process terminates the browser WebSockets and forwards frames over one
Unix socket, one JSON object per line. This module turns each forwarded
connection into an ASGI websocket scope and runs the project's ASGI
application for it: the same ``WireviewConsumer`` daphne would run, with the
channel layer untouched. Only the socket handling moves to Go.

Line format (both directions): ``{"c": id, "t": "open"|"frame"|"close"|"accept", ...}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import typing as t

log = logging.getLogger("wireview.gohost")

Message = dict[str, t.Any]
LINE_LIMIT = 64 * 1024 * 1024


class Session:
    """One browser connection: an ASGI websocket app fed from the Go proxy."""

    def __init__(self, cid: str, opened: Message, application: t.Any, outbox: asyncio.Queue[Message]) -> None:
        self.cid = cid
        self.outbox = outbox
        self.inbox: asyncio.Queue[Message] = asyncio.Queue()
        self.closed = False
        headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in opened.get("headers", [])]
        path = opened.get("path", "/")
        query = opened.get("query", "")
        self.scope: Message = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": headers,
            "client": None,
            "server": None,
            "subprotocols": [],
        }
        self.inbox.put_nowait({"type": "websocket.connect"})
        self.task = asyncio.create_task(self._run(application))

    async def _run(self, application: t.Any) -> None:
        try:
            await application(self.scope, self.inbox.get, self._send)
        except Exception:
            log.exception("session %s crashed", self.cid)
            await self._close(1011)

    async def _send(self, message: Message) -> None:
        kind = message["type"]
        if kind == "websocket.accept":
            await self.outbox.put({"c": self.cid, "t": "accept"})
        elif kind == "websocket.send":
            text = message.get("text")
            if text is None and message.get("bytes") is not None:
                text = message["bytes"].decode("utf-8")
            await self.outbox.put({"c": self.cid, "t": "frame", "text": text or ""})
        elif kind == "websocket.close":
            await self._close(message.get("code", 1000))

    async def _close(self, code: int) -> None:
        if not self.closed:
            self.closed = True
            await self.outbox.put({"c": self.cid, "t": "close", "code": code})

    def disconnect(self, code: int = 1000) -> None:
        """The browser side went away: let the consumer run its disconnect() path."""
        self.closed = True
        self.inbox.put_nowait({"type": "websocket.disconnect", "code": code})


class GoHost:
    def __init__(self, application: t.Any, socket_path: str) -> None:
        self.application = application
        self.socket_path = socket_path

    async def serve(self) -> None:
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        server = await asyncio.start_unix_server(self._backend, path=self.socket_path, limit=LINE_LIMIT)
        log.info("wireview gohost listening on %s", self.socket_path)
        async with server:
            await server.serve_forever()

    async def _backend(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One Go proxy process. All of its browser connections multiplex over this stream."""
        sessions: dict[str, Session] = {}
        outbox: asyncio.Queue[Message] = asyncio.Queue()
        pump = asyncio.create_task(self._pump(outbox, writer))
        try:
            while line := await reader.readline():
                msg = json.loads(line)
                cid, kind = msg["c"], msg["t"]
                if kind == "open":
                    sessions[cid] = Session(cid, msg, self.application, outbox)
                elif kind == "frame" and cid in sessions:
                    sessions[cid].inbox.put_nowait({"type": "websocket.receive", "text": msg.get("text", "")})
                elif kind == "close" and cid in sessions:
                    sessions.pop(cid).disconnect(msg.get("code", 1000))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            for session in sessions.values():
                session.disconnect(1001)
            pump.cancel()
            writer.close()

    @staticmethod
    async def _pump(outbox: asyncio.Queue[Message], writer: asyncio.StreamWriter) -> None:
        """Single writer for the backend stream so lines never interleave."""
        while True:
            msg = await outbox.get()
            writer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
