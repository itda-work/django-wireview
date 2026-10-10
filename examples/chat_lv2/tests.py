"""What this example is for: the server decides who is speaking, and who is in the room.

A message's author is the connection's user, whatever the browser sends. The
room page and every component on it are for the room's members: the view
refuses anyone else before it renders, and the boundary's hook refuses them on
every join -- a socket that never went through the view included.
"""

import json
from html.parser import HTMLParser

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import AsyncClient, Client
from django.urls import reverse
from testproj.asgi import application
from testproj.e2e_browser import expect_count, expect_text, open_live, wait_live
from testproj.e2e_server import serve

from wireview import mount

from .live import Lv2ChatRoom, Lv2MessageList, Lv2OnlineUsers, membership_topic
from .models import Membership, Message, Room

# Handlers and the socket write from other threads, on connections of their own,
# so each test commits and the tables are flushed after it (#133).
pytestmark = pytest.mark.django_db(transaction=True)

BOUNDARY = "chat_lv2"


@pytest.fixture
def alice():
    return get_user_model().objects.create(username="alice")


@pytest.fixture
def bob():
    return get_user_model().objects.create(username="bob")


@pytest.fixture
def room(alice):
    room = Room.objects.create(name="lobby")
    Membership.objects.create(room=room, user=alice)
    return room


def _room_url(room: Room) -> str:
    return reverse("chat_lv2:room", args=[room.id])


# The room page: refused before it renders


@pytest.mark.integration
def test_an_anonymous_visitor_is_sent_to_sign_in_and_back(room):
    response = Client().get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("chat_lv2:index") + "?next=")
    assert _room_url(room) in response["Location"].replace("%2F", "/")


@pytest.mark.integration
def test_a_signed_in_user_who_is_not_a_member_is_sent_to_the_room_list(room, bob):
    client = Client()
    client.force_login(bob)

    response = client.get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"] == reverse("chat_lv2:index")
    assert b"Lv2ChatRoom" not in response.content


@pytest.mark.integration
def test_a_member_gets_the_room(room, alice):
    client = Client()
    client.force_login(alice)

    response = client.get(_room_url(room))

    assert response.status_code == 200
    assert {"Lv2ChatRoom", "Lv2MessageList", "Lv2OnlineUsers"} <= set(_states(response.content.decode()))


@pytest.mark.integration
def test_joining_from_the_list_makes_a_member_and_leaving_ends_it(room, bob):
    client = Client()
    client.force_login(bob)

    assert client.post(reverse("chat_lv2:join_room", args=[room.id]))["Location"] == _room_url(room)
    assert client.get(_room_url(room)).status_code == 200

    client.post(reverse("chat_lv2:leave_room", args=[room.id]))
    assert not Membership.objects.filter(room=room, user=bob).exists()
    assert client.get(_room_url(room)).status_code == 302


@pytest.mark.integration
def test_creating_a_room_makes_its_creator_a_member(bob):
    client = Client()
    client.force_login(bob)

    response = client.post(reverse("chat_lv2:create_room"), {"name": "bob's"})

    room = Room.objects.get(name="bob's")
    assert response["Location"] == _room_url(room)
    assert Membership.objects.filter(room=room, user=bob).exists()


# Over a real socket: the join is refused without the view


class _States(HTMLParser):
    """Each component's ``data-state`` on a page, by component name."""

    def __init__(self):
        super().__init__()
        self.states: dict[str, tuple[str, str]] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-name" in attrs and "data-state" in attrs:
            self.states[attrs["data-name"]] = (attrs["id"], attrs["data-state"])


def _states(html: str) -> dict[str, tuple[str, str]]:
    parser = _States()
    parser.feed(html)
    return parser.states


async def _socket(client: AsyncClient) -> WebsocketCommunicator:
    """A socket carrying ``client``'s cookies, through the project's ASGI stack."""
    cookie = "; ".join(f"{name}={morsel.value}" for name, morsel in client.cookies.items())
    communicator = WebsocketCommunicator(application, "/__wireview__?vsn=99", headers=[(b"cookie", cookie.encode())])
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _join(communicator: WebsocketCommunicator, name: str, state: str) -> None:
    await communicator.send_json_to({"command": "join", "payload": {"name": name, "state": state, "children": {}}})


async def _answer(communicator: WebsocketCommunicator, component_id: str) -> dict:
    """The first render or remove for ``component_id``: how the server answered its join."""
    while True:
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] in ("render", "remove") and message["payload"].get("id") == component_id:
            return message


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_member_who_left_cannot_join_with_the_page_they_kept(room, alice, bob):
    """bob was a member and kept the page. After he leaves, its states no longer join.

    The socket goes through the project's real ASGI stack with bob's cookies:
    nothing here passes the room view, which is what an open tab, or a client
    replaying a page it saved, does.
    """
    await Membership.objects.acreate(room=room, user=bob)
    await Message.objects.acreate(room=room, author=alice, content="members only")
    client = AsyncClient()
    await client.aforce_login(bob)
    page = await client.get(_room_url(room))
    states = _states(page.content.decode())

    # While he is a member, the list joins and shows the room's messages
    communicator = await _socket(client)
    try:
        list_id, list_state = states["Lv2MessageList"]
        await _join(communicator, "Lv2MessageList", list_state)
        assert (await _answer(communicator, list_id))["command"] == "render"
    finally:
        await communicator.disconnect()

    await Membership.objects.filter(room=room, user=bob).adelete()

    communicator = await _socket(client)
    try:
        for name, (component_id, state) in states.items():
            await _join(communicator, name, state)
            answer = await _answer(communicator, component_id)
            assert answer["command"] == "remove", name
        # Nothing of the room went out after the refusals either: no stream of its messages
        heard = []
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
        assert "members only" not in json.dumps(heard)
    finally:
        await communicator.disconnect()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_message_is_written_by_the_connection_whatever_the_event_says(room, alice, bob):
    """alice's socket sends a message that names bob every way an event can. It is hers."""
    await Membership.objects.acreate(room=room, user=bob)
    client = AsyncClient()
    await client.aforce_login(alice)
    page = await client.get(_room_url(room))
    room_id, room_state = _states(page.content.decode())["Lv2ChatRoom"]

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv2ChatRoom", room_state)
        assert (await _answer(communicator, room_id))["command"] == "render"
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {
                    "id": room_id,
                    "command": "send_message",
                    # A form field and explicit arguments, all claiming to be bob
                    "implicit_args": {"content": ["hello"], "username": ["bob"], "author": ["bob"], "user": ["bob"]},
                    "explicit_args": {"username": "bob", "author_id": bob.pk},
                },
            }
        )
        await communicator.receive_json_from(timeout=5)
    finally:
        await communicator.disconnect()

    assert [(m.author_id, m.content) async for m in Message.objects.all()] == [(alice.pk, "hello")]


@pytest.mark.unit
def test_no_state_field_holds_a_name():
    """A field is state the browser holds and sends back. Names come from self.user."""
    for component in (Lv2ChatRoom, Lv2MessageList, Lv2OnlineUsers):
        assert not {"username", "author", "user_id"} & set(component.model_fields), component


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_author_is_the_user_and_presence_is_keyed_by_pk(room, alice):
    view = await mount(Lv2ChatRoom, user=alice, room=room, live_session=BOUNDARY)

    await view.call("send_message", content="  hi  ")

    message = await Message.objects.select_related("author").aget()
    assert (message.author, message.content) == (alice, "hi")
    assert view.component._presence_user_id() == str(alice.pk)
    assert view.component._presence_username() == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_leaving_the_open_page_writes_nothing(room, alice):
    """Events of a mounted component are not authorized again by the boundary; the handler asks."""
    view = await mount(Lv2ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    await Membership.objects.filter(room=room, user=alice).adelete()

    await view.call("send_message", content="still here?")

    assert not await Message.objects.aexists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_leaving_from_the_room_ends_the_membership_and_tells_the_users_pages(room, alice):
    view = await mount(Lv2ChatRoom, user=alice, room=room, live_session=BOUNDARY)

    await view.call("leave_room")

    assert not await Membership.objects.filter(room=room, user=alice).aexists()
    view.assert_redirected_to(reverse("chat_lv2:index"))
    assert view.component.get_subscriptions() == {membership_topic(room.id, alice.pk)}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_member_is_refused_when_mounted(room, bob):
    """The boundary's hook, as a unit test sees it: nothing of the room renders for bob."""
    view = await mount(Lv2MessageList, user=bob, room=room, live_session=BOUNDARY)

    assert not view.render()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_message_reaches_the_lists_of_its_room_only(room, alice):
    """send_message broadcasts the new message: every list of the room shows it, another room's does not."""
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=alice)
    lists = [
        await mount(Lv2MessageList, id="a", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv2MessageList, id="b", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv2MessageList, id="c", user=alice, room=elsewhere, live_session=BOUNDARY),
    ]
    chat = await mount(Lv2ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    for view in lists:
        view.clear_messages()

    await chat.call("send_message", content="hello <b>room</b>")

    for view in lists[:2]:
        html = view.stream_html("messages")
        assert "hello &lt;b&gt;room&lt;/b&gt;" in html
        assert "alice" in html
        assert [op["at"] for op in view.stream_ops("messages")] == [0]
    assert lists[2].stream_ops() == []


# In a browser: two people, two sets of cookies, one room


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


def _signed_in(browser, server: str, username: str):
    """A page of its own -- ``new_page()`` opens a new context, so its own cookies."""
    page = browser.new_page()
    page.goto(f"{server}/chat-lv2/")
    page.get_by_role("button", name=f"Sign in as {username}").click()
    expect_text(page.get_by_test_id("signed-in-as"), username)
    return page


def _in_room(page, room_url: str) -> None:
    open_live(page, room_url, '[data-name=Lv2ChatRoom][data-is-live="true"]')
    wait_live(page, '[data-name=Lv2MessageList][data-is-live="true"]')
    wait_live(page, '[data-name=Lv2OnlineUsers][data-is-live="true"]')


def _say(page, text: str) -> None:
    page.locator("input[name=content]").fill(text)
    page.locator("input[name=content]").press("Enter")


@pytest.mark.e2e
def test_two_members_see_each_others_messages_and_presence(browser, server):
    alice = _signed_in(browser, server, "alice")
    alice.locator("input[name=name]").fill("tea")
    alice.get_by_role("button", name="Create").click()
    wait_live(alice, '[data-name=Lv2ChatRoom][data-is-live="true"]')
    room_url = alice.url

    bob = _signed_in(browser, server, "bob")
    # Not a member yet: the room sends him back to the list
    bob.goto(room_url)
    expect_count(bob.locator("[data-name=Lv2ChatRoom]"), 0)
    bob.locator('[data-room="tea"]').get_by_role("button", name="Join").click()
    _in_room(bob, room_url)
    _in_room(alice, room_url)

    for page in (alice, bob):
        expect_count(page.locator(".online-user"), 2)
        expect_count(page.locator(".online-user", has_text="alice"), 1)
        expect_count(page.locator(".online-user", has_text="bob"), 1)

    _say(alice, "hi bob")
    _say(bob, "hi alice")

    for page in (alice, bob):
        expect_count(page.locator("[wire-stream=messages] li[id^=messages-]"), 2)
        expect_text(page.locator("[wire-stream=messages] li", has_text="hi bob").locator("strong"), "alice")
        expect_text(page.locator("[wire-stream=messages] li", has_text="hi alice").locator("strong"), "bob")
