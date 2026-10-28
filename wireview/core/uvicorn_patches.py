"""A fix for a uvicorn defect that ends a server's graceful shutdown early (#226).

The defect
----------
On SIGTERM (a deploy, a restart, ``--reload``) uvicorn calls ``shutdown()`` on
every open WebSocket connection, and each sends a 1012 (service restart) close.
``WebSocketsSansIOProtocol`` -- what ``--ws auto`` picks since uvicorn 0.50 -- and
``WSProtocol`` (``--ws wsproto``) look only at ``close_sent``: whether the *server*
began the closing handshake. When the *page* began it -- its close frame arrived
and was answered, and ``connection_lost()`` has not run yet -- the protocol is no
longer open, so ``conn.send_close(1012)`` raises websockets' ``InvalidState``
("connection is closing") and ``conn.send(CloseConnection(1012))`` raises wsproto's
``LocalProtocolError``. Nothing catches it. ``Server.shutdown()`` stops at that
connection: the connections after it get no 1012, and the wait for the app tasks
-- where wireview runs ``leaving()`` and releases subscriptions (#211) -- is
skipped. A page navigating away as the server is told to stop is enough; the E2E
servers met it in 4 of 430 tests when the test server stopped (#225).

Reproduced on uvicorn 0.38.0 (websockets 15.0.1) and 0.54.0 (websockets 17.3, and
wsproto) by feeding a protocol the page's close frame and calling ``shutdown()``
before ``connection_lost()``; tests/test_uvicorn_patches.py does the same. The
sansio method has had the shape that does this since it appeared (0.35.0) and
still has it on uvicorn's main (2026-10-28); wsproto's since at least 0.34.0. It
did not show before 0.50 because ``--ws auto`` chose the legacy ``websockets``
implementation, which does not have it -- and which uvicorn 0.54 deprecates,
saying ``--ws websockets`` will point at the sansio one. Choosing it is no way out.

The patch
---------
It wraps ``shutdown()`` rather than replacing it: the original runs as it is, and
only the exception it raises for a connection that is no longer open is caught.
Every version above puts the disconnect on the app's queue *before* the send that
raises and closes the transport after it, so closing the transport is all that is
left to do. An open connection, and any other exception, go through untouched.

It goes on a class only while the method still has the defective shape: it sends
the close (``send_close`` / ``send``), puts the disconnect on the queue, closes the
transport, and reads no ``state`` -- the check upstream would add. A fixed method,
or one rewritten some other way, is left alone, the first quietly and the second
with a warning: the defect may be there and nothing covers it.

When it runs
------------
At the first WebSocket connection (``WireviewConsumer.websocket_connect``), not at
import or in ``AppConfig.ready()``. By then the server serving the socket has
imported its protocol class, and a shutdown can only meet a connection that
exists. ``ready()`` would have to import uvicorn (about 55 ms) in every process it
is installed in -- ``manage.py``, daphne -- and would still miss a server that
imported Django before uvicorn (``uvicorn.run(application)``, the E2E servers).
Only modules already in ``sys.modules`` are patched; nothing is imported. It is
process-wide: other apps' sockets on the same server get the fix too.

``WIREVIEW = {"PATCH_UVICORN_SHUTDOWN": False}`` leaves uvicorn alone.

Taking it out
-------------
When uvicorn ships a ``shutdown()`` that checks the connection's state, the shape
check stops applying this, and tests/test_uvicorn_patches.py's
``test_uvicorn_still_has_the_defect`` fails for that release. That is the signal:
remove this module once the oldest uvicorn the deployment guide sends people to has
the fix.
"""

from __future__ import annotations

import functools
import logging
import sys
import typing as t
from dataclasses import dataclass

log = logging.getLogger("wireview")

#: Set on the wrapper, so a class that has it is not wrapped twice
_MARK = "_wireview_uvicorn_patch"


def _sansio_failure() -> tuple[type[BaseException], t.Callable[[t.Any], bool]]:
    from websockets.exceptions import InvalidState
    from websockets.protocol import State

    return InvalidState, lambda conn: conn.state is State.OPEN


def _wsproto_failure() -> tuple[type[BaseException], t.Callable[[t.Any], bool]]:
    from wsproto.connection import ConnectionState
    from wsproto.utilities import LocalProtocolError

    return LocalProtocolError, lambda conn: conn.state == ConnectionState.OPEN


@dataclass(frozen=True)
class _Target:
    """One uvicorn WebSocket protocol class whose ``shutdown()`` has the defect."""

    module: str
    name: str
    #: The ``conn`` method that sends the 1012 close and raises when the connection is not open
    sends: str
    #: What that send raises, and whether the connection is open. Looked up only once
    #: the module is loaded, which has imported the library they come from.
    failure: t.Callable[[], tuple[type[BaseException], t.Callable[[t.Any], bool]]]


TARGETS = (
    _Target(
        "uvicorn.protocols.websockets.websockets_sansio_impl", "WebSocketsSansIOProtocol", "send_close", _sansio_failure
    ),
    _Target("uvicorn.protocols.websockets.wsproto_impl", "WSProtocol", "send", _wsproto_failure),
)

_applied = False


def _names(method: t.Any) -> set[str]:
    """The global and attribute names ``method``'s code uses.

    The names, not the source: they hold across the releases that have the defect
    (0.35.0's to 0.54.0's, which differ in what they do before the send), and the
    one upstream's fix needs -- ``state`` -- is not among them.
    """
    return set(getattr(getattr(method, "__code__", None), "co_names", ()))


def patch_shutdown(cls: type, target: _Target) -> str:
    """Wrap ``cls.shutdown`` if it has the defect; say what was done.

    Idempotent: a class that already carries the wrapper is left as it is.
    """
    original = cls.__dict__.get("shutdown")
    if original is None:
        return "skipped: no shutdown()"
    if getattr(original, _MARK, False):
        return "already applied"
    names = _names(original)
    if "state" in names:
        return "not needed: shutdown() checks the connection's state"
    if not {target.sends, "queue", "put_nowait", "conn", "transport", "close"} <= names:
        return "skipped: shutdown() is not the code this patch wraps"
    error, is_open = target.failure()

    @functools.wraps(original)
    def shutdown(self: t.Any) -> None:
        try:
            original(self)
        except error:
            if is_open(self.conn):
                raise
            # The page began the closing handshake: there is no 1012 to send. The
            # original queued the disconnect before it raised; this is its last line.
            self.transport.close()

    setattr(shutdown, _MARK, True)
    cls.shutdown = shutdown  # type: ignore[attr-defined]
    return "applied"


def apply() -> None:
    """Patch the uvicorn protocol classes this process has loaded, once.

    Called at every socket's connect, so it returns at once after the first. The
    outcome goes to the ``wireview`` logger at debug, or as a warning when a loaded
    class's ``shutdown()`` is neither the defective code nor a fixed one.
    """
    global _applied
    if _applied:
        return
    _applied = True

    from .. import settings

    if not settings.PATCH_UVICORN_SHUTDOWN:
        return
    for target in TARGETS:
        module = sys.modules.get(target.module)
        cls = getattr(module, target.name, None)
        if cls is None:
            continue
        outcome = patch_shutdown(cls, target)
        if outcome.startswith("skipped"):
            log.warning("uvicorn %s.shutdown() is not patched (#226): %s", target.name, outcome)
        else:
            log.debug("uvicorn %s.shutdown() patch (#226): %s", target.name, outcome)
