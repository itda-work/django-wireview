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
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import typing as t
from time import monotonic, sleep

from channels.routing import get_default_application
from django.test import override_settings
from uvicorn.config import Config as UvicornConfig
from uvicorn.main import Server as Uvicorn

__all__ = ["UvicornThread", "serve"]

#: How long to wait for the server to come up, and to go away again.
STARTUP_TIMEOUT = 15.0
SHUTDOWN_TIMEOUT = 15.0


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
        try:
            self.loop = asyncio.new_event_loop()
            config = UvicornConfig(self.application, host=self.host, port=self.port, log_level="warning")
            self.server = Uvicorn(config)
            self.server.install_signal_handlers = lambda *args, **kwargs: None
            self.loop.run_until_complete(self.server.serve(sockets=[self.sock] if self.sock else None))
        except BaseException as e:  # noqa: BLE001 - carried out through .error
            self.error = e
        finally:
            self.server = None

    @property
    def started(self) -> bool:
        return self.server is not None and self.server.started

    def terminate(self) -> None:
        if self.server:
            self.server.force_exit = True
            self.server.should_exit = True
            if self.loop:
                self.loop.create_task(self.server.shutdown())


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
    with override_settings(DEBUG=True), contextlib.closing(sock):
        thread.start()
        deadline = monotonic() + STARTUP_TIMEOUT
        while not thread.started:
            if thread.error is not None:
                raise AssertionError(f"the test server did not start: {thread.error!r}")
            if monotonic() > deadline:
                thread.terminate()
                raise AssertionError(f"the test server did not start within {STARTUP_TIMEOUT}s")
            sleep(0.05)

        try:
            yield f"http://{host}:{port}"
        finally:
            thread.terminate()
            # Inside the override, so it is still in place while the thread reads
            # settings on its way out; and waited for, because a server that has
            # only been asked to stop is still bound to its port and still
            # answering, which the next test would get instead of its own.
            thread.join(timeout=SHUTDOWN_TIMEOUT)

    assert not thread.is_alive(), "the test server did not stop"
    if thread.error is not None:
        raise AssertionError(f"the test server failed: {thread.error!r}")
