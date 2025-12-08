"""Tests for scroll_into_view functionality."""

import pytest

from wireview import Component
from wireview.core.meta import WireviewMeta
from wireview.testing import mount


class TestWireviewMetaScrollIntoView:
    """Test WireviewMeta.scroll_into_view() method."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_scroll_into_view_sends_command(self):
        """scroll_into_view() should queue a scroll_into_view command."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.scroll_into_view("element-123", behavior="smooth", block="end")

        assert len(meta._pending_operations) == 1
        cmd, kwargs = meta._pending_operations[0]
        assert cmd == "scroll_into_view"
        assert kwargs == {
            "id": "element-123",
            "behavior": "smooth",
            "block": "end",
            "inline": "nearest",
        }

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_scroll_into_view_default_params(self):
        """scroll_into_view() should use correct default parameters."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.scroll_into_view("element-id")

        assert len(meta._pending_operations) == 1
        cmd, kwargs = meta._pending_operations[0]
        assert cmd == "scroll_into_view"
        assert kwargs == {
            "id": "element-id",
            "behavior": "auto",
            "block": "start",
            "inline": "nearest",
        }

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_scroll_into_view_all_params(self):
        """scroll_into_view() should pass all parameters correctly."""
        meta = WireviewMeta(params={}, channel_name="test-channel")
        meta.enter_pending_mode()

        await meta.scroll_into_view(
            "element-id",
            behavior="instant",
            block="center",
            inline="start",
        )

        cmd, kwargs = meta._pending_operations[0]
        assert kwargs == {
            "id": "element-id",
            "behavior": "instant",
            "block": "center",
            "inline": "start",
        }


class ScrollInJoinedComponent(Component):
    """Component that calls scroll_into_view() in joined()."""

    _template_name = "streams/stream_list.html"

    async def joined(self):
        """Scroll to element during join."""
        await self.scroll_into_view("target-element", behavior="instant", block="end")


class TestComponentScrollIntoView:
    """Test Component.scroll_into_view() method."""

    @pytest.mark.unit
    def test_component_has_scroll_into_view_method(self):
        """Component should have scroll_into_view() method."""
        import asyncio

        assert hasattr(Component, "scroll_into_view")
        assert asyncio.iscoroutinefunction(Component.scroll_into_view)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_scroll_into_view_in_joined_is_queued(self):
        """scroll_into_view() called in joined() should be queued."""
        view = await mount(ScrollInJoinedComponent)

        # Check that scroll_into_view was sent
        scroll_messages = [m for m in view.sent_messages if m.get("type") == "scroll_into_view"]
        assert len(scroll_messages) == 1
        assert scroll_messages[0]["id"] == "target-element"
        assert scroll_messages[0]["behavior"] == "instant"
        assert scroll_messages[0]["block"] == "end"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_scroll_into_view_after_joined_sends_immediately(self):
        """scroll_into_view() called after joined() should not be queued."""
        view = await mount(ScrollInJoinedComponent)

        # Clear messages from initial mount
        initial_count = len(view.sent_messages)

        # Call scroll_into_view directly on the component
        await view.component.scroll_into_view("another-element", behavior="smooth")

        # Should have new scroll_into_view message
        new_messages = view.sent_messages[initial_count:]
        scroll_messages = [m for m in new_messages if m.get("type") == "scroll_into_view"]
        assert len(scroll_messages) == 1
        assert scroll_messages[0]["id"] == "another-element"
        assert scroll_messages[0]["behavior"] == "smooth"
