"""What a visitor of the public demo may send, enforced without touching the examples.

Over HTTP, ``RequestLimitMiddleware`` refuses a field longer than ``TEXT_MAX`` and counts each
visitor's form posts. Over the WebSocket, ``SocketLimits`` wraps the route: it counts each
visitor's new sockets, refuses a frame larger than ``FRAME_MAX``, cuts every text a page sends
with an event to ``TEXT_MAX``, and closes a socket that sends events faster than its bucket
refills. A closed page reconnects on its own after the client's backoff, so a visitor who only
clicks never sees any of it.

The visitor is the address Caddy read from Cloudflare's ``CF-Connecting-IP`` and passed on as
``X-Real-IP`` (demo/Caddyfile). Nothing reaches the app except through Caddy, so the header is
not the browser's to choose.
"""

from __future__ import annotations

import json
import logging
import time
import typing as t

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

log = logging.getLogger("demo.limits")

#: The close code for "too much": the client reconnects after its backoff
TOO_MANY = 4029
#: RFC 6455's "message too big"
TOO_BIG = 1009

#: The inbound commands a person causes, as opposed to those a page sends on its own (join,
#: leave, params_changed, sync): these are what the event bucket counts
COUNTED_COMMANDS = frozenset({"user_event", "hook_event", "upload_register", "upload_cancel", "upload_complete"})


def limits() -> dict[str, t.Any]:
    return settings.DEMO_LIMITS


def visitor_from_headers(headers: t.Iterable[tuple[bytes, bytes]], fallback: str) -> str:
    for name, value in headers:
        if name.lower() == b"x-real-ip" and value:
            return value.decode("latin-1").strip()
    return fallback


def over_limit(kind: str, visitor: str, limit: int, window: int) -> bool:
    """Count one more of ``kind`` for ``visitor`` in a fixed window; whether that is past ``limit``.

    ``add`` creates the key only when it is missing, so the window starts at the first hit and
    does not grow with every hit after it. Redis's INCR is atomic, so workers do not lose counts.
    """
    key = f"limit:{kind}:{visitor}"
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:  # the window ended between add and incr
        cache.set(key, 1, window)
        count = 1
    return count > limit


async def aover_limit(kind: str, visitor: str, limit: int, window: int) -> bool:
    key = f"limit:{kind}:{visitor}"
    await cache.aadd(key, 0, window)
    try:
        count = await cache.aincr(key)
    except ValueError:
        await cache.aset(key, 1, window)
        count = 1
    return count > limit


def request_visitor(request: HttpRequest) -> str:
    return request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR", "")


class RequestLimitMiddleware:
    """Refuse an over-long field (400) and a visitor's posts past the minute's limit (429)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        text_max = limits()["TEXT_MAX"]
        fields = [*request.GET.lists()]
        if request.method == "POST":
            fields += request.POST.lists()
        for name, values in fields:
            if any(len(value) > text_max for value in values):
                return HttpResponse(f"{name} is longer than {text_max} characters.\n", status=400)
        if request.method == "POST":
            per_minute = limits()["POSTS_PER_MINUTE"]
            if over_limit("post", request_visitor(request), per_minute, 60):
                log.info("posts over the limit from %s", request_visitor(request))
                return HttpResponse("Too many requests. Wait a minute and try again.\n", status=429)
        return self.get_response(request)


def _cut(value: t.Any, text_max: int) -> t.Any:
    """``value`` with every string in it cut to ``text_max`` characters."""
    if isinstance(value, str):
        return value[:text_max]
    if isinstance(value, list):
        return [_cut(item, text_max) for item in value]
    if isinstance(value, dict):
        return {key: _cut(item, text_max) for key, item in value.items()}
    return value


class _Bucket:
    def __init__(self, burst: int, per_second: float):
        self.burst = burst
        self.per_second = per_second
        self.tokens = float(burst)
        self.at = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.per_second)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class SocketLimits:
    """ASGI middleware for the WebSocket route. ``inner`` keeps wireview.W020's walk going."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.inner(scope, receive, send)
        settings_ = limits()
        client = scope.get("client") or ("", 0)
        visitor = visitor_from_headers(scope.get("headers", []), client[0])
        bucket = _Bucket(settings_["EVENT_BURST"], settings_["EVENTS_PER_SECOND"])
        closed = False

        async def close(code: int, why: str) -> dict:
            nonlocal closed
            log.info("closing a socket of %s: %s", visitor, why)
            if not closed:
                closed = True
                await send({"type": "websocket.close", "code": code})
            # The consumer sees a disconnect, runs leaving() and lets go of its subscriptions
            return {"type": "websocket.disconnect", "code": code}

        async def guarded_send(message):
            # Once this middleware closed the socket, what the consumer still sends goes nowhere
            if not closed:
                await send(message)

        async def guarded_receive():
            message = await receive()
            kind = message["type"]
            if kind == "websocket.connect":
                if await aover_limit("socket", visitor, settings_["SOCKETS_PER_MINUTE"], 60):
                    return await close(TOO_MANY, "new sockets over the limit")
                return message
            if kind != "websocket.receive":
                return message
            text = message.get("text")
            if text is None:
                return await close(TOO_BIG, "a binary frame")
            if len(text) > settings_["FRAME_MAX"]:
                return await close(TOO_BIG, f"a frame of {len(text)} characters")
            try:
                frame = json.loads(text)
            except ValueError:
                return message  # the consumer logs and drops it
            if not isinstance(frame, dict) or frame.get("command") not in COUNTED_COMMANDS:
                return message
            if not bucket.take():
                return await close(TOO_MANY, "events over the limit")
            payload = frame.get("payload")
            if frame["command"] == "user_event" and isinstance(payload, dict):
                text_max = settings_["TEXT_MAX"]
                cut = {key: _cut(value, text_max) for key, value in payload.items()}
                if cut != payload:
                    frame["payload"] = cut
                    return {**message, "text": json.dumps(frame)}
            return message

        return await self.inner(scope, guarded_receive, guarded_send)
