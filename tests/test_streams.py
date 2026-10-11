"""Tests for Streams functionality."""

import inspect

import pytest

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import INSERTS_IN_ORDER_SINCE, PROTOCOL_VERSION, UPDATE_ONLY_SINCE
from wireview.features.streams import StreamItem, StreamOp
from wireview.testing import mount


class TestStreamItem:
    """Test the StreamItem dataclass."""

    @pytest.mark.unit
    def test_stream_item_creation(self):
        """StreamItem should store dom_id and html."""
        item = StreamItem(dom_id="items-1", html="<li>Test</li>")
        assert item.dom_id == "items-1"
        assert item.html == "<li>Test</li>"


class TestStreamOp:
    """Test the StreamOp dataclass."""

    @pytest.mark.unit
    def test_reset_op_to_payload(self):
        """Reset operation should serialize correctly."""
        items = [
            StreamItem(dom_id="items-1", html="<li>Item 1</li>"),
            StreamItem(dom_id="items-2", html="<li>Item 2</li>"),
        ]
        op = StreamOp(op="reset", stream="items", items=items)
        payload = op.to_payload()

        assert payload["op"] == "reset"
        assert payload["stream"] == "items"
        assert payload["at"] == -1
        assert len(payload["items"]) == 2
        assert payload["items"][0] == {"id": "items-1", "html": "<li>Item 1</li>"}
        assert payload["items"][1] == {"id": "items-2", "html": "<li>Item 2</li>"}

    @pytest.mark.unit
    def test_insert_op_to_payload(self):
        """Insert operation should serialize correctly with position."""
        item = StreamItem(dom_id="items-3", html="<li>Item 3</li>")
        op = StreamOp(op="insert", stream="items", items=[item], at=0)
        payload = op.to_payload()

        assert payload["op"] == "insert"
        assert payload["stream"] == "items"
        assert payload["at"] == 0
        assert len(payload["items"]) == 1
        assert payload["items"][0] == {"id": "items-3", "html": "<li>Item 3</li>"}

    @pytest.mark.unit
    def test_delete_op_to_payload(self):
        """Delete operation should serialize correctly."""
        item = StreamItem(dom_id="items-1", html="")
        op = StreamOp(op="delete", stream="items", items=[item])
        payload = op.to_payload()

        assert payload["op"] == "delete"
        assert payload["stream"] == "items"
        assert payload["items"][0] == {"id": "items-1", "html": ""}

    @pytest.mark.unit
    def test_default_at_is_append(self):
        """Default at value should be -1 (append)."""
        op = StreamOp(op="insert", stream="items")
        assert op.at == -1


class MockItem:
    """Mock item for testing streams."""

    def __init__(self, pk: int, name: str):
        self.pk = pk
        self.name = name


class StreamComponent(Component):
    """Test component with stream methods."""

    class Meta:
        template_name = "streams/stream_list.html"

    async def add_item(self, name: str):
        """Add an item to the stream."""
        item = MockItem(pk=1, name=name)
        await self.stream_insert("items", item, at=0)

    async def remove_item(self, item_id: int):
        """Remove an item from the stream."""
        await self.stream_delete("items", item_id)

    async def reset_items(self):
        """Reset items in the stream."""
        items = [MockItem(pk=1, name="Item 1"), MockItem(pk=2, name="Item 2")]
        await self.stream("items", items)

    async def add_many(self, at: int = -1, count: int = 3):
        """Insert ``count`` items without a reset, in one operation."""
        await self.stream("items", [MockItem(pk=n, name=f"Item {n}") for n in range(1, count + 1)], at=at, reset=False)


class TestComponentStreamMethods:
    """Test Component stream methods."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_stream_delete_with_int_id(self):
        """stream_delete should convert int to proper DOM ID."""
        view = await mount(StreamComponent)
        view.clear_messages()

        await view.call("remove_item", item_id=123)

        # Check that stream_op message was sent
        stream_messages = [m for m in view.sent_messages if m.get("type") == "stream_op"]
        assert len(stream_messages) == 1

        msg = stream_messages[0]
        assert msg["op"] == "delete"
        assert msg["stream"] == "items"
        assert msg["items"][0]["id"] == "items-123"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_every_stream_op_names_the_component_that_sent_it(self):
        """The page looks for the container inside this element, not the first of its name.

        Two components with a stream named alike wrote into one list: the page
        searched the whole document for ``wire-stream="items"``.
        """
        view = await mount(StreamComponent, id="feed-a")
        await view.call("remove_item", item_id=1)

        [op] = view.stream_ops("items")
        assert op["id"] == "feed-a"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_get_stream_item_template(self):
        """_get_stream_item_template should derive template name correctly."""
        view = await mount(StreamComponent)
        template_name = view.component._get_stream_item_template()
        assert template_name == "streams/stream_list_item.html"


@pytest.mark.django_db  # an item renders through db(), which closes old connections
class TestStreamWithoutReset:
    """``stream(..., reset=False)``: several items at ``at``, one operation (#199)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize("at", [-1, 0, 2])
    async def test_the_items_go_out_in_one_insert_in_the_order_given(self, at):
        view = await mount(StreamComponent, id="feed")
        view.clear_messages()

        await view.call("add_many", at=at)

        [op] = view.stream_ops("items")
        assert op["op"] == "insert"
        assert op["at"] == at
        assert [item["id"] for item in op["items"]] == ["items-1", "items-2", "items-3"]
        assert op["id"] == "feed"
        assert "update_only" not in op

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_items_send_nothing(self):
        view = await mount(StreamComponent)
        view.clear_messages()

        await view.call("add_many", count=0)

        assert view.stream_ops() == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_reset_is_still_the_default(self):
        view = await mount(StreamComponent)
        view.clear_messages()

        await view.call("reset_items")

        [op] = view.stream_ops("items")
        assert op["op"] == "reset"


class QuerySetStream(Component):
    class Meta:
        template_name = "streams/stream_list.html"

    async def joined(self):
        from django.contrib.auth.models import User

        await self.stream("items", User.objects.order_by("username"))


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.django_db(transaction=True)  # the async ORM writes on another thread's connection
async def test_a_queryset_streams_without_running_the_orm_on_the_event_loop():
    """A QuerySet is consumed with ``async for`` (#120).

    Iterated synchronously it queries on the event loop, and every join of a
    page that streamed one failed on a real server while the suite, run with
    DJANGO_ALLOW_ASYNC_UNSAFE, stayed green.
    """
    from django.contrib.auth.models import User

    await User.objects.acreate(username="b")
    await User.objects.acreate(username="a")

    view = await mount(QuerySetStream)

    [op] = [m for m in view.sent_messages if m.get("type") == "stream_op"]
    users = [u async for u in User.objects.order_by("username")]
    assert [item["id"] for item in op["items"]] == [f"items-{u.pk}" for u in users]


def _started(consumer: WireviewConsumer, vsn: int = PROTOCOL_VERSION) -> None:
    """What the session's start leaves that these mails read: a repository, with the
    protocol version the socket URL named. Started for real, it would need a channel layer."""
    from wireview.repository import ComponentRepository

    consumer.repo = ComponentRepository(is_live=True, vsn=vsn)


class TestConsumerAcceptsEveryStreamPayload:
    """The consumer is a hop the component tests never cross.

    ``mount()`` stops at ``WireviewMeta.send_stream_op``. On a real connection the
    payload travels through the channel layer and is unpacked as keyword arguments
    into ``WireviewConsumer.component_stream_op``, so a key the method does not
    accept raises ``TypeError`` there and takes the whole WebSocket down — with a
    passing unit-test suite. ``limit`` did exactly that.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "op",
        [
            StreamOp(op="reset", stream="items", items=[]),
            StreamOp(op="reset", stream="items", items=[], limit=50),
            StreamOp(op="insert", stream="items", items=[StreamItem(dom_id="items-1", html="<li></li>")], at=0),
            StreamOp(op="insert", stream="items", items=[], at=0, limit=10),
            StreamOp(op="insert", stream="items", items=[], update_only=True),
            StreamOp(op="delete", stream="items", items=[StreamItem(dom_id="items-1", html="")]),
        ],
        ids=["reset", "reset-limit", "insert", "insert-limit", "insert-update-only", "delete"],
    )
    def test_the_consumer_signature_accepts_what_the_component_sends(self, op: StreamOp):
        signature = inspect.signature(WireviewConsumer.component_stream_op)
        # WireviewMeta.send_stream_op adds the owner's id
        unexpected = {*op.to_payload(), "id"} - set(signature.parameters)
        assert unexpected == set(), f"component_stream_op() would raise TypeError for {sorted(unexpected)}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_limit_survives_the_consumer_hop(self):
        consumer = WireviewConsumer()
        _started(consumer)
        sent: list[tuple[str, dict]] = []

        async def fake_send_command(command, payload):
            sent.append((command, payload))

        consumer.send_command = fake_send_command  # type: ignore[method-assign]
        await consumer.component_stream_op(**StreamOp(op="reset", stream="items", items=[], limit=50).to_payload())

        assert sent == [("stream_op", {"op": "reset", "stream": "items", "items": [], "at": -1, "limit": 50})]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_the_owner_survives_the_consumer_hop(self):
        consumer = WireviewConsumer()
        _started(consumer)
        sent: list[tuple[str, dict]] = []

        async def fake_send_command(command, payload):
            sent.append((command, payload))

        consumer.send_command = fake_send_command  # type: ignore[method-assign]
        await consumer.component_stream_op(**StreamOp(op="reset", stream="items", items=[]).to_payload(), id="feed-a")

        assert sent == [("stream_op", {"op": "reset", "stream": "items", "items": [], "at": -1, "id": "feed-a"})]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_only_survives_the_consumer_hop(self):
        """Dropped on the way, the page would insert an edit of an item it never loaded."""
        consumer = WireviewConsumer()
        _started(consumer)
        sent: list[tuple[str, dict]] = []

        async def fake_send_command(command, payload):
            sent.append((command, payload))

        consumer.send_command = fake_send_command  # type: ignore[method-assign]
        op = StreamOp(op="insert", stream="items", items=[], at=0, update_only=True)
        await consumer.component_stream_op(**op.to_payload(), id="feed-a")

        payload = {"op": "insert", "stream": "items", "items": [], "at": 0, "update_only": True, "id": "feed-a"}
        assert sent == [("stream_op", payload)]


class TestWhatAnOlderClientIsSent:
    """The session writes an older client only what it reads as meant (#199, wire-protocol.md §7)."""

    @staticmethod
    async def _sent(vsn: int, op: StreamOp) -> list[tuple[str, dict]]:
        consumer = WireviewConsumer()
        _started(consumer, vsn)
        sent: list[tuple[str, dict]] = []

        async def fake_send_command(command, payload):
            sent.append((command, payload))

        consumer.send_command = fake_send_command  # type: ignore[method-assign]
        await consumer.component_stream_op(**op.to_payload(), id="feed-a")
        return sent

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_an_update_only_insert_is_not_sent_to_a_client_that_would_insert_it(self):
        op = StreamOp(op="insert", stream="items", items=[StreamItem("items-1", "<li></li>")], update_only=True)

        assert await self._sent(UPDATE_ONLY_SINCE - 1, op) == []
        [(command, payload)] = await self._sent(UPDATE_ONLY_SINCE, op)
        assert payload["update_only"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("at", [0, 2])
    async def test_an_older_client_is_sent_a_block_in_reverse_so_that_it_lands_in_order(self, at):
        """It inserts each item at ``at`` in turn: given c, b, a it shows a, b, c."""
        items = [StreamItem(f"items-{n}", "<li></li>") for n in (1, 2, 3)]
        op = StreamOp(op="insert", stream="items", items=items, at=at)

        [(_, old)] = await self._sent(INSERTS_IN_ORDER_SINCE - 1, op)
        [(_, new)] = await self._sent(INSERTS_IN_ORDER_SINCE, op)

        assert [item["id"] for item in old["items"]] == ["items-3", "items-2", "items-1"]
        assert [item["id"] for item in new["items"]] == ["items-1", "items-2", "items-3"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_an_appended_block_is_the_same_for_every_client(self):
        items = [StreamItem(f"items-{n}", "<li></li>") for n in (1, 2, 3)]
        op = StreamOp(op="insert", stream="items", items=items, at=-1)

        assert await self._sent(0, op) == await self._sent(PROTOCOL_VERSION, op)
