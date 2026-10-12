"""What a visitor of the public demo may send, enforced without touching the examples.

Over HTTP, ``RequestLimitMiddleware`` counts each visitor's requests and form posts, refuses a
field longer than ``TEXT_MAX``, and keeps a visitor from minting stored sessions without end
(``demo.sessions``). Over the WebSocket, ``SocketLimits`` wraps the route:

- at the handshake it counts the visitor's new sockets and the sockets they hold open, and all
  sockets the demo holds open;
- every frame is measured in UTF-8 bytes against ``FRAME_MAX`` and spends the socket's frame
  bucket and the visitor's frame window;
- a frame that is not JSON or names no command the server knows spends a small junk bucket and
  goes no further, so it neither reaches the consumer nor fills the log;
- events (``user_event``, ``hook_event``, uploads) spend the socket's event bucket and the
  visitor's event window;
- what makes the server restore or re-render a component without a click -- a join of state
  this socket has joined before, ``params_changed``, ``navigated`` -- spends the socket's render
  bucket; a join of state it has not seen spends a bucket sized for a page's first joins. Both
  count in the visitor's render window;
- every text a frame carries is cut to ``TEXT_MAX``, the query of a ``uri`` included, except a
  join's signed state.

A bucket lives with its socket; a window lives in Redis per visitor, so a visitor who
reconnects or opens more sockets does not start over. A page that is closed for going too fast
reconnects on its own after the client's backoff, so a visitor who only clicks never sees any
of it.

The visitor is the address Caddy read from Cloudflare's ``CF-Connecting-IP`` and passed on as
``X-Real-IP`` (demo/Caddyfile); an IPv6 address counts as its /64, the block one subscriber
holds. Nothing reaches the app except through Caddy, so the header is not the browser's to
choose. The counters are in a Redis of their own that never evicts (compose.yaml): when it is
full or gone, a limit refuses rather than lets through (``LimiterDown``).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import time
import typing as t
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

log = logging.getLogger("demo.limits")

#: The close code for "too much": the client reconnects after its backoff
TOO_MANY = 4029
#: RFC 6455's "message too big"
TOO_BIG = 1009
#: RFC 6455's "try again later": the counters could not be reached
TRY_LATER = 1013
#: How long closing a socket may take, reading out what the page had already sent
CLOSE_SECONDS = 10

#: What a person causes: these spend the event budget
EVENT_COMMANDS = frozenset({"user_event", "hook_event", "upload_register", "upload_cancel", "upload_complete"})
#: What makes the server restore or render a component without a click
RENDER_COMMANDS = frozenset({"params_changed", "navigated"})
#: Everything else the server knows (wire-protocol.md §2); any other command is junk
KNOWN_COMMANDS = EVENT_COMMANDS | RENDER_COMMANDS | {"join", "leave", "sync"}

#: The visitor of this request, for ``demo.sessions`` (None outside a request: the WebSocket)
current_visitor: ContextVar[str | None] = ContextVar("demo_visitor", default=None)


class LimiterDown(Exception):
    """The counters could not be read or written: a limit refuses rather than lets through."""


def limits() -> dict[str, t.Any]:
    return settings.DEMO_LIMITS


def plain(text: str, status: int) -> HttpResponse:
    """An error answer as text: nothing a visitor sent goes back as markup."""
    return HttpResponse(text + "\n", status=status, content_type="text/plain; charset=utf-8")


def visitor_key(address: str) -> str:
    """The address a limit counts: an IPv6 address by its /64, anything else as it is."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address[:64]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def visitor_from_headers(headers: t.Iterable[tuple[bytes, bytes]], fallback: str) -> str:
    for name, value in headers:
        if name.lower() == b"x-real-ip" and value:
            return visitor_key(value.decode("latin-1").strip())
    return visitor_key(fallback)


def request_visitor(request: HttpRequest) -> str:
    return visitor_key(request.headers.get("X-Real-IP") or request.META.get("REMOTE_ADDR", ""))


def _key(kind: str, visitor: str) -> str:
    return f"limit:{kind}:{visitor}"


def count(kind: str, visitor: str, window: int) -> int:
    """One more of ``kind`` for ``visitor`` in a fixed window that starts at the first one; the count so far.

    INCR is atomic in Redis, so workers do not lose counts, and it keeps the key's expiry, so the
    window does not grow with every hit.
    """
    key = _key(kind, visitor)
    try:
        try:
            return cache.incr(key)
        except ValueError:  # no window yet, or it just ended
            if cache.add(key, 1, window):
                return 1
            return cache.incr(key)
    except ValueError:
        return 1
    except Exception as error:
        raise LimiterDown(kind) from error


async def acount(kind: str, visitor: str, window: int) -> int:
    """``count`` from the event loop, on a worker thread.

    Not the cache's ``aincr``: Django's RedisCache has none of its own, and BaseCache's reads the
    value and writes it back with the default timeout -- not atomic, and every hit would push
    the window's end five minutes out.
    """
    return await _off_loop(count)(kind, visitor, window)


def _off_loop(function):
    # Not thread-sensitive: a count touches no database, and a frame should not queue behind
    # the request thread
    return sync_to_async(function, thread_sensitive=False)


def over_limit(kind: str, visitor: str, limit: int, window: int) -> bool:
    return count(kind, visitor, window) > limit


async def aover_limit(kind: str, visitor: str, limit: int, window: int) -> bool:
    return await acount(kind, visitor, window) > limit


#: Paths compose's own healthchecks ask for from inside the network, without X-Real-IP
PROBES = frozenset({"/health/", "/ready/"})


class RequestLimitMiddleware:
    """Refuse a visitor's requests past the minute's budget (429), an over-long field (400), and
    posts past the minute's limit (429). A session the request may not create (``demo.sessions``)
    ends the request with 429 or 503 too.

    Every answer it gives is plain text, and none repeats what the visitor sent: a field's name
    is the visitor's to choose, and an HTML answer would run it as markup.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        settings_ = limits()
        visitor = request_visitor(request)
        try:
            if "X-Real-IP" in request.headers or request.path not in PROBES:
                if over_limit("request", visitor, settings_["REQUESTS_PER_MINUTE"], 60):
                    log.info("requests over the limit from %s", visitor)
                    return plain("Too many requests. Wait a minute and try again.", 429)
            text_max = settings_["TEXT_MAX"]
            fields = [*request.GET.lists()]
            if request.method == "POST":
                fields += request.POST.lists()
            if any(len(value) > text_max for _, values in fields for value in values):
                return plain(f"A field is longer than {text_max} characters.", 400)
            if request.method == "POST" and over_limit("post", visitor, settings_["POSTS_PER_MINUTE"], 60):
                log.info("posts over the limit from %s", visitor)
                return plain("Too many requests. Wait a minute and try again.", 429)
        except LimiterDown:
            log.exception("the rate-limit counters are unreachable")
            return plain("The demo is busy. Try again in a minute.", 503)
        token = current_visitor.set(visitor)
        try:
            return self.get_response(request)
        finally:
            current_visitor.reset(token)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        # A view that creates a session past the budget (demo.sessions)
        from .sessions import SessionRefused

        if isinstance(exception, SessionRefused):
            return plain(exception.answer, exception.status)
        if isinstance(exception, LimiterDown):
            log.exception("the rate-limit counters are unreachable")
            return plain("The demo is busy. Try again in a minute.", 503)
        return None


def _cut(value: t.Any, text_max: int) -> t.Any:
    """``value`` with every string in it cut to ``text_max`` characters."""
    if isinstance(value, str):
        return value[:text_max]
    if isinstance(value, list):
        return [_cut(item, text_max) for item in value]
    if isinstance(value, dict):
        return {key: _cut(item, text_max) for key, item in value.items()}
    return value


def _cut_uri(uri: t.Any, text_max: int) -> t.Any:
    """``uri`` with each query value cut to ``text_max`` characters, and its path to ``2 * text_max``.

    Rebuilt only when something was cut, so a page's own address goes on byte for byte.
    """
    if not isinstance(uri, str):
        return uri
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    cut = [(name[:text_max], value[:text_max]) for name, value in query]
    path = parts.path[: 2 * text_max]
    if cut == query and path == parts.path and len(parts.fragment) <= text_max:
        return uri
    return urlunsplit((parts.scheme, parts.netloc[:text_max], path, urlencode(cut), parts.fragment[:text_max]))


def cut_payload(command: str, payload: t.Any, text_max: int) -> t.Any:
    """What the server reads of ``payload``, with every text a visitor wrote cut to ``text_max``.

    A join's state is signed and as long as the component's fields, so a join is left whole:
    ``FRAME_MAX`` bounds it, and the signature refuses anything a visitor made up.
    """
    if command == "join" or not isinstance(payload, dict):
        return payload
    cut = {key: _cut(value, text_max) for key, value in payload.items() if key != "uri"}
    if "uri" in payload:
        cut["uri"] = _cut_uri(payload["uri"], text_max)
    return cut


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


class _Budget:
    """One socket's buckets, and which states it has joined."""

    def __init__(self, settings_: dict[str, t.Any]):
        self.frames = _Bucket(settings_["FRAME_BURST"], settings_["FRAMES_PER_SECOND"])
        self.junk = _Bucket(settings_["JUNK_BURST"], settings_["JUNK_PER_SECOND"])
        self.events = _Bucket(settings_["EVENT_BURST"], settings_["EVENTS_PER_SECOND"])
        self.joins = _Bucket(settings_["JOIN_BURST"], settings_["JOINS_PER_SECOND"])
        self.renders = _Bucket(settings_["RENDER_BURST"], settings_["RENDERS_PER_SECOND"])
        self.states: set[bytes] = set()
        self.states_max = settings_["JOIN_BURST"] * 10

    def joined_before(self, payload: t.Any) -> bool:
        """Whether this socket joined this state before; remembers it if not."""
        state = payload.get("state") if isinstance(payload, dict) else None
        digest = hashlib.blake2b(str(state).encode(), digest_size=16).digest()
        if digest in self.states:
            return True
        if len(self.states) < self.states_max:
            self.states.add(digest)
            return False
        return True  # a socket that joined this much counts every join as a render


def _boot() -> str:
    # The open-socket counts belong to this start of the app: a crash that skipped the
    # decrements leaves counts that the next start does not read
    return getattr(settings, "DEMO_BOOT", "")


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
        budget = _Budget(settings_)
        window = settings_["VISITOR_WINDOW"]
        closed = False
        accepted = False
        held: list[str] = []  # the open-socket counters this socket holds

        async def close(code: int, why: str) -> dict:
            nonlocal closed
            log.info("closing a socket of %s: %s", visitor, why)
            if not closed:
                closed = True
                closing = asyncio.ensure_future(send({"type": "websocket.close", "code": code}))
                if accepted:
                    # uvicorn's close waits for the page's answer, which it reads only after the
                    # frames queued before it -- and a page that floods fills that queue. So what
                    # the page already sent is read and dropped until the socket is closed.
                    try:
                        async with asyncio.timeout(CLOSE_SECONDS):
                            while (await receive())["type"] != "websocket.disconnect":
                                pass
                    except TimeoutError:
                        log.warning("a socket of %s did not close in %ss", visitor, CLOSE_SECONDS)
                await closing
            # The consumer sees a disconnect, runs leaving() and lets go of its subscriptions
            return {"type": "websocket.disconnect", "code": code}

        async def guarded_send(message):
            nonlocal accepted
            # Once this middleware closed the socket, what the consumer still sends goes nowhere
            if not closed:
                accepted = accepted or message["type"] == "websocket.accept"
                await send(message)

        async def hold(kind: str, owner: str, most: int) -> bool:
            key = f"open:{_boot()}:{kind}"
            if await acount(key, owner, 24 * 3600) > most:
                await _release(key, owner)
                return False
            held.append(f"{key}|{owner}")
            return True

        async def connect(message) -> dict:
            if await aover_limit("socket", visitor, settings_["SOCKETS_PER_MINUTE"], 60):
                return await close(TOO_MANY, "new sockets over the limit")
            if not await hold("visitor", visitor, settings_["OPEN_SOCKETS_PER_VISITOR"]):
                return await close(TOO_MANY, "open sockets over the visitor's limit")
            if not await hold("all", "demo", settings_["OPEN_SOCKETS_TOTAL"]):
                return await close(TOO_MANY, "open sockets over the demo's limit")
            return message

        async def spend(bucket: _Bucket, kind: str, most: int, what: str) -> bool:
            if not bucket.take():
                return False
            if await acount(kind, visitor, window) > most:
                log.info("%s over the visitor's window from %s", what, visitor)
                return False
            return True

        async def frame(message) -> dict | None:
            """What the consumer hears of one frame: the frame, a cut copy, a disconnect, or None to skip it."""
            text = message.get("text")
            if text is None:
                return await close(TOO_BIG, "a binary frame")
            # Bytes, as FRAME_MAX says: a character of Korean is three, an emoji four
            size = len(text) if len(text) * 4 <= settings_["FRAME_MAX"] else len(text.encode("utf-8", "surrogatepass"))
            if size > settings_["FRAME_MAX"]:
                return await close(TOO_BIG, f"a frame of {size} bytes")
            if not await spend(budget.frames, "frames", settings_["VISITOR_FRAMES"], "frames"):
                return await close(TOO_MANY, "frames over the limit")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            command = parsed.get("command") if isinstance(parsed, dict) else None
            if command not in KNOWN_COMMANDS:
                if not budget.junk.take():
                    return await close(TOO_MANY, "frames the server does not read, over the limit")
                return None
            payload = parsed.get("payload")
            if command in EVENT_COMMANDS:
                if not await spend(budget.events, "events", settings_["VISITOR_EVENTS"], "events"):
                    return await close(TOO_MANY, "events over the limit")
            elif command == "join" and not budget.joined_before(payload):
                if not await spend(budget.joins, "renders", settings_["VISITOR_RENDERS"], "joins"):
                    return await close(TOO_MANY, "joins over the limit")
            elif command == "join" or command in RENDER_COMMANDS:
                if not await spend(budget.renders, "renders", settings_["VISITOR_RENDERS"], "renders"):
                    return await close(TOO_MANY, f"{command} over the limit")
            cut = cut_payload(command, payload, settings_["TEXT_MAX"])
            if cut != payload:
                return {**message, "text": json.dumps({**parsed, "payload": cut})}
            return message

        async def guarded_receive():
            while True:
                message = await receive()
                kind = message["type"]
                if closed or kind not in ("websocket.connect", "websocket.receive"):
                    return message
                try:
                    heard = await (connect(message) if kind == "websocket.connect" else frame(message))
                except LimiterDown:
                    log.exception("the rate-limit counters are unreachable")
                    return await close(TRY_LATER, "the counters are unreachable")
                if heard is not None:
                    return heard

        try:
            return await self.inner(scope, guarded_receive, guarded_send)
        finally:
            for item in held:
                key, owner = item.split("|", 1)
                await _release(key, owner)


def _decr(key: str) -> None:
    cache.decr(key)  # as with incr: the cache's own decr keeps the key's expiry


async def _release(key: str, owner: str) -> None:
    try:
        await _off_loop(_decr)(_key(key, owner))
    except ValueError:
        pass  # the count expired with its day
    except Exception:
        log.exception("could not give back an open-socket count")
