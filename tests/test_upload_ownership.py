"""Upload artefacts belong to a connection, not to a component id (#77, #83).

Component ids are unique within a page, not across connections, and templates
commonly fix them (``{% component 'X' id="bookmarks" %}``). Everything an upload
needs on the server -- the signed token, the HTTP endpoint, the directory its
chunks land in and the progress group -- is therefore scoped by the connection
that owns it.

Since #83 there is no index to scope: the endpoint holds no per-upload state and
finds an upload by computing where it lives. What is asserted here is that the
computation, the token and the cleanup all agree on who the owner is.
"""

import asyncio
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import AsyncRequestFactory, override_settings
from django.urls import reverse

from wireview import Component, LiveComponent
from wireview import settings as wireview_settings
from wireview.consumer import WireviewConsumer
from wireview.core.component import WireviewMeta
from wireview.features import upload_store
from wireview.features.uploads import UploadEntry, UploadStatus, upload_group_name
from wireview.views import UploadView

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "own/uploader.html": "{% load wireview %}<div {% tag_header %}>{{ this.title }}</div>",
    "own/board.html": (
        "{% load wireview %}<div {% tag_header %}>{% live_component 'OwnChildUploader' id='child-1' %}</div>"
    ),
    "own/child.html": "{% load wireview %}<span {% live_tag_header %}>{{ this.label }}</span>",
}


class OwnUploader(Component):
    _template_name = "own/uploader.html"
    title: str = "uploader"

    async def joined(self):
        self.allow_upload("images", accept=[".jpg"])


class OwnChildUploader(LiveComponent):
    _template_name = "own/child.html"
    label: str = "child"

    async def joined(self):
        self.allow_upload("images", accept=[".jpg"])


class OwnBoard(Component):
    _template_name = "own/board.html"


class OwnPlain(Component):
    """No uploads, so no registry and no progress group."""

    _template_name = "own/uploader.html"
    title: str = "plain"


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """Point the chunk store at a directory this test owns."""
    monkeypatch.setattr(wireview_settings, "UPLOAD_TEMP_DIR", str(tmp_path))
    upload_store.reset_sweep_clock()
    return tmp_path / upload_store.STORE_DIR_NAME


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)


async def _accept(message: dict[str, t.Any]) -> None:
    """Stand-in for the ASGI send callable, so connect() can accept."""


async def connect_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    """Run the real ``connect()`` without a WebSocket, so it mints its own id."""
    consumer = WireviewConsumer()
    consumer.scope = {"user": AnonymousUser(), "session": {}}
    consumer.channel_name = "test-channel"
    consumer.channel_layer = None
    consumer.base_send = _accept  # type: ignore[assignment]
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    await consumer.connect()
    return consumer, outbound


async def join_uploader(consumer: WireviewConsumer, component_id: str) -> Component:
    """Join a component with uploads, the way ``command_join`` does it."""
    component = await consumer.repo.join("OwnUploader", {"id": component_id})
    await consumer._subscribe_upload_group(component)
    await consumer.send_render(component)
    return component


def registry_of(consumer: WireviewConsumer, component_id: str):
    """The upload registry of a component this connection holds."""
    component = consumer.repo.get(component_id)
    return getattr(component, "_upload_registry", None)


def add_entry(registry, ref: str = "upload-1") -> str:
    """Register one pending entry and return its signed token."""
    return registry.add_entry(
        "images",
        UploadEntry(
            ref=ref,
            upload_name="images",
            client_name="photo.jpg",
            client_size=100,
            client_type="image/jpeg",
        ),
    )


async def post_chunk(
    view: UploadView,
    connection_id: str,
    component_id: str,
    token: str,
    ref: str = "upload-1",
    data: bytes = b"x" * 8,
    index: int = 0,
    total: int = 2,
):
    """POST one chunk of an upload entry. Not the last chunk by default."""
    factory = AsyncRequestFactory()
    request = factory.post(
        f"/__wireview_upload__/{connection_id}/{component_id}/images/",
        data=data,
        content_type="application/octet-stream",
    )
    request.META["HTTP_X_UPLOAD_TOKEN"] = token
    request.META["HTTP_X_CHUNK_INDEX"] = str(index)
    request.META["HTTP_X_TOTAL_CHUNKS"] = str(total)
    request.META["HTTP_X_ENTRY_REF"] = ref
    return await view.post(request, connection_id, component_id, "images")


# --- the URL and the endpoint the server hands the client ---------------------------------


@pytest.mark.unit
async def test_the_upload_route_takes_three_segments():
    with override_settings(ROOT_URLCONF="wireview.urls"):
        url = reverse(
            "wireview_upload",
            kwargs={"connection_id": "conn-a", "component_id": "comp-1", "upload_name": "images"},
        )
    assert url == "/__wireview_upload__/conn-a/comp-1/images/"


async def test_allow_upload_sends_an_endpoint_that_carries_the_owner(monkeypatch):
    sent: list[tuple[str, dict[str, t.Any]]] = []

    async def record(self, _command: str, **kwargs: t.Any) -> None:
        sent.append((_command, kwargs))

    monkeypatch.setattr(WireviewMeta, "send", record)

    component = Component._build("OwnUploader", {"id": "comp-1"}, params={}, connection_id="conn-a")
    await component.joined()
    await asyncio.sleep(0.05)  # allow_upload sends its config from a task

    configs = [payload for command, payload in sent if command == "upload_op" and payload["op"] == "config"]
    assert len(configs) == 1
    assert configs[0]["endpoint"] == "/__wireview_upload__/conn-a/comp-1/images/"


async def test_an_unowned_component_falls_back_to_a_dash(monkeypatch):
    """``mount()`` and other connectionless renders have no owner to name."""
    sent: list[tuple[str, dict[str, t.Any]]] = []

    async def record(self, _command: str, **kwargs: t.Any) -> None:
        sent.append((_command, kwargs))

    monkeypatch.setattr(WireviewMeta, "send", record)

    component = Component._build("OwnUploader", {"id": "comp-1"}, params={})
    await component.joined()
    await asyncio.sleep(0.05)

    configs = [payload for command, payload in sent if command == "upload_op" and payload["op"] == "config"]
    assert configs[0]["endpoint"] == "/__wireview_upload__/-/comp-1/images/"


# --- two connections, one component id ----------------------------------------------------


async def test_two_connections_on_the_same_component_id_keep_their_own_files(_store):
    consumer_a, _ = await connect_consumer()
    consumer_b, _ = await connect_consumer()
    assert consumer_a.connection_id != consumer_b.connection_id

    await join_uploader(consumer_a, "bookmarks")
    await join_uploader(consumer_b, "bookmarks")

    registry_a = registry_of(consumer_a, "bookmarks")
    registry_b = registry_of(consumer_b, "bookmarks")
    assert registry_a is not None and registry_b is not None
    assert registry_a is not registry_b

    add_entry(registry_a)
    add_entry(registry_b)
    path_a = registry_a.get_entry("images", "upload-1").temp_path
    path_b = registry_b.get_entry("images", "upload-1").temp_path
    assert path_a != path_b
    assert path_a.parent == _store / consumer_a.connection_id
    assert path_b.parent == _store / consumer_b.connection_id


async def test_a_leave_on_one_connection_leaves_the_others_file_alone():
    consumer_a, _ = await connect_consumer()
    consumer_b, _ = await connect_consumer()
    await join_uploader(consumer_a, "bookmarks")
    await join_uploader(consumer_b, "bookmarks")

    registry_a = registry_of(consumer_a, "bookmarks")
    registry_b = registry_of(consumer_b, "bookmarks")
    assert registry_a is not None and registry_b is not None

    token_a = add_entry(registry_a)
    token_b = add_entry(registry_b)
    view = UploadView()
    await post_chunk(view, consumer_a.connection_id, "bookmarks", token_a)
    await post_chunk(view, consumer_b.connection_id, "bookmarks", token_b)
    file_a = registry_a.get_entry("images", "upload-1").temp_path
    file_b = registry_b.get_entry("images", "upload-1").temp_path
    assert file_a is not None and file_a.exists()
    assert file_b is not None and file_b.exists()

    await consumer_a.command_leave("bookmarks")

    assert not file_a.exists()
    assert registry_of(consumer_b, "bookmarks") is registry_b
    assert file_b.exists()

    # A chunk still in flight for the component that left has nowhere to go,
    # even on a worker that never heard about the leave.
    refused = await post_chunk(view, consumer_a.connection_id, "bookmarks", token_a)
    assert refused.status_code == 410
    assert not file_a.exists()

    await consumer_b.disconnect(1000)


async def test_disconnect_releases_only_that_connections_files():
    consumer_a, _ = await connect_consumer()
    consumer_b, _ = await connect_consumer()
    await join_uploader(consumer_a, "bookmarks")
    await join_uploader(consumer_a, "gallery")
    await join_uploader(consumer_b, "bookmarks")

    registry_a = registry_of(consumer_a, "bookmarks")
    registry_gallery = registry_of(consumer_a, "gallery")
    registry_b = registry_of(consumer_b, "bookmarks")
    assert registry_a is not None and registry_gallery is not None and registry_b is not None
    view = UploadView()
    token_a = add_entry(registry_a)
    await post_chunk(view, consumer_a.connection_id, "bookmarks", token_a)
    await post_chunk(view, consumer_a.connection_id, "gallery", add_entry(registry_gallery))
    await post_chunk(view, consumer_b.connection_id, "bookmarks", add_entry(registry_b))
    file_a = registry_a.get_entry("images", "upload-1").temp_path
    file_gallery = registry_gallery.get_entry("images", "upload-1").temp_path
    file_b = registry_b.get_entry("images", "upload-1").temp_path
    assert file_a is not None and file_gallery is not None and file_b is not None

    await consumer_a.disconnect(1000)

    assert not file_a.exists()
    assert not file_gallery.exists()
    assert file_b.exists()

    # And a chunk that was in flight when the connection went finds it gone.
    refused = await post_chunk(view, consumer_a.connection_id, "bookmarks", token_a)
    assert refused.status_code == 410
    assert not file_a.exists()

    await consumer_b.disconnect(1000)


# --- tokens are bound to their owner ------------------------------------------------------


async def test_a_token_issued_on_one_connection_is_refused_on_the_others_url():
    consumer_a, _ = await connect_consumer()
    consumer_b, _ = await connect_consumer()
    await join_uploader(consumer_a, "bookmarks")
    await join_uploader(consumer_b, "bookmarks")

    registry_a = registry_of(consumer_a, "bookmarks")
    registry_b = registry_of(consumer_b, "bookmarks")
    assert registry_a is not None and registry_b is not None
    token_a = add_entry(registry_a)
    add_entry(registry_b)

    view = UploadView()
    refused = await post_chunk(view, consumer_b.connection_id, "bookmarks", token_a)
    assert refused.status_code == 403, "the signature verifies, but the owner in it does not match the URL"
    assert not registry_b.get_entry("images", "upload-1").temp_path.exists()

    accepted = await post_chunk(view, consumer_a.connection_id, "bookmarks", token_a)
    assert accepted.status_code == 200

    await consumer_a.disconnect(1000)
    await consumer_b.disconnect(1000)


# --- nested components --------------------------------------------------------------------


async def test_a_live_component_child_with_uploads_is_ready_after_the_parents_render():
    consumer, _ = await connect_consumer()

    board = await consumer.repo.join("OwnBoard", {"id": "board"})
    await consumer._subscribe_upload_group(board)
    await consumer.send_render(board)

    registry = registry_of(consumer, "child-1")
    assert registry is not None, "a LiveComponent's registry is created in joined(), during the parent's render"
    assert "images" in registry.configs

    view = UploadView()
    await post_chunk(view, consumer.connection_id, "child-1", add_entry(registry))
    child_file = registry.get_entry("images", "upload-1").temp_path
    assert child_file is not None and child_file.exists()

    await consumer.disconnect(1000)
    assert not child_file.exists()


# --- a chunk racing a cancel ---------------------------------------------------------------


async def test_a_chunk_that_finishes_after_a_cancel_leaves_no_temp_file():
    consumer, _ = await connect_consumer()
    component = await join_uploader(consumer, "bookmarks")
    registry = registry_of(consumer, "bookmarks")
    assert registry is not None
    token = add_entry(registry)
    entry = registry.get_entry("images", "upload-1")
    assert entry is not None

    view = UploadView()
    write_chunk = UploadView._write_chunk

    async def cancel_mid_write(path, data):
        written = await write_chunk(path, data)
        # What a client's cancel does while the chunk is still being written.
        await component.cancel_upload("images", "upload-1")
        return written

    view._write_chunk = cancel_mid_write  # type: ignore[method-assign]
    response = await post_chunk(view, consumer.connection_id, "bookmarks", token)

    assert response.status_code == 410
    assert entry.status == UploadStatus.CANCELLED
    assert entry.temp_path is not None and not entry.temp_path.exists()

    await consumer.disconnect(1000)


# --- the progress group -------------------------------------------------------------------


async def test_the_progress_group_is_per_connection_and_subscribed_once():
    consumer, outbound = await connect_consumer()
    group = upload_group_name(consumer.connection_id)

    assert group not in outbound.subscribed, "nothing joins the group until a component needs it"

    await join_uploader(consumer, "bookmarks")
    await join_uploader(consumer, "gallery")
    assert outbound.subscribed.count(group) == 1, "components do not each join a group of their own"
    assert not any(topic.startswith("wireview_upload_bookmarks") for topic in outbound.subscribed)

    await consumer.command_leave("bookmarks")
    assert outbound.unsubscribed.count(group) == 0, "one component leaving does not drop the connection's group"

    await consumer.disconnect(1000)
    assert outbound.unsubscribed.count(group) == 1


async def test_a_connection_without_uploads_never_joins_a_progress_group():
    """A page with no uploads must not cost a group_add on the channel layer."""
    consumer, outbound = await connect_consumer()

    board = await consumer.repo.join("OwnPlain", {"id": "plain"})
    await consumer._subscribe_upload_group(board)
    await consumer.send_render(board)

    assert not any(topic.startswith("wireview_upload_") for topic in outbound.subscribed)

    await consumer.disconnect(1000)
    assert not any(topic.startswith("wireview_upload_") for topic in outbound.unsubscribed)
