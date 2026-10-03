"""When a server run as a process of its own is ready: it answered a request of ours (#151, #152).

``testproj.e2e_server`` serves in a thread of the test process and hands the
server a socket it already bound. A server in another process -- the starter's
``runserver``, the upload suite's uvicorn workers -- binds its own, so the port
is chosen here and closed again until the server binds it. Another process can
take it in that gap, and then "something answers on the port" is someone else.

So every try carries a token, and ready is the token in *this* server's access
log. A bind failure in the log, or the process ending, fails at once with the
log, rather than after the deadline.
"""

from __future__ import annotations

import contextlib
import http.server
import os
import socket
import subprocess
import sys
import threading
import time
import typing as t
import urllib.request
import uuid
from pathlib import Path

from .time_limit import disown, own

#: The repository and its tests/ directory, which a server process runs from.
ROOT = Path(__file__).resolve().parent.parent.parent
TESTS = ROOT / "tests"

#: What each server logs when the port is taken. Daphne logs its line and exits
#: with 0; uvicorn logs its line and exits with 1.
BIND_FAILURES = (
    "Listen failure",  # daphne
    "error while attempting to bind",  # uvicorn
)


def free_port() -> int:
    """A port the OS handed out. Free when chosen, not reserved: the gap until the server binds it stays."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_serving(port: int, proc: subprocess.Popen, log: Path, timeout: float) -> None:
    """Until this server has answered a request of ours -- not until something answers on the port.

    ``log`` is where the server writes its access log, unbuffered: the request
    line with the token in it is the proof. Raises ``AssertionError`` with the log.
    """
    token = uuid.uuid4().hex
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = log.read_text(errors="replace")
        if any(mark in output for mark in BIND_FAILURES) or proc.poll() is not None:
            raise AssertionError(f"the server on port {port} did not serve (exit code {proc.poll()}):\n{output}")
        if f"?ready={token} " in output:
            return
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/?ready={token}", timeout=2).close()  # noqa: S310
        except OSError:  # nothing listening yet, or an error status: the log decides
            pass
        time.sleep(0.2)
    output = log.read_text(errors="replace")
    raise AssertionError(f"the server on port {port} did not answer within {timeout:g}s:\n{output}")


class _Answering(http.server.BaseHTTPRequestHandler):
    """Another server, answering every request with a 200."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"someone else")

    def log_message(self, *args: t.Any) -> None:
        pass


@contextlib.contextmanager
def held_port() -> t.Iterator[int]:
    """A port another server holds and answers on, for the gap ``free_port()`` leaves."""
    other = http.server.HTTPServer(("127.0.0.1", 0), _Answering)
    thread = threading.Thread(target=other.serve_forever, daemon=True)
    thread.start()
    try:
        yield other.server_address[1]
    finally:
        other.shutdown()
        other.server_close()
        thread.join()


def launch_uvicorn(port: int, log_dir: Path) -> tuple[subprocess.Popen, Path]:
    """One uvicorn process serving the test project, on this interpreter and the configured layer.

    Its output goes to a file rather than to the void: when a request comes back
    500 the traceback is on the server, not in the test. At ``info``, because the
    access log in that file is what says this server, not another process on its
    port, answered (:func:`wait_until_serving`); unbuffered, so what the
    application prints lands in order with it. Appended, so a server started
    again on the same port (a restart) keeps the first one's log above its own.
    """
    log = log_dir / f"worker-{port}.log"
    with log.open("ab") as out:
        # A stopped run skips the fixture's teardown; the stop terminates what it owns.
        proc = own(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "testproj.asgi:application",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "info",
                ],
                cwd=TESTS,
                env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(TESTS)]), "PYTHONUNBUFFERED": "1"},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        )
    return proc, log


def start_uvicorn(ports: list[int], log_dir: Path, timeout: float) -> list[subprocess.Popen]:
    """Launch a server on each port, then wait for each against one deadline; stop them all if one did not serve."""
    launched = [launch_uvicorn(port, log_dir) for port in ports]
    deadline = time.monotonic() + timeout
    try:
        for port, (proc, log) in zip(ports, launched):
            wait_until_serving(port, proc, log, max(deadline - time.monotonic(), 0.0))
    except BaseException:
        for proc, _ in launched:
            stop_process(proc)
        raise
    return [proc for proc, _ in launched]


def stop_process(proc: subprocess.Popen) -> None:
    """Stop a server process and wait for it, killing it if it does not go."""
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
        proc.kill()
        proc.wait()
    disown(proc)
