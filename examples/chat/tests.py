"""Tests for Chat app components."""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

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


@pytest.fixture
def wireview_server():
    with serve() as base_url:
        yield base_url


@pytest.mark.e2e
class TestOnlineUsers:
    """A user with two tabs open stays on the others' lists until both are closed (#202).

    The room page's XChatRoom announces the tab and its XOnlineUsers lists who is
    here: two components of one connection, speaking for one tab.
    """

    @pytest.fixture
    def room(self, transactional_db):
        return Room.objects.create(name="lobby")

    def test_closing_one_of_two_tabs_keeps_the_user_listed(self, page, wireview_server, room):
        def open_tab(username):
            tab = page if username == "bob" else page.context.new_page()
            open_live(
                tab,
                f"{wireview_server}/chat/room/{room.id}/?username={username}",
                "[data-name=XOnlineUsers][data-is-live=true]",
            )
            return tab

        def listed(tab):
            return tab.locator(".online-users li span:nth-child(2)")

        bob = open_tab("bob")
        ann_tabs = [open_tab("ann"), open_tab("ann")]
        heading = bob.locator(".online-users h3")
        expect_text(heading, "Online Users (2)")
        expect(listed(bob)).to_have_text(["bob", "ann"])

        ann_tabs[0].close()
        # carol's arrival renders bob's list after the closed tab's leave
        carol = open_tab("carol")
        expect_text(heading, "Online Users (3)")
        expect(listed(bob)).to_have_text(["bob", "ann", "carol"])
        # A tab that came after hears ann from her remaining tab
        expect(listed(carol)).to_contain_text(["ann"])

        ann_tabs[1].close()
        expect_text(heading, "Online Users (2)")
        expect(listed(bob)).to_have_text(["bob", "carol"])
