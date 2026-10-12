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
from django.db import transaction
from django.test import AsyncClient, Client
from django.urls import reverse
from playwright.sync_api import expect
from testproj.asgi import application
from testproj.e2e_browser import expect_count, expect_text, wait_live
from testproj.e2e_server import serve

from wireview import abroadcast, mount, telemetry

from . import live
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


@pytest.fixture
def published():
    """Each topic wireview publishes to, and whether a transaction was still open then.

    Watched through the public telemetry signal, below ``broadcast()``: the
    test sees when the notice really leaves, whoever defers it.
    """
    seen = []

    def record(sender, topic, **info):
        seen.append((topic, transaction.get_connection().in_atomic_block))

    telemetry.enable()
    telemetry.broadcast_published.connect(record)
    try:
        yield seen
    finally:
        telemetry.broadcast_published.disconnect(record)
        telemetry.disable()


@pytest.mark.integration
def test_the_list_tells_the_open_pages_once_the_leave_is_committed(room, bob, published):
    """A page told asks ``is_member`` on a connection of its own. Inside the view's
    transaction (ATOMIC_REQUESTS) the delete is not visible to it yet."""
    Membership.objects.create(room=room, user=bob)
    client = Client()
    client.force_login(bob)

    client.post(reverse("chat_lv2:leave_room", args=[room.id]))

    assert published == [(membership_topic(room.id, bob.pk), False)]


@pytest.mark.integration
def test_a_leave_rolled_back_tells_no_one(room, bob, published):
    Membership.objects.create(room=room, user=bob)

    with pytest.raises(RuntimeError), transaction.atomic():
        live.end_membership(room, bob)
        raise RuntimeError

    assert published == []
    assert Membership.objects.filter(room=room, user=bob).exists()


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


async def _drain(communicator: WebsocketCommunicator, quiet: float = 0.5) -> list[str]:
    """Every frame the socket gets until it has been ``quiet`` seconds without one.

    ``receive_nothing`` and not a timeout on ``receive_output``: the latter
    cancels the application when it runs out.
    """
    heard = []
    while not await communicator.receive_nothing(timeout=quiet):
        output = await communicator.receive_output()
        if output.get("type") == "websocket.send":
            heard.append(output.get("text") or "")
    return heard


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
        heard = await _drain(communicator)
        # Nor later: a refused component subscribed to nothing, so alice's new message passes him by
        speaker = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
        await speaker.call("send_message", content="after the refusal")
        heard += await _drain(communicator)
    finally:
        await communicator.disconnect()

    assert not any("members only" in text or "after the refusal" in text for text in heard), heard


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["room list", "another page of the room"])
async def test_leaving_sends_the_users_open_page_away_and_it_hears_the_room_no_more(room, alice, bob, where):
    """bob keeps a page of the room open and leaves elsewhere. The page is told to go.

    That is a request, and a client may ignore it and keep the socket, as this
    one does. What the page would hear is stopped on the server: alice arriving,
    typing and writing reaches her own socket and not his.
    """
    await Membership.objects.acreate(room=room, user=bob)
    bob_client = AsyncClient()
    await bob_client.aforce_login(bob)
    bob_states = _states((await bob_client.get(_room_url(room))).content.decode())
    kept = await _socket(bob_client)
    alice_socket = None
    try:
        for name, (component_id, state) in bob_states.items():
            await _join(kept, name, state)
            assert (await _answer(kept, component_id))["command"] == "render", name
        await _drain(kept)

        if where == "room list":
            await bob_client.post(reverse("chat_lv2:leave_room", args=[room.id]))
        else:
            other = await mount(Lv2ChatRoom, id="other-tab", user=bob, room=room, live_session=BOUNDARY)
            await other.call("leave_room")
        told = [json.loads(text) for text in await _drain(kept)]
        assert {"command": "redirect", "url": reverse("chat_lv2:index")} in [
            {key: message["payload"].get(key) for key in ("command", "url")}
            for message in told
            if message["command"] == "url_change"
        ], told

        alice_client = AsyncClient()
        await alice_client.aforce_login(alice)
        alice_states = _states((await alice_client.get(_room_url(room))).content.decode())
        alice_socket = await _socket(alice_client)
        for name, (component_id, state) in alice_states.items():
            await _join(alice_socket, name, state)
            assert (await _answer(alice_socket, component_id))["command"] == "render", name
        chat_id = alice_states["Lv2ChatRoom"][0]
        for command, args in (("on_typing", {}), ("send_message", {"content": ["after bob left"]})):
            await alice_socket.send_json_to(
                {
                    "command": "user_event",
                    "payload": {"id": chat_id, "command": command, "implicit_args": args, "explicit_args": {}},
                }
            )
        # alice's page shows what she did, so it happened
        assert any("after bob left" in text for text in await _drain(alice_socket))

        assert await _drain(kept) == []
    finally:
        await kept.disconnect()
        if alice_socket is not None:
            await alice_socket.disconnect()


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
    view = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)

    await view.call("send_message", content="  hi  ")

    message = await Message.objects.select_related("author").aget()
    assert (message.author, message.content) == (alice, "hi")
    assert view.component._presence_user_id() == str(alice.pk)
    assert view.component._presence_username() == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_leaving_the_open_page_writes_nothing(room, alice):
    """Events of a mounted component are not authorized again by the boundary; the handler asks."""
    view = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    await Membership.objects.filter(room=room, user=alice).adelete()

    await view.call("send_message", content="still here?")

    assert not await Message.objects.aexists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("still_member", [True, False])
async def test_after_leaving_the_open_page_does_not_say_it_is_typing(room, alice, still_member):
    """The typing indicator is the room's to see: the handler asks before it tells them.

    The membership ends with no notice, as when it is deleted some other way.
    """
    view = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    if not still_member:
        await Membership.objects.filter(room=room, user=alice).adelete()

    await view.call("on_typing")

    typing = [b["kwargs"]["state"] for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_typing"]
    assert typing == (["typing"] if still_member else [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_leaving_from_the_room_ends_the_membership_and_the_page_hears_nothing_more(room, alice):
    view = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)

    await view.call("leave_room")

    assert not await Membership.objects.filter(room=room, user=alice).aexists()
    view.assert_redirected_to(reverse("chat_lv2:index"))
    assert view.component.get_subscriptions() == set()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("left", [True, False])
async def test_a_page_says_once_that_its_user_is_gone(room, alice, left):
    """A page that left the room said so then. When its socket closes later it does
    not say it again: by then the user may have joined again on another page."""
    view = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    if left:
        await view.call("leave_room")

    await view.component.leaving()

    leaves = [b for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_leave"]
    assert len(leaves) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("component", [Lv2ChatRoom, Lv2MessageList, Lv2OnlineUsers])
async def test_told_of_the_end_a_component_asks_again_and_gives_up_the_room(room, alice, component):
    """The notice says only "ask again": a component that is still a member keeps listening.

    Published as the app publishes it; the mounted component hears it as a page does (#201).
    """
    view = await mount(component, id="room", user=alice, room=room, live_session=BOUNDARY)
    topic = membership_topic(room.id, alice.pk)
    listening = view.component.get_subscriptions()
    assert topic in listening

    await abroadcast(topic)
    assert view.component.get_subscriptions() == listening
    view.assert_no_navigation()

    await Membership.objects.filter(room=room, user=alice).adelete()
    await abroadcast(topic)

    assert view.component.get_subscriptions() == set()
    if component is Lv2ChatRoom:
        view.assert_redirected_to(reverse("chat_lv2:index"))
        # Gone from the others' online lists too, whatever the page does next
        assert view.presence_broadcasts[-1]["kwargs"]["action"] == "presence_leave"


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
    chat = await mount(Lv2ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    for view in lists:
        view.clear_messages()

    await chat.call("send_message", content="hello <b>room</b>")

    for view in lists[:2]:
        html = view.stream_html("messages")
        assert "hello &lt;b&gt;room&lt;/b&gt;" in html
        assert "alice" in html
        assert [op["at"] for op in view.stream_ops("messages")] == [0]
    assert lists[2].stream_ops() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_empty_rooms_note_is_outside_the_list_a_reset_empties(room, alice):
    """In the list, the first render showed it in a room with messages and the join's reset took it from an empty one.

    base.html shows it while the list holds nothing (the browser half: the E2E).
    """
    await Message.objects.acreate(room=room, author=alice, content="hello")
    view = await mount(Lv2MessageList, room=room, user=alice, live_session=BOUNDARY)

    html = view.render() or ""

    start = html.index('wire-stream="messages"')
    assert "No messages yet" not in html[start : html.index("</ul>", start)]
    assert "No messages yet" in html[html.index("</ul>", start) :]
    assert len(view.stream_items("messages")) == 1


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
    """Go to the room and wait until its components have joined."""
    page.goto(room_url)
    _joined_room(page, room_url)


def _joined_room(page, room_url: str) -> None:
    """Wait for the room the page is going to -- by a click -- to be live. It does not go there itself.

    A goto of its own after the click would race the click's navigation to the
    same URL, and one of the two is aborted.
    """
    expect(page).to_have_url(room_url)
    wait_live(page, '[data-name=Lv2ChatRoom][data-is-live="true"]')
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
    _joined_room(bob, room_url)
    _in_room(alice, room_url)

    for page in (alice, bob):
        expect_count(page.locator(".online-user"), 2)
        expect_count(page.locator(".online-user", has_text="alice"), 1)
        expect_count(page.locator(".online-user", has_text="bob"), 1)
        # A new room: the join's reset emptied the list, and the note outside it shows
        expect(page.locator(".empty-message")).to_be_visible()

    _say(alice, "hi bob")
    _say(bob, "hi alice")

    for page in (alice, bob):
        expect_count(page.locator("[wire-stream=messages] li[id^=messages-]"), 2)
        expect(page.locator(".empty-message")).to_be_hidden()
        expect_text(page.locator("[wire-stream=messages] li", has_text="hi bob").locator("strong"), "alice")
        expect_text(page.locator("[wire-stream=messages] li", has_text="hi alice").locator("strong"), "bob")
