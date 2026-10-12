"""Tests for Chat app components."""

import threading

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, open_live, wait_live
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
        chat = await mount(XChatRoom, id="room", room=room, username="ada")
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


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_empty_rooms_note_is_outside_the_list_a_reset_empties():
    """In the list, the first render showed it in a room with messages and the join's reset took it from an empty one.

    base.html shows it while the list holds nothing (the browser half: the E2E).
    """
    from .models import Message

    room = await Room.objects.acreate(name="lobby")
    await Message.objects.acreate(room=room, username="ada", content="hello")
    view = await mount(XMessageList, room=room)

    html = view.render() or ""

    start = html.index('wire-stream="messages"')
    assert "No messages yet" not in html[start : html.index("</ul>", start)]
    assert "No messages yet" in html[html.index("</ul>", start) :]
    assert len(view.stream_items("messages")) == 1


# In a browser


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


def _in_room(page, url: str) -> None:
    open_live(page, url, '[data-name=XChatRoom][data-is-live="true"]')
    wait_live(page, '[data-name=XMessageList][data-is-live="true"]')


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_an_empty_room_says_so_until_its_first_message_and_a_room_with_messages_never_does(browser, server):
    """The note is outside the list: the join's reset no longer takes it from an empty room,
    and a room with messages does not show it before the join."""
    from .models import Message

    empty = Room.objects.create(name="empty")
    busy = Room.objects.create(name="busy")
    Message.objects.create(room=busy, username="ada", content="hello")

    page = browser.new_page()
    _in_room(page, f"{server}/chat/room/{busy.id}/")
    expect_count(page.locator("[wire-stream=messages] li"), 1)
    expect(page.locator(".empty-message")).to_be_hidden()

    _in_room(page, f"{server}/chat/room/{empty.id}/")
    expect(page.locator(".empty-message")).to_be_visible()
    page.locator("input[name=content]").fill("first")
    page.locator("input[name=content]").press("Enter")
    expect_count(page.locator("[wire-stream=messages] li"), 1)
    expect(page.locator(".empty-message")).to_be_hidden()


@pytest.mark.e2e
class TestOnlineUsers:
    """A user with two tabs open stays on the others' lists until both are closed (#202).

    The room page's XChatRoom announces the tab and its XOnlineUsers lists who is
    here: two components of one connection, speaking for one tab.
    """

    @pytest.fixture
    def room(self, transactional_db):
        return Room.objects.create(name="lobby")

    def test_closing_one_of_two_tabs_keeps_the_user_listed(self, page, server, room, monkeypatch):
        # Closing a tab shows nothing on bob's page when the fix holds; the server
        # says when the tab's leave went out, so carol comes after it
        left = threading.Event()
        leaving = XChatRoom.leaving

        async def leaving_and_tell(self):
            await leaving(self)
            left.set()

        monkeypatch.setattr(XChatRoom, "leaving", leaving_and_tell)

        def open_tab(username):
            tab = page if username == "bob" else page.context.new_page()
            open_live(
                tab,
                f"{server}/chat/room/{room.id}/?username={username}",
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
        assert left.wait(timeout=10), "the closed tab never left"
        # bob's typing renders his list after that leave: his connection hears it first.
        # Not another arrival: ann's remaining tab answers its sync, and every list
        # on the topic hears that answer, so it would put back what the leave took.
        bob.locator("input[name=content]").press_sequentially("hi")
        expect(bob.locator(".online-users li", has_text="typing...")).to_have_count(1)
        expect(listed(bob)).to_have_text(["bob", "ann"])

        carol = open_tab("carol")
        expect_text(heading, "Online Users (3)")
        expect(listed(bob)).to_have_text(["bob", "ann", "carol"])
        # A tab that came after hears ann from her remaining tab
        expect(listed(carol)).to_contain_text(["ann"])

        ann_tabs[1].close()
        expect_text(heading, "Online Users (2)")
        expect(listed(bob)).to_have_text(["bob", "carol"])
