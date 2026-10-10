"""Tests for Chat app components."""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, open_live, wait_live
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
