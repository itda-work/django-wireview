"""Tests for Streams functionality."""

import pytest

from wireview import Component
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

    _template_name = "streams/stream_list.html"

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
    async def test_get_stream_item_template(self):
        """_get_stream_item_template should derive template name correctly."""
        view = await mount(StreamComponent)
        template_name = view.component._get_stream_item_template()
        assert template_name == "streams/stream_list_item.html"
