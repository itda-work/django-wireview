"""Tests for Chat app components."""

import pytest

from wireview import mount

from .live import XChatRoom, XMessageList
from .models import Room


class TestXMessageList:
    """Tests for XMessageList component."""

    @pytest.mark.unit
    def test_stream_template_derivation(self):
        """XMessageList should derive correct stream item template."""
        # Test the class attribute directly without mounting
        # This avoids database access in joined()
        assert XMessageList._meta.template_name == "chat/message_list.html"

        # Verify template naming convention
        base = XMessageList._meta.template_name.rsplit(".", 1)[0]
        expected_item_template = f"{base}_item.html"
        assert expected_item_template == "chat/message_list_item.html"

    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)  # acreate commits on the worker thread
    async def test_a_message_reaches_the_lists_of_its_room_only(self):
        """send_message broadcasts the new message: every list of the room shows it, another room's does not."""
        room = await Room.objects.acreate(name="lobby")
        elsewhere = await Room.objects.acreate(name="elsewhere")
        lists = [
            await mount(XMessageList, id="a", room=room),
            await mount(XMessageList, id="b", room=room),
            await mount(XMessageList, id="c", room=elsewhere),
        ]
        chat = await mount(XChatRoom, room=room, username="ada")
        for view in lists:
            view.clear_messages()

        await chat.call("send_message", content="hello <b>room</b>")

        for view in lists[:2]:
            assert "hello &lt;b&gt;room&lt;/b&gt;" in view.stream_html("messages")
            assert [op["at"] for op in view.stream_ops("messages")] == [0]
        assert lists[2].stream_ops() == []


class TestXChatRoom:
    """Tests for XChatRoom component."""

    @pytest.mark.unit
    def test_no_message_subscription(self):
        """XChatRoom does not hear new messages itself: XMessageList does."""
        assert not XChatRoom._meta.subscriptions

    @pytest.mark.unit
    def test_template_name(self):
        """XChatRoom should use room_component.html template."""
        assert XChatRoom._meta.template_name == "chat/room_component.html"
