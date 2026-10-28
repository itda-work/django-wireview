"""What a server's graceful stop leaves of the ``leaving()`` hooks of its open connections (#211).

wireview ends a session however its app ends -- a disconnect, or the server
cancelling the app -- and does not swallow that cancellation. Whether the
ending runs to its end is the server's to decide, and docs/DEPLOYMENT.md
("SIGTERM 드레인 절차") says what each does. These run the real servers on the
test project (``testproj.endprobe``), two sockets each, one hook quick and one
slow:

- uvicorn closes every socket (1012) and waits for the apps up to
  ``--timeout-graceful-shutdown``: both hooks end. Past it, it cancels and
  exits: the slow one does not.
- daphne sends no disconnect; it cancels every app and waits on them with a
  ``gather`` that ends with the first app to end (``kill_all_applications``).
  Both hooks start, and the slow one does not end.

A server that changes what it does fails here, and the deployment guide with it.

On Windows ``send_signal(SIGTERM)`` is TerminateProcess, which runs no shutdown at all. Its
graceful stop is Ctrl+Break: the server runs in its own process group and gets
``CTRL_BREAK_EVENT``, which uvicorn handles as SIGBREAK and Twisted (daphne) shuts down on.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from testproj.endprobe import EndProbe
from testproj.server_process import ROOT, TESTS, free_port, wait_until_serving
from testproj.time_limit import disown, own
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

WINDOWS = sys.platform == "win32"

#: How long the slow hook takes: well past the quick one's end, well inside the time limit
SLOW = 1.5


def _launch(server: str, port: int, log: Path, events: Path, graceful: int) -> subprocess.Popen:
    if server == "daphne":
        command = ["daphne", "-b", "127.0.0.1", "-p", str(port), "testproj.endprobe:application"]
    else:
        command = [
            "uvicorn",
            "testproj.endprobe:application",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "off",
            "--timeout-graceful-shutdown",
            str(graceful),
        ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(TESTS)]),
        "PYTHONUNBUFFERED": "1",
        "ENDPROBE_EVENTS": str(events),
        # One process: the layer changes nothing here
        "WIREVIEW_TEST_LAYER": "memory",
    }
    # Its own process group, so that the Ctrl+Break reaches the server and not this run
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    with log.open("ab") as out:
        return own(
            subprocess.Popen(
                [sys.executable, "-m", *command],
                cwd=TESTS,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        )


def _stop(proc: subprocess.Popen) -> None:
    """The server's graceful stop: SIGTERM, or on Windows Ctrl+Break (SIGTERM there is TerminateProcess)."""
    proc.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGTERM)


def _join(port: int, id: str, seconds: float) -> ClientConnection:
    # The caller holds it past this function and closes it (websockets 17 asks for legacy= to say so)
    socket = connect(f"ws://127.0.0.1:{port}/__wireview__?vsn={PROTOCOL_VERSION}", legacy=True)
    state = sign_state(EndProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, seconds=seconds))
    socket.send(json.dumps({"command": "join", "payload": {"name": "EndProbe", "state": state, "children": {}}}))
    while json.loads(socket.recv(timeout=5))["command"] != "joined":
        pass
    return socket


def _close_code(socket: ClientConnection) -> int | None:
    """What the server closed it with, once it did; None when nothing came."""
    try:
        while True:
            socket.recv(timeout=5)
    except ConnectionClosed:
        pass
    except TimeoutError:
        return None
    return socket.close_code


def _shut_down(
    server: str, tmp_path: Path, *, graceful: int = 10, slow: float = SLOW
) -> tuple[list[str], list[int | None]]:
    """Two joined sockets, then the graceful stop: what the hooks recorded, and the close code each socket got.

    Synchronous: the E2E run's Playwright holds an event loop on this thread.
    """
    port = free_port()
    log = tmp_path / f"{server}.log"
    events = tmp_path / f"{server}.events"
    events.write_text("", encoding="utf-8")
    proc = _launch(server, port, log, events, graceful)
    sockets: list[ClientConnection] = []
    try:
        wait_until_serving(port, proc, log, 20)
        sockets = [_join(port, "quick", 0), _join(port, "slow", slow)]
        if server == "daphne":
            # Its application checker (every second) retires the readiness requests' entries: one
            # it has not yet is an entry without an app, and kill_all_applications raises on it
            time.sleep(2.2)
        _stop(proc)
        proc.wait(20)
        codes = [_close_code(socket) for socket in sockets]
        return events.read_text(encoding="utf-8").splitlines(), codes
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        disown(proc)
        for socket in sockets:
            socket.close()


def test_uvicorn_ends_every_hook_within_its_graceful_timeout(tmp_path):
    """It closes the sockets with 1012 and waits for the apps: each hook runs to its end."""
    events, codes = _shut_down("uvicorn", tmp_path)

    assert sorted(events) == ["done quick", "done slow", "start quick", "start slow"]
    assert codes == [1012, 1012]


def test_uvicorn_past_its_graceful_timeout_does_not_end_a_slow_hook(tmp_path):
    """Past ``--timeout-graceful-shutdown`` it cancels the apps and exits: the slow hook does not end."""
    events, _ = _shut_down("uvicorn", tmp_path, graceful=1, slow=4)

    assert {"start quick", "start slow", "done quick"} <= set(events)
    assert "done slow" not in events


def test_daphne_starts_every_hook_and_ends_with_the_first_app_to_end(tmp_path):
    """No disconnect, the apps cancelled: every hook starts, and the process exits before the slow one ends."""
    events, _ = _shut_down("daphne", tmp_path)

    assert {"start quick", "start slow", "done quick"} <= set(events)
    assert "done slow" not in events
