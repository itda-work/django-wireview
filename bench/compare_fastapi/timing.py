"""Server processing time, measured the same way around both applications.

``Timed`` wraps an ASGI application. On a WebSocket it notes when a client message
comes out of ``receive`` and when the application next calls ``send`` on the same
connection: the time between is the server's work for that message, whatever stack
runs it. The raw message is kept and parsed only when the driver asks
(``GET /__bench__/timings``, which also clears them), so the wrapper adds nothing but
two clock reads to the measured span.
"""

import json
import time

TIMINGS_PATH = "/__bench__/timings"


class Timed:
    def __init__(self, app) -> None:
        self.app = app
        self.records: list[tuple[str, float]] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == TIMINGS_PATH:
            await self._timings(send)
            return
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        pending: list[tuple[str, float]] = []

        async def timed_receive():
            message = await receive()
            if message["type"] == "websocket.receive":
                pending.append((message.get("text") or "", time.perf_counter()))
            return message

        async def timed_send(message):
            if message["type"] == "websocket.send" and pending:
                text, started = pending.pop(0)
                self.records.append((text, (time.perf_counter() - started) * 1000))
            await send(message)

        await self.app(scope, timed_receive, timed_send)

    async def _timings(self, send) -> None:
        records, self.records = self.records, []
        body = json.dumps([[text, ms] for text, ms in records]).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def kind(text: str) -> str:
    """The action a client message asks for: wireview's handler name, or the FastAPI message type."""
    try:
        message = json.loads(text)
    except ValueError:
        return ""
    if "payload" in message:
        return message["payload"].get("command") or message.get("command", "")
    return message.get("type", "")
