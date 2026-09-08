"""Tests for pending operations queue in WireviewMeta."""

import pytest

from wireview import Component
from wireview.core.meta import WireviewMeta
from wireview.testing import mount

# Rendering crosses channels' ``database_sync_to_async``, which calls
# ``close_old_connections()`` on the way in and out. Under pytest-django a test
# that opened a connection leaves it inside a transaction, so this hop hits the
# blocker and a test that never touches the ORM fails with "Database access not
# allowed" — but only when such a test ran earlier in the same process. Asking
# for the database here states what the render path already needs and takes the
# ordering out of it.
pytestmark = pytest.mark.django_db


class TestPendingModeBasics:
    """Test basic pending mode functionality."""

    @pytest.mark.unit
    def test_initial_state_not_pending(self):
        """WireviewMeta should not be in pending mode by default."""
        meta = WireviewMeta(params={})
        assert meta._pending_mode is False
        assert meta._pending_operations == []
        assert meta._pending_broadcasts == []

    @pytest.mark.unit
    def test_enter_pending_mode(self):
        """enter_pending_mode should set pending flag."""
        meta = WireviewMeta(params={})
        meta.enter_pending_mode()
        assert meta._pending_mode is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_flush_pending_clears_mode(self):
        """flush_pending should clear pending mode and operations."""
        meta = WireviewMeta(params={})
        meta.enter_pending_mode()
        meta._pending_operations = [("test", {"key": "value"})]

        await meta.flush_pending()

        assert meta._pending_mode is False
        assert meta._pending_operations == []


class TestPendingOperationsQueue:
    """Test operations are queued in pending mode."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_send_queues_when_pending(self):
        """send() should queue operations when in pending mode."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.send("test_command", foo="bar")

        assert len(meta._pending_operations) == 1
        assert meta._pending_operations[0] == ("test_command", {"foo": "bar"})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_operations_queued(self):
        """Multiple operations should be queued in order."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.send("cmd1", a=1)
        await meta.send("cmd2", b=2)
        await meta.send("cmd3", c=3)

        assert len(meta._pending_operations) == 3
        assert meta._pending_operations[0] == ("cmd1", {"a": 1})
        assert meta._pending_operations[1] == ("cmd2", {"b": 2})
        assert meta._pending_operations[2] == ("cmd3", {"c": 3})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_send_not_queued_when_not_pending(self):
        """send() should not queue when not in pending mode."""
        meta = WireviewMeta(params={}, channel_name=None)
        # Not in pending mode, no channel

        await meta.send("test_command", foo="bar")

        # No operations queued (also no channel to send to)
        assert len(meta._pending_operations) == 0


class MockItem:
    """Mock item for testing streams."""

    def __init__(self, pk: int, name: str):
        self.pk = pk
        self.name = name


class StreamInJoinedComponent(Component):
    """Component that calls stream() in joined()."""

    _template_name = "streams/stream_list.html"
    items: list = []

    async def joined(self):
        """Stream items during join."""
        items = [MockItem(pk=1, name="Item 1"), MockItem(pk=2, name="Item 2")]
        await self.stream("items", items)


class PushJsInJoinedComponent(Component):
    """Component that calls push_js() in joined()."""

    _template_name = "streams/stream_list.html"

    async def joined(self):
        """Push JS during join."""
        from wireview.js import JS

        await self.push_js(JS().focus("#input"))


class MultipleOpsInJoinedComponent(Component):
    """Component that calls multiple operations in joined()."""

    _template_name = "streams/stream_list.html"
    items: list = []

    async def joined(self):
        """Multiple operations during join."""
        from wireview.js import JS

        items = [MockItem(pk=1, name="Item 1")]
        await self.stream("items", items)
        await self.push_js(JS().focus("#input"))


class TestComponentJoinedPendingOperations:
    """Test that operations in joined() are properly queued."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_stream_in_joined_is_queued(self):
        """stream() called in joined() should be queued."""
        view = await mount(StreamInJoinedComponent)

        # After mount, pending operations should have been flushed
        # Check that stream_op was sent
        stream_messages = [m for m in view.sent_messages if m.get("type") == "stream_op"]
        assert len(stream_messages) == 1
        assert stream_messages[0]["op"] == "reset"
        assert stream_messages[0]["stream"] == "items"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_push_js_in_joined_is_queued(self):
        """push_js() called in joined() should be queued."""
        view = await mount(PushJsInJoinedComponent)

        # Check that exec_js was sent
        js_messages = [m for m in view.sent_messages if m.get("type") == "exec_js"]
        assert len(js_messages) == 1
        assert js_messages[0]["commands"][0]["cmd"] == "focus"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_ops_in_joined_preserve_order(self):
        """Multiple operations in joined() should preserve order."""
        view = await mount(MultipleOpsInJoinedComponent)

        # Get all operation types in order
        op_types = [m.get("type") for m in view.sent_messages]

        # stream_op should come before exec_js (in the order they were called)
        stream_idx = op_types.index("stream_op") if "stream_op" in op_types else -1
        exec_idx = op_types.index("exec_js") if "exec_js" in op_types else -1

        assert stream_idx != -1, "stream_op should be present"
        assert exec_idx != -1, "exec_js should be present"
        assert stream_idx < exec_idx, "stream_op should come before exec_js"


class NoPendingAfterJoinedComponent(Component):
    """Component that calls operations after joined() completes."""

    _template_name = "streams/stream_list.html"
    items: list = []

    async def joined(self):
        """Empty joined - no operations."""
        pass

    async def add_item(self):
        """Add item after joined() - should send immediately."""
        items = [MockItem(pk=1, name="New Item")]
        await self.stream("items", items)


class TestPendingModeAfterJoined:
    """Test that pending mode is properly exited after joined()."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_operations_after_joined_not_queued(self):
        """Operations called after joined() should send immediately."""
        view = await mount(NoPendingAfterJoinedComponent)

        # Clear messages from initial mount
        initial_count = len(view.sent_messages)

        # Call add_item after joined()
        await view.call("add_item")

        # Should have new stream_op message
        new_messages = view.sent_messages[initial_count:]
        stream_messages = [m for m in new_messages if m.get("type") == "stream_op"]
        assert len(stream_messages) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_pending_mode_false_after_mount(self):
        """Component should not be in pending mode after mount."""
        view = await mount(NoPendingAfterJoinedComponent)
        assert view.component.wire._pending_mode is False
        assert view.component.wire._pending_operations == []


class TestPendingBroadcasts:
    """Test pending broadcast functionality."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_broadcast_queued_when_pending(self):
        """queue_broadcast() should queue broadcasts when in pending mode."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.queue_broadcast("test-channel", action="joined", user="alice")

        assert len(meta._pending_broadcasts) == 1
        assert meta._pending_broadcasts[0] == ("test-channel", {"action": "joined", "user": "alice"})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_broadcasts_queued(self):
        """Multiple broadcasts should be queued in order."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.queue_broadcast("ch1", action="a")
        await meta.queue_broadcast("ch2", action="b")
        await meta.queue_broadcast("ch3", action="c")

        assert len(meta._pending_broadcasts) == 3
        assert meta._pending_broadcasts[0] == ("ch1", {"action": "a"})
        assert meta._pending_broadcasts[1] == ("ch2", {"action": "b"})
        assert meta._pending_broadcasts[2] == ("ch3", {"action": "c"})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_flush_pending_clears_broadcasts(self):
        """flush_pending() should clear pending broadcasts."""
        meta = WireviewMeta(params={})
        meta.enter_pending_mode()
        meta._pending_broadcasts = [("ch", {"action": "test"})]

        await meta.flush_pending()

        assert meta._pending_broadcasts == []


class BroadcastInJoinedComponent(Component):
    """Component that calls broadcast() in joined()."""

    _template_name = "streams/stream_list.html"

    async def joined(self):
        """Broadcast during join - should be queued."""
        await self.broadcast("test-channel", action="joined", username="testuser")


class TestComponentBroadcastMethod:
    """Test Component.broadcast() method."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_broadcast_in_joined_is_queued(self):
        """broadcast() called in joined() should be queued."""
        view = await mount(BroadcastInJoinedComponent)

        # After mount, pending broadcasts should have been flushed
        # The mock channel layer won't actually send, but we can check
        # that the component is no longer in pending mode
        assert view.component.wire._pending_mode is False
        assert view.component.wire._pending_broadcasts == []


class TestLeavingLifecycleHook:
    """Test leaving() lifecycle hook."""

    @pytest.mark.unit
    def test_component_has_leaving_method(self):
        """Component should have leaving() method."""
        assert hasattr(Component, "leaving")
        import asyncio

        assert asyncio.iscoroutinefunction(Component.leaving)


class LeavingComponent(Component):
    """Component that tracks leaving() calls."""

    _template_name = "streams/stream_list.html"
    leaving_called: bool = False

    async def leaving(self):
        """Track that leaving was called."""
        self.leaving_called = True


class TestLeavingHookExecution:
    """Test that leaving() is called properly."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_leaving_default_implementation(self):
        """Default leaving() should do nothing (not raise)."""
        view = await mount(Component, template_name="streams/stream_list.html")
        # Should not raise
        await view.component.leaving()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_custom_leaving_is_called(self):
        """Custom leaving() implementation should be callable."""
        view = await mount(LeavingComponent)
        assert view.component.leaving_called is False

        await view.component.leaving()

        assert view.component.leaving_called is True
