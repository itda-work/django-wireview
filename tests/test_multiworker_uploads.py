"""A chunked upload that never touches the worker holding the WebSocket (#83).

Two real ASGI servers, one broker, no sticky routing: the page and the WebSocket
are on worker A, every chunk is POSTed to worker B. AC1 and AC2 of #83 in the
shape a deployment actually has them -- the URLconf, the middleware and the
channel layer included, not just the view.

What proves it is not the HTTP status. It is that ``on_upload_complete`` on
worker A reads the bytes worker B wrote, and the digest it puts in its state
comes back over the WebSocket.

Needs a broker: with ``WIREVIEW_TEST_LAYER=memory`` the two workers cannot hear
each other at all, which is the point of the setting, so the test skips there.
``tests/e2e.sh`` starts a nats-server and runs this.

Synchronous on purpose. The rest of the E2E suite drives Playwright's sync API,
which holds an event loop of its own, and an ``asyncio`` test scheduled after one
of those cannot start a second one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import typing as t
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

#: Big enough to need several chunks at the size the component asks for.
PAYLOAD = ("wireview #83 multi-worker upload\n" * 400).encode()
CHUNK_SIZE = 4096


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"worker on port {port} exited with {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"nothing listening on port {port} after {timeout:.0f}s")


def _start_worker(port: int, log_dir: Path) -> subprocess.Popen:
    """One uvicorn process, on this interpreter, sharing the configured layer.

    Its output goes to a file rather than to the void: when a chunk comes back
    500 the traceback is on the worker, not in the test.
    """
    log = open(log_dir / f"worker-{port}.log", "wb")  # noqa: SIM115 (lives as long as the process)
    return subprocess.Popen(
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
            "warning",
        ],
        cwd=TESTS,
        env={**os.environ, "DJANGO_ALLOW_ASYNC_UNSAFE": "1", "PYTHONPATH": os.pathsep.join([str(ROOT), str(TESTS)])},
        stdout=log,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="module")
def workers(tmp_path_factory):
    """Two servers on one channel layer, as a deployment behind a load balancer."""
    if os.environ.get("WIREVIEW_TEST_LAYER", "memory") == "memory":
        pytest.skip("two workers need a broker; run with WIREVIEW_TEST_LAYER=nats or redis")
    pytest.importorskip("uvicorn")

    log_dir = tmp_path_factory.mktemp("workers")
    ports = [_free_port(), _free_port()]
    procs = [_start_worker(port, log_dir) for port in ports]
    try:
        for port, proc in zip(ports, procs):
            _wait_for_port(port, proc)
        yield ports
    except Exception:  # pragma: no cover - only when a worker misbehaves
        for port in ports:
            print((log_dir / f"worker-{port}.log").read_text())
        raise
    finally:
        for port in ports:
            log = log_dir / f"worker-{port}.log"
            if "Traceback" in log.read_text():
                print(f"--- worker {port} ---\n{log.read_text()}")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a wedged worker
                proc.kill()


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - a localhost test server
        return response.read().decode()


def _post_chunk(url: str, chunk: bytes, headers: dict[str, str]) -> dict[str, t.Any]:
    request = urllib.request.Request(url, data=chunk, method="POST")  # noqa: S310
    request.add_header("Content-Type", "application/octet-stream")
    for name, value in headers.items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def _next_command(ws, command: str, predicate=None, timeout: float = 20.0) -> dict[str, t.Any]:
    """The next message of one kind, ignoring renders and heartbeats in between."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = json.loads(ws.recv(timeout=timeout))
        if message["command"] != command:
            continue
        if predicate is None or predicate(message["payload"]):
            return message["payload"]
    raise AssertionError(f"no {command} matching within {timeout:.0f}s")


def test_the_chunks_go_to_the_other_worker(workers):
    """The WebSocket is on A, every chunk is POSTed to B, and it still works."""
    connect = pytest.importorskip("websockets.sync.client").connect
    ws_port, http_port = workers

    page = _get(f"http://127.0.0.1:{ws_port}/uploadprobe/")
    state = re.search(r'data-state="([^"]+)"', page)
    assert state is not None, "the page did not render a component with signed state"
    signed_state = state.group(1).replace("&quot;", '"').replace("&amp;", "&")

    with connect(f"ws://127.0.0.1:{ws_port}/__wireview__", open_timeout=30) as ws:
        ws.send(
            json.dumps({"command": "join", "payload": {"name": "UploadProbe", "state": signed_state, "children": {}}})
        )

        # allow_upload() runs in joined() and hands back the endpoint, which is
        # where this connection's id comes from.
        config = _next_command(ws, "upload_op", lambda p: p["op"] == "config")
        endpoint = config["endpoint"]
        assert endpoint.startswith("/__wireview_upload__/")

        ref = "upload-1"
        ws.send(
            json.dumps(
                {
                    "command": "upload_register",
                    "payload": {
                        "id": "probe",
                        "name": "files",
                        "entries": [{"ref": ref, "name": "payload.txt", "size": len(PAYLOAD), "type": "text/plain"}],
                    },
                }
            )
        )
        registered = _next_command(ws, "upload_op", lambda p: p["op"] in ("registered", "error"))
        assert registered["op"] == "registered", registered
        token = registered["token"]

        # Every chunk to the worker that has never heard of this connection.
        chunks = [PAYLOAD[i : i + CHUNK_SIZE] for i in range(0, len(PAYLOAD), CHUNK_SIZE)]
        url = f"http://127.0.0.1:{http_port}{endpoint}"
        for index, chunk in enumerate(chunks):
            result = _post_chunk(
                url,
                chunk,
                {
                    "X-Upload-Token": token,
                    "X-Chunk-Index": str(index),
                    "X-Total-Chunks": str(len(chunks)),
                    "X-Entry-Ref": ref,
                },
            )
            assert result["status"] == "ok"
        assert result["complete"] is True

        # AC2: the progress the other worker published reached this connection.
        progress = _next_command(ws, "upload_op", lambda p: p["op"] == "progress")
        assert progress["ref"] == ref

        ws.send(json.dumps({"command": "upload_complete", "payload": {"id": "probe", "name": "files", "ref": ref}}))

        digest = hashlib.sha256(PAYLOAD).hexdigest()[:16]
        render = _next_command(ws, "render", lambda p: digest in json.dumps(p))
        assert str(len(PAYLOAD)) in json.dumps(render), "the owning worker read what the other one wrote"
