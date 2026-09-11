"""The live ASGI server the E2E suites point a browser at.

One copy, because there used to be four and they all carried the same two
defects.

**The settings override has to outlive the thread, not ride inside it.**
Decorating ``run()`` with ``override_settings(DEBUG=True)`` enters and leaves a
*global* override on the server thread's own schedule. A thread still winding
down when the next test starts restores settings underneath that test, and the
symptom -- ``'override_settings' object has no attribute 'wrapped'`` -- is raised
inside the thread, where pytest files it as a warning after a green run. So the
override wraps the fixture, and the fixture waits for the thread.

**A server nobody waited for is still serving.** Asking Uvicorn to stop and
returning immediately leaves a socket bound and a request loop running into the
next test, which then gets answers from the previous test's application.

**The port is bound before the thread starts, not guessed.** A random port nobody
checked is a collision waiting for a busy machine, and it fails in the way that is
hardest to chase: once, and never again on the retry.

Anything that fails in the thread is carried back out so a dead server fails the
test that needed it rather than looking like a slow one.

**What the server logs is carried out too.** A handler that raises does not fail
the request the browser can see -- ``receive_json`` has no catch, so Channels
tears the socket down and the page simply stops updating. From the test that
looks identical to a slow one: an element that never appears. So the errors the
server logs while the block runs are collected, and a test that is about to fail
on a missing element can say what the server said (:func:`server_errors`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import sys
import threading
import typing as t
from time import monotonic, sleep

from channels.routing import get_default_application
from django.test import override_settings
from uvicorn.config import Config as UvicornConfig
from uvicorn.main import Server as Uvicorn

log = logging.getLogger(__name__)

__all__ = ["UvicornThread", "serve", "server_errors"]

#: How long to wait for the server to come up, and to go away again.
STARTUP_TIMEOUT = 15.0
SHUTDOWN_TIMEOUT = 15.0


class _ErrorCollector(logging.Handler):
    """Keeps the formatted ERROR records logged anywhere while a server runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:  # pragma: no cover - a broken formatter must not break the test
            self.records.append(f"{record.name}: {record.getMessage()}")


#: The collector for the ``serve()`` block currently running, if any. One at a
#: time because a test runs one server; nested blocks would need a stack and no
#: suite has ever wanted one.
_collector: "_ErrorCollector | None" = None


def server_errors() -> list[str]:
    """Errors logged since the current ``serve()`` block started.

    Empty outside a block. Meant for a failure message -- "the element never
    appeared" and "the socket died on an exception" look the same to a browser,
    and only one of them is a timeout worth retrying.
    """
    return list(_collector.records) if _collector is not None else []


class UvicornThread(threading.Thread):
    """Runs one ASGI application on a thread, for the length of one test.

    The listening socket is bound by the caller and handed over, so the port is
    one the OS said was free rather than one that was picked and hoped for.
    """

    def __init__(self, application: t.Any, host: str, port: int, sock: "socket.socket | None" = None) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.sock = sock
        self.application = application
        self.server: Uvicorn | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        #: Whatever stopped the thread, so the caller can raise it instead of
        #: waiting out a timeout on a server that already died.
        self.error: BaseException | None = None

    def run(self) -> None:
        loop = None
        try:
            loop = asyncio.new_event_loop()
            self.loop = loop
            config = UvicornConfig(self.application, host=self.host, port=self.port, log_level="warning")
            self.server = Uvicorn(config)
            self.server.install_signal_handlers = lambda *args, **kwargs: None
            loop.run_until_complete(self.server.serve(sockets=[self.sock] if self.sock else None))
        except BaseException as e:  # noqa: BLE001 - carried out through .error
            self.error = e
        finally:
            self.server = None
            if loop is not None:
                # A joined thread is not a closed loop, and an unclosed loop keeps
                # its selector and its descriptors. One per test adds up.
                loop.close()

    @property
    def started(self) -> bool:
        return self.server is not None and self.server.started

    def terminate(self) -> None:
        server, loop = self.server, self.loop
        if not server:
            return
        server.force_exit = True
        server.should_exit = True
        if loop is not None and not loop.is_closed():
            # From another thread, which is the only place this is called from.
            # ``create_task`` is not safe across threads; this is the way in.
            try:
                loop.call_soon_threadsafe(lambda: loop.create_task(server.shutdown()))
            except RuntimeError:  # pragma: no cover - the loop closed first
                pass


@contextlib.contextmanager
def serve(application: t.Any = None) -> t.Iterator[str]:
    """Run a server for the body of a test and yield its base URL.

    Use it from a function-scoped fixture::

        @pytest.fixture
        def server():
            with serve() as base_url:
                yield base_url
    """
    host = "127.0.0.1"
    # Bound here, before the thread starts, so nothing can take the port between
    # choosing it and listening on it. Picking a random one and hoping produced the
    # least diagnosable failure there is: two E2E tests failing once and passing on
    # every retry afterwards.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    thread = UvicornThread(application or get_default_application(), host, port, sock=sock)
    with override_settings(DEBUG=True), contextlib.closing(sock), _collecting_errors():
        # Everything after ``start()`` is inside the same cleanup, the wait for
        # startup included. Guarding only the body left the timeout path handing
        # back a live thread and then restoring settings out from under it --
        # the original bug, in the branch nobody was looking at.
        thread.start()
        try:
            failure = _wait_until_serving(thread)
            if failure:
                raise AssertionError(failure)
            yield f"http://{host}:{port}"
        finally:
            # Inside the override, so it is still in place while the thread reads
            # settings on its way out; and waited for, because a server that has
            # only been asked to stop is still bound to its port and still
            # answering, which the next test would get instead of its own.
            _stop(thread)


@contextlib.contextmanager
def _collecting_errors() -> t.Iterator[None]:
    """Attach the collector to the root logger for the length of the block.

    On the root logger rather than on ``wireview``: the exception that kills a
    socket is logged by whoever caught it last, and that is Channels or Uvicorn,
    not this package.
    """
    global _collector
    handler = _ErrorCollector()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous, _collector = _collector, handler
    try:
        yield
    finally:
        _collector = previous
        root.removeHandler(handler)


def _stop(thread: UvicornThread) -> None:
    """Ask the server to stop and account for whether it did.

    Called from a ``finally``, so it has to be careful about what it raises: an
    exception here would replace whatever sent us here, and the original failure
    is the more useful one. A shutdown that did not finish is therefore reported
    only when nothing else is on its way out -- and Python cannot force a thread
    to end, so this reports rather than guarantees.
    """
    thread.terminate()
    thread.join(timeout=SHUTDOWN_TIMEOUT)
    if sys.exc_info()[0] is not None:
        if thread.is_alive():
            log.error("the test server did not stop, and something else was already failing")
        return
    assert not thread.is_alive(), "the test server did not stop"
    if thread.error is not None:
        raise AssertionError(f"the test server failed: {thread.error!r}")


def _wait_until_serving(thread: UvicornThread) -> str:
    """Block until the server is up. Returns why it is not, or ``""`` if it is."""
    deadline = monotonic() + STARTUP_TIMEOUT
    while not thread.started:
        if thread.error is not None:
            return f"the test server did not start: {thread.error!r}"
        if monotonic() > deadline:
            return f"the test server did not start within {STARTUP_TIMEOUT}s"
        sleep(0.05)
    return ""
