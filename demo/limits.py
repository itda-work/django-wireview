"""What a visitor of the public demo may send, enforced without touching the examples.

Over HTTP, ``RequestLimitMiddleware`` counts each visitor's requests and form posts, refuses a
field longer than ``TEXT_MAX``, and keeps a visitor from minting stored sessions without end
(``demo.sessions``). Over the WebSocket, ``SocketLimits`` wraps the route:

- at the handshake it counts the visitor's new sockets, and takes a lease on a place among the
  sockets the visitor holds open and among all the demo holds open (``Leases``);
- every frame is measured in UTF-8 bytes against ``FRAME_MAX`` and spends the socket's frame
  bucket and the visitor's frame window;
- a frame that is not JSON or names no command the server knows spends a small junk bucket and
  goes no further, so it neither reaches the consumer nor fills the log;
- events (``user_event``, ``hook_event``, uploads) spend the socket's event bucket and the
  visitor's event window;
- what makes the server restore or re-render a component without a click spends the socket's
  render bucket: a join of a component this socket has joined before, a join whose state does
  not verify, ``navigated``, and a ``params_changed`` no event asked for. The first join of each
  component -- its class and id, read from the verified state (``join_identity``) -- spends a
  bucket sized for a page's first joins. A ``params_changed`` that follows an event is the
  address that event patched (a search box's ``.debounce.300`` does it three times a second
  while someone types): the event paid for it. All of them count in the visitor's render window;
- every text a frame carries is cut to ``TEXT_MAX``, the query of a ``uri`` included, except a
  join's signed state.

A bucket lives with its socket; a window lives in Redis per visitor, so a visitor who
reconnects or opens more sockets does not start over. A page that is closed for going too fast
reconnects on its own after the client's backoff, so a visitor who only clicks never sees any
of it.

An open socket holds a lease, not a count: it renews its place every ``RENEW_SECONDS`` and the
place lapses ``LEASE_SECONDS`` after the last renewal. A worker that dies without closing its
sockets (SIGKILL, the OOM killer) leaves leases that lapse on their own, and a set with no live
lease left is gone from Redis. Every other key the limits write lives no longer than its window.

The visitor is the address Caddy read from Cloudflare's ``CF-Connecting-IP`` -- in direct mode
from the host Caddy's ``X-Real-IP``, the TCP peer (demo/Caddyfile.direct) -- and passed on as
``X-Real-IP`` (demo/Caddyfile); an IPv6 address counts as its /64, the block one subscriber
holds. Nothing reaches the app except through Caddy, so the header is not the browser's to
choose. The counters are in a Redis of their own that never evicts (compose.yaml): when it is
full or gone, a limit refuses rather than lets through (``LimiterDown``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import threading
import time
import typing as t
import uuid
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.redis import RedisCache
from django.http import HttpRequest, HttpResponse
from redis.exceptions import OutOfMemoryError

log = logging.getLogger("demo.limits")

#: The close code for "too much": the client reconnects after its backoff
TOO_MANY = 4029
#: RFC 6455's "message too big"
TOO_BIG = 1009
#: RFC 6455's "try again later": the counters could not be reached
TRY_LATER = 1013
#: How long closing a socket may take, reading out what the page had already sent
CLOSE_SECONDS = 10
#: How long an open socket's place lasts without a renewal, and how often a socket renews it
LEASE_SECONDS = 90
RENEW_SECONDS = 30
#: Event-paid ``params_changed`` a socket may have in hand: an event patches the address once
PARAMS_CREDITS = 2

#: What a person causes: these spend the event budget
EVENT_COMMANDS = frozenset({"user_event", "hook_event", "upload_register", "upload_cancel", "upload_complete"})
#: What makes the server restore or render a component without a click
RENDER_COMMANDS = frozenset({"params_changed", "navigated"})
#: Everything else the server knows (wire-protocol.md §2); any other command is junk
KNOWN_COMMANDS = EVENT_COMMANDS | RENDER_COMMANDS | {"join", "leave", "sync"}

#: The visitor of this request, for ``demo.sessions`` (None outside a request: the WebSocket)
current_visitor: ContextVar[str | None] = ContextVar("demo_visitor", default=None)


#: The clocks the limits read: a socket's buckets the monotonic one, leases the wall one (every
#: worker reads the same Redis). Module names, so a test can move time
clock = time.monotonic
wall = time.time


class LimiterDown(Exception):
    """The counters could not be read or written: a limit refuses rather than lets through.

    ``full``: Redis refused the write for want of memory (it evicts nothing), which windows
    expiring frees in minutes and the janitor reports (demo_janitor); otherwise it did not answer.
    """

    def __init__(self, what: str, full: bool = False):
        super().__init__(what)
        self.full = full

    @classmethod
    def from_error(cls, what: str, error: Exception) -> LimiterDown:
        # redis-py turns Redis's "OOM command not allowed ..." into OutOfMemoryError and drops the "OOM"
        return cls(what, full=isinstance(error, OutOfMemoryError) or "maxmemory" in str(error))

    @property
    def answer(self) -> str:
        if self.full:
            return "The demo has more visitors than it can keep track of right now. Try again in a few minutes."
        return "The demo cannot check its limits right now. Try again in a few minutes."


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


def _redis():
    """The counters' Redis client, or None when the cache is not Redis (the test suite's)."""
    backend = caches["default"]
    if isinstance(backend, RedisCache):
        return backend._cache.get_client(write=True)
    return None


def count(kind: str, visitor: str, window: int) -> int:
    """One more of ``kind`` for ``visitor`` in a fixed window that starts at the first one; the count so far.

    In Redis, one transaction: SET NX EX opens the window if there is none, INCR counts. Both run
    together, so a window that ends between them cannot leave a key without its expiry (the
    cache's own ``incr`` checks the key and increments it in two steps), INCR keeps the expiry,
    so the window does not grow with every hit, and workers do not lose counts.
    """
    key = _key(kind, visitor)
    try:
        client = _redis()
        if client is not None:
            name = cache.make_and_validate_key(key)
            with client.pipeline(transaction=True) as pipe:
                pipe.set(name, 0, ex=window, nx=True)
                pipe.incr(name)
                return pipe.execute()[1]
        try:
            return cache.incr(key)
        except ValueError:  # no window yet, or it just ended
            if cache.add(key, 1, window):
                return 1
            return cache.incr(key)
    except ValueError:
        return 1
    except Exception as error:
        raise LimiterDown.from_error(kind, error) from error


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
        except LimiterDown as down:
            log.exception("the rate-limit counters are unreachable or full")
            return plain(down.answer, 503)
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
            log.exception("the rate-limit counters are unreachable or full")
            return plain(exception.answer, 503)
        from .retention import StorageFull

        if isinstance(exception, StorageFull):
            return plain(exception.answer, 503)
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
        self.at = clock()

    def take(self) -> bool:
        now = clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.per_second)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class _Budget:
    """One socket's buckets, which components it has joined, and the ``params_changed`` its events paid for."""

    def __init__(self, settings_: dict[str, t.Any]):
        self.frames = _Bucket(settings_["FRAME_BURST"], settings_["FRAMES_PER_SECOND"])
        self.junk = _Bucket(settings_["JUNK_BURST"], settings_["JUNK_PER_SECOND"])
        self.events = _Bucket(settings_["EVENT_BURST"], settings_["EVENTS_PER_SECOND"])
        self.joins = _Bucket(settings_["JOIN_BURST"], settings_["JOINS_PER_SECOND"])
        self.renders = _Bucket(settings_["RENDER_BURST"], settings_["RENDERS_PER_SECOND"])
        self.components: set[tuple[str, str]] = set()
        self.components_max = settings_["JOIN_BURST"] * 10
        self.params_credits = 0

    def first_join(self, identity: tuple[str, str] | None) -> bool:
        """Whether this is the socket's first join of the component; remembers it if so.

        A state that does not verify is no component's first join: the server refuses it, and
        what that costs is what a rejoin costs.
        """
        if identity is None or identity in self.components:
            return False
        if len(self.components) >= self.components_max:
            return False  # a socket that joined this much counts every join as a render
        self.components.add(identity)
        return True

    def event_passed(self) -> None:
        self.params_credits = min(PARAMS_CREDITS, self.params_credits + 1)

    def paid_params(self) -> bool:
        """Whether an event paid for this ``params_changed``; spends the credit if so."""
        if self.params_credits:
            self.params_credits -= 1
            return True
        return False


def join_identity(payload: t.Any) -> tuple[str, str] | None:
    """The component a join's state was signed for, as (class, id); None when the state does not verify.

    Reads wireview's internal ``unsign_envelope`` and ``Component._resolve``: the library has no
    public call for it (demo/README.md, "라이브러리 내부에 기대는 곳"). The class, not the name the
    page sent: ``app:Name`` and ``Name`` are one component.
    """
    from wireview.core.component import Component
    from wireview.core.state import unsign_envelope

    if not isinstance(payload, dict):
        return None
    name, state = payload.get("name"), payload.get("state")
    if not isinstance(name, str) or not isinstance(state, str):
        return None
    try:
        signed = unsign_envelope(state, name)
        component = Component._resolve(name)
    except Exception:
        return None
    component_id = signed.state.get("id")
    return component._fqn, component_id if isinstance(component_id, str) else ""


class Leases:
    """Places among open sockets: a sorted set per owner, a member per socket, scored by when it lapses.

    Taking one first drops the lapsed members and counts the rest, in one script, so workers
    taking places at once never overshoot. The set's own expiry is a lease's length, pushed out by
    every take and renewal: a set whose sockets are all gone goes too, and so does the last
    member's ZREM. Outside Redis (the test suite's cache), the same rules over the cache, under a
    lock.
    """

    TAKE = """
local now, lapses, ttl, member = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
for i, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  if redis.call('ZCARD', key) >= tonumber(ARGV[4 + i]) then
    return i
  end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, lapses, member)
  redis.call('EXPIRE', key, ttl)
end
return 0
"""
    RENEW = """
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, ARGV[1], ARGV[2])
  redis.call('EXPIRE', key, ARGV[3])
end
return 0
"""
    _lock = threading.Lock()

    @staticmethod
    def _names(keys: t.Iterable[str]) -> list[str]:
        return [caches["default"].make_and_validate_key(_key("open", key)) for key in keys]

    @classmethod
    def take(cls, places: dict[str, int], member: str) -> str | None:
        """A place under each of ``places`` (key -> most) for ``member``, or the key that is full."""
        keys = list(places)
        now = wall()
        try:
            client = _redis()
            if client is not None:
                full = client.eval(
                    cls.TAKE, len(keys), *cls._names(keys), now, now + LEASE_SECONDS, LEASE_SECONDS, member,
                    *places.values(),
                )  # fmt: skip
                return keys[full - 1] if full else None
            with cls._lock:
                held = {key: cls._local(key, now) for key in keys}
                for key, most in places.items():
                    if len(held[key]) >= most:
                        return key
                for key in keys:
                    held[key][member] = now + LEASE_SECONDS
                    cache.set(_key("open", key), held[key], LEASE_SECONDS)
            return None
        except Exception as error:
            raise LimiterDown.from_error("open sockets", error) from error

    @classmethod
    def renew(cls, keys: list[str], member: str) -> None:
        now = wall()
        client = _redis()
        if client is not None:
            client.eval(cls.RENEW, len(keys), *cls._names(keys), now + LEASE_SECONDS, member, LEASE_SECONDS)
            return
        with cls._lock:
            for key in keys:
                held = cls._local(key, now)
                held[member] = now + LEASE_SECONDS
                cache.set(_key("open", key), held, LEASE_SECONDS)

    @classmethod
    def release(cls, keys: list[str], member: str) -> None:
        client = _redis()
        if client is not None:
            with client.pipeline(transaction=True) as pipe:
                for name in cls._names(keys):
                    pipe.zrem(name, member)
                pipe.execute()
            return
        with cls._lock:
            for key in keys:
                held = cls._local(key, wall())
                held.pop(member, None)
                if held:
                    cache.set(_key("open", key), held, LEASE_SECONDS)
                else:
                    cache.delete(_key("open", key))

    @classmethod
    def held(cls, key: str) -> int:
        """How many live places ``key`` holds."""
        now = wall()
        client = _redis()
        if client is not None:
            return client.zcount(cls._names([key])[0], f"({now}", "+inf")
        return len(cls._local(key, now))

    @staticmethod
    def _local(key: str, now: float) -> dict[str, float]:
        return {member: lapses for member, lapses in cache.get(_key("open", key), {}).items() if lapses > now}


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
        member = uuid.uuid4().hex  # this socket, in the open-socket leases
        leased: list[str] = []  # the leases it holds
        renewing: asyncio.Task | None = None

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

        async def renew() -> None:
            while True:
                await asyncio.sleep(RENEW_SECONDS)
                try:
                    await _off_loop(Leases.renew)(leased, member)
                except Exception:
                    # The next renewal tries again; a lease lapses only after three misses
                    log.warning("could not renew an open socket's lease", exc_info=True)

        async def connect(message) -> dict:
            nonlocal renewing
            if await aover_limit("socket", visitor, settings_["SOCKETS_PER_MINUTE"], 60):
                return await close(TOO_MANY, "new sockets over the limit")
            places = {
                f"visitor:{visitor}": settings_["OPEN_SOCKETS_PER_VISITOR"],
                "all": settings_["OPEN_SOCKETS_TOTAL"],
            }
            full = await _off_loop(Leases.take)(places, member)
            if full is not None:
                whose = "the demo's" if full == "all" else "the visitor's"
                return await close(TOO_MANY, f"open sockets over {whose} limit")
            leased.extend(places)
            renewing = asyncio.ensure_future(renew())
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
                budget.event_passed()
            elif command == "join" and budget.first_join(join_identity(payload)):
                if not await spend(budget.joins, "renders", settings_["VISITOR_RENDERS"], "joins"):
                    return await close(TOO_MANY, "joins over the limit")
            elif command == "params_changed" and budget.paid_params():
                pass  # the address an event patched: the event paid for it
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
                    log.exception("the rate-limit counters are unreachable or full")
                    return await close(TRY_LATER, "the counters are unreachable or full")
                if heard is not None:
                    return heard

        try:
            return await self.inner(scope, guarded_receive, guarded_send)
        finally:
            if renewing is not None:
                renewing.cancel()
            if leased:
                try:
                    await _off_loop(Leases.release)(leased, member)
                except Exception:
                    # The lease lapses on its own in LEASE_SECONDS
                    log.warning("could not give back an open socket's lease", exc_info=True)
