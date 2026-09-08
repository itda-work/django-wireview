"""Tests for Chat app components."""

import pytest

from .live import XChatRoom, XMessageList


class TestXMessageList:
    """Tests for XMessageList component."""

    @pytest.mark.unit
    def test_stream_template_derivation(self):
        """XMessageList should derive correct stream item template."""
        # Test the class attribute directly without mounting
        # This avoids database access in joined()
        assert XMessageList._template_name == "chat/message_list.html"

        # Verify template naming convention
        base = XMessageList._template_name.rsplit(".", 1)[0]
        expected_item_template = f"{base}_item.html"
        assert expected_item_template == "chat/message_list_item.html"

    @pytest.mark.unit
    def test_has_message_subscription(self):
        """XMessageList should subscribe to message model mutations."""
        assert "chat.message" in XMessageList._subscriptions


class TestXChatRoom:
    """Tests for XChatRoom component."""

    @pytest.mark.unit
    def test_no_message_subscription(self):
        """XChatRoom should NOT subscribe to message mutations (delegated to XMessageList)."""
        # XChatRoom should not have _subscriptions or it should not include "chat.message"
        subscriptions = getattr(XChatRoom, "_subscriptions", set())
        assert "chat.message" not in subscriptions

    @pytest.mark.unit
    def test_template_name(self):
        """XChatRoom should use room_component.html template."""
        assert XChatRoom._template_name == "chat/room_component.html"
