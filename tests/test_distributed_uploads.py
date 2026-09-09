"""Chunked uploads across processes (#83).

The chunk endpoint is HTTP, so nothing routes it to the worker that holds the
WebSocket. Before this the endpoint looked the upload up in a process-local dict
and answered ``404 Component not found`` on any other worker. Now it decides
from the signed token alone and writes to a path computed from it, and the
owning worker learns what happened over the broker.

The tests here are the two halves of that: what the endpoint does without any
local state, and what the owner does with the messages it gets back. The
cross-interpreter test is the one that would have caught the original bug -- a
fresh process, sharing nothing with this one but the settings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import typing as t
from pathlib import Path

import pytest
from django.test import AsyncRequestFactory

from wireview import settings as wireview_settings
from wireview.features import upload_store
from wireview.features.uploads import (
    UploadConfig,
    UploadEntry,
    UploadRegistry,
    UploadStatus,
    sign_upload_token,
    upload_group_name,
    validate_upload_token,
)
from wireview.views import UploadView

ROOT = Path(__file__).resolve().parent.parent

JPEG_HEADER = b"\xff\xd8\xff\xe0"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Point the chunk store at a directory this test owns."""
    monkeypatch.setattr(wireview_settings, "UPLOAD_TEMP_DIR", str(tmp_path))
    upload_store.reset_sweep_clock()
    return tmp_path / upload_store.STORE_DIR_NAME


def make_entry(
    connection_id: str = "conn-1",
    component_id: str = "comp-1",
    size: int = 8,
) -> tuple[UploadRegistry, UploadEntry, str]:
    """One registered entry, as the worker holding the WebSocket would make it."""
    registry = UploadRegistry(component_id, connection_id=connection_id)
    registry.allow_upload(UploadConfig(name="images", accept=[".jpg"]))
    entry = UploadEntry(
        ref="upload-1",
        upload_name="images",
        client_name="photo.jpg",
        client_size=size,
        client_type="image/jpeg",
    )
    token = registry.add_entry("images", entry)
    return registry, entry, token


# --- another interpreter, sharing only the settings -----------------------------------------

CHILD_SCRIPT = '''
"""Upload chunks from a process that has never seen the component."""

import asyncio, json, os, sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testproj.settings")
os.environ.setdefault("WIREVIEW_TEST_LAYER", "memory")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

import django

django.setup()

from django.test import AsyncRequestFactory

from wireview import settings as wireview_settings
from wireview.views import UploadView

# The one thing the two processes must agree on besides the signing key.
wireview_settings.UPLOAD_TEMP_DIR = os.environ["WV_UPLOAD_DIR"]

job = json.loads(sys.argv[1])


async def main():
    view = UploadView()
    factory = AsyncRequestFactory()
    results = []
    chunks = [bytes.fromhex(chunk) for chunk in job["chunks"]]
    for index, chunk in enumerate(chunks):
        request = factory.post(
            job["url"], data=chunk, content_type="application/octet-stream"
        )
        request.META["HTTP_X_UPLOAD_TOKEN"] = job["token"]
        request.META["HTTP_X_CHUNK_INDEX"] = str(index)
        request.META["HTTP_X_TOTAL_CHUNKS"] = str(len(chunks))
        request.META["HTTP_X_ENTRY_REF"] = job["ref"]
        response = await view.post(
            request, job["connection_id"], job["component_id"], job["upload_name"]
        )
        results.append({"status": response.status_code, "body": json.loads(response.content)})
    return results


print(json.dumps(asyncio.run(main())))
'''


def upload_from_another_process(job: dict[str, t.Any], upload_dir: Path) -> list[dict[str, t.Any]]:
    """Run the chunk endpoint in a fresh interpreter and return its answers."""
    script = upload_dir / "_child_upload.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(CHILD_SCRIPT)

    env = {
        **os.environ,
        "WV_UPLOAD_DIR": str(upload_dir),
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
    }
    completed = subprocess.run(
        [sys.executable, str(script), json.dumps(job)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"child process failed:\n{completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.integration
def test_a_worker_that_never_saw_the_component_completes_the_upload(store, tmp_path):
    """The #83 regression, in the shape it was first reproduced.

    The registry, the entry and the token exist only here. The chunks are POSTed
    by an interpreter that shares nothing with this one except the settings --
    the signing key and the chunk directory. It used to answer 404.
    """
    _, entry, token = make_entry(size=8)
    payload = JPEG_HEADER + b"efgh"

    results = upload_from_another_process(
        {
            "url": "/__wireview_upload__/conn-1/comp-1/images/",
            "connection_id": "conn-1",
            "component_id": "comp-1",
            "upload_name": "images",
            "ref": "upload-1",
            "token": token,
            "chunks": [payload[:4].hex(), payload[4:].hex()],
        },
        tmp_path,
    )

    assert [r["status"] for r in results] == [200, 200]
    assert results[0]["body"]["complete"] is False
    assert results[1]["body"]["complete"] is True
    assert entry.temp_path is not None
    assert entry.temp_path.read_bytes() == payload, "the other process wrote where this one expects to read"


@pytest.mark.integration
def test_a_cancel_here_stops_a_write_over_there(store, tmp_path):
    """AC3: a cancel reaches a chunk being written in another process."""
    registry, entry, token = make_entry(size=8)
    registry.cancel_entry("images", "upload-1")

    results = upload_from_another_process(
        {
            "url": "/__wireview_upload__/conn-1/comp-1/images/",
            "connection_id": "conn-1",
            "component_id": "comp-1",
            "upload_name": "images",
            "ref": "upload-1",
            "token": token,
            "chunks": [JPEG_HEADER.hex()],
        },
        tmp_path,
    )

    assert [r["status"] for r in results] == [410]
    assert entry.temp_path is not None and not entry.temp_path.exists()


# --- the owner applies what the endpoint reports ---------------------------------------------


class FakeConsumer:
    """The parts of ``WireviewConsumer`` these handlers touch."""

    def __init__(self, component) -> None:
        from wireview.consumer import WireviewConsumer

        self.component = component
        self.sent: list[tuple[str, dict[str, t.Any]]] = []
        self.upload_progress = WireviewConsumer.upload_progress.__get__(self)
        self.upload_completed = WireviewConsumer.upload_completed.__get__(self)
        self.upload_error = WireviewConsumer.upload_error.__get__(self)
        self._find_upload_entry = WireviewConsumer._find_upload_entry.__get__(self)
        self._promote_completed = WireviewConsumer._promote_completed

        class Repo:
            def __init__(self, component):
                self._component = component

            def get(self, component_id):
                return self._component if component_id == self._component.id else None

        self.repo = Repo(component)

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.sent.append((command, payload))


class OwningComponent:
    """A stand-in for the component the entry belongs to."""

    def __init__(self, registry: UploadRegistry) -> None:
        self.id = registry.component_id
        self._upload_registry = registry


@pytest.mark.integration
@pytest.mark.asyncio
async def test_progress_from_another_worker_updates_the_entry(store):
    """``this.uploads`` has to be true even when no chunk touched this process."""
    registry, entry, _ = make_entry(size=100)
    consumer = FakeConsumer(OwningComponent(registry))

    await consumer.upload_progress(
        {
            "component": "comp-1",
            "upload": "images",
            "ref": "upload-1",
            "progress": 42,
            "bytes_received": 42,
        }
    )

    assert entry.status == UploadStatus.UPLOADING
    assert entry.bytes_received == 42
    assert entry.progress == 42
    assert consumer.sent[0][1]["op"] == "progress", "the browser still hears about it"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completion_from_another_worker_needs_the_bytes(store):
    """The message says the upload finished; the file is what proves it."""
    registry, entry, _ = make_entry(size=4)
    consumer = FakeConsumer(OwningComponent(registry))
    event = {"component": "comp-1", "upload": "images", "ref": "upload-1", "bytes_received": 4}

    await consumer.upload_completed(event)
    assert entry.status != UploadStatus.COMPLETED, "no file, no completion"

    assert entry.temp_path is not None
    upload_store.append_chunk(entry.temp_path, JPEG_HEADER)
    await consumer.upload_completed(event)

    assert entry.status == UploadStatus.COMPLETED
    assert entry.progress == 100
    assert consumer.sent == [], "the browser's own upload_complete command is what notifies it"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_error_from_another_worker_marks_the_entry(store):
    registry, entry, _ = make_entry()
    consumer = FakeConsumer(OwningComponent(registry))

    await consumer.upload_error(
        {
            "component": "comp-1",
            "upload": "images",
            "ref": "upload-1",
            "errors": ["File content doesn't match file type"],
        }
    )

    assert entry.status == UploadStatus.ERROR
    assert entry.errors == ["File content doesn't match file type"]
    assert consumer.sent[0][1]["op"] == "error"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_message_for_someone_elses_component_is_ignored(store):
    """One group carries a connection's whole page, so the name is checked."""
    registry, entry, _ = make_entry()
    consumer = FakeConsumer(OwningComponent(registry))

    await consumer.upload_progress({"component": "another", "upload": "images", "ref": "upload-1", "progress": 99})

    assert entry.progress == 0


@pytest.mark.unit
def test_the_endpoint_publishes_to_the_connections_group(store):
    """AC2: progress goes to the group the owning worker joined."""
    assert upload_group_name("conn-1") == "wireview_upload_conn-1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_every_message_names_the_component_it_is_about(store, monkeypatch):
    """The owner needs the id to find the entry; the URL is not enough."""
    published: list[tuple[str, dict[str, t.Any]]] = []

    class FakeBroker:
        async def publish(self, group: str, message: dict[str, t.Any]) -> None:
            published.append((group, message))

    monkeypatch.setattr("wireview.views.get_broker", lambda: FakeBroker())
    _, _, token = make_entry(size=4)

    factory = AsyncRequestFactory()
    request = factory.post(
        "/__wireview_upload__/conn-1/comp-1/images/",
        data=JPEG_HEADER,
        content_type="application/octet-stream",
    )
    request.META["HTTP_X_UPLOAD_TOKEN"] = token
    request.META["HTTP_X_CHUNK_INDEX"] = "0"
    request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
    request.META["HTTP_X_ENTRY_REF"] = "upload-1"
    await UploadView().post(request, "conn-1", "comp-1", "images")

    assert [message["type"] for _, message in published] == ["upload.progress", "upload.completed"]
    for group, message in published:
        assert group == upload_group_name("conn-1")
        assert message["component"] == "comp-1"
        assert message["upload"] == "images"
        assert message["ref"] == "upload-1"


# --- the token is the whole authority --------------------------------------------------------


@pytest.mark.unit
def test_a_token_survives_a_round_trip():
    token = sign_upload_token(
        connection_id="conn-1",
        component_id="comp:with:colons",
        config_name="images",
        ref="upload-1",
        max_bytes=1234,
        extension=".jpg",
    )

    validated = validate_upload_token(token)

    assert validated is not None
    assert validated.component_id == "comp:with:colons", "a packed string would have split this wrong"
    assert validated.max_bytes == 1234
    assert validated.extension == ".jpg"


@pytest.mark.unit
def test_an_expired_token_is_refused():
    token = sign_upload_token(
        connection_id="conn-1",
        component_id="comp-1",
        config_name="images",
        ref="upload-1",
        max_bytes=10,
    )

    assert validate_upload_token(token, max_age=-1) is None


@pytest.mark.unit
def test_the_pre_83_token_is_still_accepted():
    """A page rendered by an older worker keeps uploading during a rollout."""
    from wireview.core.signing import get_signer
    from wireview.features.uploads import UPLOAD_SALT

    legacy = get_signer(UPLOAD_SALT).sign("conn-1:comp-1:images:upload-1")

    validated = validate_upload_token(legacy)

    assert validated is not None
    assert (validated.connection_id, validated.component_id) == ("conn-1", "comp-1")
    assert validated.max_bytes == wireview_settings.UPLOAD_MAX_FILE_SIZE, "no size in the old token"
    assert validated.extension == ""


@pytest.mark.unit
def test_a_tampered_token_is_refused():
    token = sign_upload_token(
        connection_id="conn-1",
        component_id="comp-1",
        config_name="images",
        ref="upload-1",
        max_bytes=10,
    )

    assert validate_upload_token(token[:-1] + ("x" if not token.endswith("x") else "y")) is None


# --- what a dead worker leaves behind --------------------------------------------------------


@pytest.mark.unit
def test_the_sweep_collects_what_outlived_its_token(store):
    """AC3, for the case no cancel can reach: a worker that died mid-upload."""
    stale = upload_store.chunk_path("conn-1", "comp-1", "images", "old")
    fresh = upload_store.chunk_path("conn-1", "comp-1", "images", "new")
    upload_store.append_chunk(stale, b"x")
    upload_store.append_chunk(fresh, b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    removed = upload_store.sweep(max_age=3600)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


@pytest.mark.unit
def test_the_sweep_removes_a_connection_directory_it_emptied(store):
    path = upload_store.chunk_path("conn-1", "comp-1", "images", "old")
    upload_store.append_chunk(path, b"x")
    old = time.time() - 7200
    os.utime(path, (old, old))

    upload_store.sweep(max_age=3600)

    assert not path.parent.exists()


@pytest.mark.unit
def test_the_opportunistic_sweep_runs_at_most_once_per_interval(store):
    """The write path pays for this, so it must not walk the store per chunk."""
    calls: list[int] = []
    upload_store.reset_sweep_clock()

    assert upload_store.sweep_if_due(interval=3600) == 0
    calls.append(1)
    # A second call inside the interval does nothing at all, not even a listdir.
    stale = upload_store.chunk_path("conn-1", "comp-1", "images", "old")
    upload_store.append_chunk(stale, b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    assert upload_store.sweep_if_due(interval=3600) == 0
    assert stale.exists()

    upload_store.reset_sweep_clock()
    assert upload_store.sweep_if_due(interval=3600) == 1


# --- through the real URLconf and handler ----------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_chunk_survives_the_request_handler(store):
    """The endpoint is async, and Django's handler has opinions about that.

    ``ATOMIC_REQUESTS`` makes the handler refuse to run an async view -- it
    raises before the view is called, so every chunk came back 500 in a project
    that sets it. The test project does, which is how this surfaced. Calling
    ``UploadView.post`` directly, as the other tests do, walks straight past it.
    """
    from django.test import AsyncClient

    _, entry, token = make_entry(size=4)

    client = AsyncClient()
    response = await client.post(
        "/__wireview_upload__/conn-1/comp-1/images/",
        data=JPEG_HEADER,
        content_type="application/octet-stream",
        headers={
            "x-upload-token": token,
            "x-chunk-index": "0",
            "x-total-chunks": "1",
            "x-entry-ref": "upload-1",
        },
    )

    assert response.status_code == 200, response.content
    assert json.loads(response.content)["complete"] is True
    assert entry.temp_path is not None and entry.temp_path.read_bytes() == JPEG_HEADER


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_connection_id_that_is_not_a_name_never_reaches_the_view(store):
    """The URLconf holds the segment that becomes a directory to its alphabet."""
    from django.test import AsyncClient

    response = await AsyncClient().post(
        "/__wireview_upload__/..%2Fescape/comp-1/images/",
        data=b"x",
        content_type="application/octet-stream",
    )

    assert response.status_code == 404
