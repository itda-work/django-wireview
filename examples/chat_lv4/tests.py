"""What this example is for: the server decides who is speaking, and who is in the room.

A message's author is the connection's user, whatever the browser sends. The
room page and every component on it are for the room's members: the view
refuses anyone else before it renders, and the boundary's hook refuses them on
every join -- a socket that never went through the view included.

And a stream is more than a list things are appended to: the list loads the
room's past a page at a time as the user scrolls up, and an author's edit or
delete reaches every page of the room -- the pages that show the message.

And some news is one person's: each member's room list shows how many messages
of each room they have not read. A new message tells the other members, each
on a topic of their own, and an open page of the room reads it.
"""

import json
import threading
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import AsyncClient, Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect
from testproj.asgi import application
from testproj.e2e_browser import expect_count, expect_text, open_live, wait_live
from testproj.e2e_server import serve
from testproj.queries import capture_queries
from testproj.waiting import eventually

from wireview import Broadcast, abroadcast, mount, telemetry

from . import live
from .live import (
    PAGE_SIZE,
    Lv4ChatRoom,
    Lv4MessageList,
    Lv4OnlineUsers,
    Lv4RoomList,
    membership_topic,
    unread_counts,
    user_topic,
)
from .models import Membership, Message, Room

# Handlers and the socket write from other threads, on connections of their own,
# so each test commits and the tables are flushed after it (#133).
pytestmark = pytest.mark.django_db(transaction=True)

BOUNDARY = "chat_lv4"
# The room list's page: a boundary with no policy, so that a logout closes it
ROOMS = "chat_lv4_rooms"


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
    return reverse("chat_lv4:room", args=[room.id])


# The room page: refused before it renders


@pytest.mark.integration
def test_an_anonymous_visitor_is_sent_to_sign_in_and_back(room):
    response = Client().get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("chat_lv4:index") + "?next=")
    assert _room_url(room) in response["Location"].replace("%2F", "/")


@pytest.mark.integration
def test_a_signed_in_user_who_is_not_a_member_is_sent_to_the_room_list(room, bob):
    client = Client()
    client.force_login(bob)

    response = client.get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"] == reverse("chat_lv4:index")
    assert b"Lv4ChatRoom" not in response.content


@pytest.mark.integration
def test_a_member_gets_the_room(room, alice):
    client = Client()
    client.force_login(alice)

    response = client.get(_room_url(room))

    assert response.status_code == 200
    assert {"Lv4ChatRoom", "Lv4MessageList", "Lv4OnlineUsers"} <= set(_states(response.content.decode()))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_joining_from_the_list_makes_a_member_and_leaving_ends_it(room, bob, published):
    """Join and Leave are the room list's events: a form in a live render would have no CSRF token.

    Either way the user's other room lists are told, on the user's topic.
    """
    client = AsyncClient()
    await client.aforce_login(bob)
    rooms = await mount(Lv4RoomList, user=bob, live_session=ROOMS)

    await rooms.call("join_room", room_id=room.id)
    rooms.assert_redirected_to(_room_url(room))
    assert user_topic(bob.pk) in [topic for topic, _ in published]
    assert (await client.get(_room_url(room))).status_code == 200

    rooms = await mount(Lv4RoomList, user=bob, live_session=ROOMS)
    await rooms.call("leave_room", room_id=room.id)
    assert not await Membership.objects.filter(room=room, user=bob).aexists()
    assert (await client.get(_room_url(room))).status_code == 302


@pytest.fixture
def published():
    """Each topic wireview publishes to, and whether a transaction was still open then.

    Watched through the public telemetry signal, below ``broadcast()``: the
    test sees when the notice really leaves, whoever defers it.
    """
    seen = []

    def record(sender, topic, **info):
        if info.get("kind") == "notification":
            seen.append((topic, transaction.get_connection().in_atomic_block))

    telemetry.enable()
    telemetry.broadcast_published.connect(record)
    try:
        yield seen
    finally:
        telemetry.broadcast_published.disconnect(record)
        telemetry.disable()


@pytest.mark.integration
def test_a_leave_tells_the_open_pages_once_it_is_committed(room, bob, published):
    """A page told asks ``is_member`` on a connection of its own. Inside the caller's
    transaction the delete is not visible to it yet."""
    Membership.objects.create(room=room, user=bob)

    with transaction.atomic():
        live.end_membership(room, bob)
        assert published == []

    assert published == [(membership_topic(room.id, bob.pk), False), (user_topic(bob.pk), False)]


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

    response = client.post(reverse("chat_lv4:create_room"), {"name": "bob's"})

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
        list_id, list_state = states["Lv4MessageList"]
        await _join(communicator, "Lv4MessageList", list_state)
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
        speaker = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
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
            await (await mount(Lv4RoomList, user=bob, live_session=ROOMS)).call("leave_room", room_id=room.id)
        else:
            other = await mount(Lv4ChatRoom, id="other-tab", user=bob, room=room, live_session=BOUNDARY)
            await other.call("leave_room")
        told = [json.loads(text) for text in await _drain(kept)]
        assert {"command": "redirect", "url": reverse("chat_lv4:index")} in [
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
        chat_id = alice_states["Lv4ChatRoom"][0]
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
    room_id, room_state = _states(page.content.decode())["Lv4ChatRoom"]

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv4ChatRoom", room_state)
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
    for component in (Lv4ChatRoom, Lv4MessageList, Lv4OnlineUsers, Lv4RoomList):
        assert not {"username", "author", "user_id"} & set(component.model_fields), component


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_author_is_the_user_and_presence_is_keyed_by_pk(room, alice):
    view = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)

    await view.call("send_message", content="  hi  ")

    message = await Message.objects.select_related("author").aget()
    assert (message.author, message.content) == (alice, "hi")
    assert view.component._presence_user_id() == str(alice.pk)
    assert view.component._presence_username() == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_leaving_the_open_page_writes_nothing(room, alice):
    """Events of a mounted component are not authorized again by the boundary; the handler asks."""
    view = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
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
    view = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    if not still_member:
        await Membership.objects.filter(room=room, user=alice).adelete()

    await view.call("on_typing")

    typing = [b["kwargs"]["state"] for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_typing"]
    assert typing == (["typing"] if still_member else [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_leaving_from_the_room_ends_the_membership_and_the_page_hears_nothing_more(room, alice):
    view = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)

    await view.call("leave_room")

    assert not await Membership.objects.filter(room=room, user=alice).aexists()
    view.assert_redirected_to(reverse("chat_lv4:index"))
    assert view.component.get_subscriptions() == set()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("left", [True, False])
async def test_a_page_says_once_that_its_user_is_gone(room, alice, left):
    """A page that left the room said so then. When its socket closes later it does
    not say it again: by then the user may have joined again on another page."""
    view = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    if left:
        await view.call("leave_room")

    await view.component.leaving()

    leaves = [b for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_leave"]
    assert len(leaves) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("component", [Lv4ChatRoom, Lv4MessageList, Lv4OnlineUsers])
async def test_told_of_the_end_a_component_asks_again_and_gives_up_the_room(room, alice, component):
    """The notice says only "ask again": a component that is still a member keeps listening.

    Published as the app publishes it; the mounted component hears it as a page does (#201).
    """
    view = await mount(component, user=alice, room=room, live_session=BOUNDARY)
    topic = membership_topic(room.id, alice.pk)
    listening = view.component.get_subscriptions()
    assert topic in listening
    # The list reads what arrives on the user's own topic: that, too, ends with the membership
    assert (user_topic(alice.pk) in listening) is (component is Lv4MessageList)

    await abroadcast(topic)
    assert view.component.get_subscriptions() == listening
    view.assert_no_navigation()

    await Membership.objects.filter(room=room, user=alice).adelete()
    await abroadcast(topic)

    assert view.component.get_subscriptions() == set()
    if component is Lv4ChatRoom:
        view.assert_redirected_to(reverse("chat_lv4:index"))
        # Gone from the others' online lists too, whatever the page does next
        assert view.presence_broadcasts[-1]["kwargs"]["action"] == "presence_leave"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_member_is_refused_when_mounted(room, bob):
    """The boundary's hook, as a unit test sees it: nothing of the room renders for bob."""
    view = await mount(Lv4MessageList, user=bob, room=room, live_session=BOUNDARY)

    assert not view.render()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_message_reaches_the_lists_of_its_room_only(room, alice):
    """send_message broadcasts the new message: every list of the room shows it, another room's does not."""
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=alice)
    lists = [
        await mount(Lv4MessageList, id="a", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv4MessageList, id="b", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv4MessageList, id="c", user=alice, room=elsewhere, live_session=BOUNDARY),
    ]
    chat = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    for view in lists:
        view.clear_messages()

    await chat.call("send_message", content="hello <b>room</b>")

    for view in lists[:2]:
        html = view.stream_html("messages")
        assert "hello &lt;b&gt;room&lt;/b&gt;" in html
        assert "alice" in html
        assert [op["at"] for op in view.stream_ops("messages")] == [0]
    assert lists[2].stream_ops() == []


# The past, a page at a time


def _seed(room: Room, author, count: int, *, same_time: bool = False, start=None) -> list[Message]:
    """``count`` messages in ``room``, a second apart (or all at one time), returned newest first."""
    start = start or timezone.now() - timedelta(days=1)
    made = [Message.objects.create(room=room, author=author, content=f"{room.name} {i}") for i in range(count)]
    for i, message in enumerate(made):
        message.created_at = start if same_time else start + timedelta(seconds=i)
        Message.objects.filter(pk=message.pk).update(created_at=message.created_at)
    # The list's order: the time, then the id where two share it
    return sorted(made, key=lambda message: (message.created_at, message.id), reverse=True)


def _ids(messages: list[Message]) -> list[str]:
    return [f"messages-{message.pk}" for message in messages]


def _shown(view) -> list[str]:
    """The ids a page's list holds, first in the DOM first, after the stream operations ``view`` received.

    What the page does with them: a reset replaces the list, an insert's items
    go first (at=0) or last (at=-1) as a block in their order, an id already
    there is replaced where it stands, an update-only insert of one that is not
    there adds nothing, a delete removes.
    """
    shown: list[str] = []
    for op in view.stream_ops("messages"):
        ids = [item["id"] for item in op["items"]]
        if op["op"] == "reset":
            shown = ids
        elif op["op"] == "delete":
            shown = [dom_id for dom_id in shown if dom_id not in ids]
        else:
            new = [] if op.get("update_only") else [dom_id for dom_id in ids if dom_id not in shown]
            shown = [*new, *shown] if op["at"] == 0 else [*shown, *new]
    return shown


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("same_time", [False, True], ids=["a second apart", "all at one time"])
async def test_scrolling_up_loads_the_room_a_page_at_a_time_to_its_first_message(room, alice, same_time):
    """Each page is the one before the oldest shown: nothing skipped, nothing twice, even where times are equal.

    The cursor is (created_at, id). With the time alone, the messages that share
    the cursor's time are lost (``<``) or shown again (``<=``).
    """
    messages = await sync_to_async(_seed)(room, alice, 2 * PAGE_SIZE + 5, same_time=same_time)
    view = await mount(Lv4MessageList, user=alice, room=room, live_session=BOUNDARY)

    assert _shown(view) == _ids(messages[:PAGE_SIZE])
    assert "Loading older messages" in view.render()

    await view.call("load_older")
    assert _shown(view) == _ids(messages[: 2 * PAGE_SIZE])
    assert not view.component.reached_start

    async with capture_queries() as reading:
        await view.call("load_older")
    assert _shown(view) == _ids(messages)
    assert view.component.reached_start
    assert "The beginning of lobby" in view.render()
    # An older page goes above: the end of the DOM, the top of a list drawn bottom up.
    # Each page is one operation, one frame, not one per message
    inserts = [op for op in view.stream_ops("messages") if op["op"] == "insert"]
    assert [(op["at"], len(op["items"])) for op in inserts] == [(-1, PAGE_SIZE), (-1, 5)]

    # At the start it stops: a late scroll event asks nothing more of the database.
    # Not "loads nothing" -- a query past the start finds nothing either
    view.clear_messages()
    async with capture_queries() as stopped:
        await view.call("load_older")
    assert view.stream_ops() == []
    assert len(reading) > 0 and len(stopped) == 0, (len(reading), stopped.captured_queries)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "edit"])
async def test_a_message_changed_while_the_past_is_read_stays_changed(room, alice, monkeypatch, change):
    """Another member deletes or edits a message of the page while it is read (#199).

    The page goes out through the connection's channel, the Broadcast as it
    comes. Read before stream(), the page came after the patch: the delete found
    nothing and the page brought the message back, the edit found nothing and
    the page brought the message as it was.
    """
    messages = await sync_to_async(_seed)(room, alice, PAGE_SIZE + 5)
    view = await mount(Lv4MessageList, user=alice, room=room, live_session=BOUNDARY)
    target = messages[PAGE_SIZE + 2]
    history = live._history

    async def changed_while_read(room, before):
        read = history(room, before)
        async for message in read:
            yield message
            if before is not None and message.pk == messages[PAGE_SIZE].pk:
                broadcast = Broadcast(Lv4MessageList, live.room_topic(room))
                if change == "delete":
                    await Message.objects.filter(pk=target.pk).adelete()
                    await broadcast.stream_delete("messages", f"messages-{target.pk}").asend()
                else:
                    target.content = "changed"
                    await target.asave(update_fields=["content"])
                    await broadcast.stream_insert("messages", target, update_only=True).asend()

    monkeypatch.setattr(live, "_history", changed_while_read)
    await view.call("load_older")

    shown = _shown(view)
    if change == "delete":
        assert f"messages-{target.pk}" not in shown
        assert shown == _ids([m for m in messages if m.pk != target.pk])
    else:
        assert shown == _ids(messages)
        # The last word on it is the edit, behind the page
        dom_id = f"messages-{target.pk}"
        last = [op for op in view.stream_ops("messages") if any(item["id"] == dom_id for item in op["items"])][-1]
        assert last.get("update_only") and "changed" in last["items"][0]["html"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "reached_start"), [(0, True), (PAGE_SIZE, True), (PAGE_SIZE + 1, False)])
async def test_a_room_that_fits_one_page_shows_its_beginning_at_once(room, alice, count, reached_start):
    """The list reads one message more than it shows: that one says whether anything is above."""
    await sync_to_async(_seed)(room, alice, count)

    view = await mount(Lv4MessageList, user=alice, room=room, live_session=BOUNDARY)

    assert len(_shown(view)) == min(count, PAGE_SIZE)
    assert view.component.reached_start is reached_start


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_past_is_the_rooms_whatever_the_cursor_says(room, alice):
    """The cursor names a time and an id, and the room is the query's own.

    The cursor is in the signed state, so a browser cannot change it (a changed
    state does not join: the next test). Here it points into another room, where
    alice is a member too and whose messages fall between this room's: the next
    page is still this room's.
    """
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=alice)
    start = timezone.now() - timedelta(days=1)
    mine = await sync_to_async(_seed)(room, alice, PAGE_SIZE + 3, start=start)
    theirs = await sync_to_async(_seed)(elsewhere, alice, PAGE_SIZE + 3, start=start + timedelta(milliseconds=500))
    view = await mount(Lv4MessageList, user=alice, room=room, live_session=BOUNDARY)
    view.component.oldest_at, view.component.oldest_id = theirs[0].created_at, theirs[0].id
    view.clear_messages()

    await view.call("load_older")

    loaded = _shown(view)
    assert loaded == _ids(mine[:PAGE_SIZE])
    assert not set(loaded) & set(_ids(theirs))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_changed_cursor_does_not_join(room, alice):
    """The browser holds the cursor in the list's signed state. Changed, the state is refused."""
    client = AsyncClient()
    await client.aforce_login(alice)
    list_id, state = _states((await client.get(_room_url(room))).content.decode())["Lv4MessageList"]
    tampered = state[:-3] + ("AAA" if not state.endswith("AAA") else "BBB")

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv4MessageList", tampered)
        answer = await communicator.receive_json_from(timeout=5)
    finally:
        await communicator.disconnect()

    assert answer["command"] == "reload", answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("still_member", [True, False])
async def test_a_page_loads_the_past_only_while_its_user_is_a_member(room, alice, still_member):
    """The membership ends with no notice, as when it is deleted some other way: the handler asks."""
    await sync_to_async(_seed)(room, alice, PAGE_SIZE + 3)
    view = await mount(Lv4MessageList, user=alice, room=room, live_session=BOUNDARY)
    if not still_member:
        await Membership.objects.filter(room=room, user=alice).adelete()
    view.clear_messages()

    await view.call("load_older")

    assert len(_shown(view)) == (3 if still_member else 0)


# Editing and deleting: the author, and the room's every page


@pytest.fixture
def bob_in_room(room, bob):
    Membership.objects.create(room=room, user=bob)
    return bob


async def _lists(room: Room, *users) -> list:
    """A message list of ``room`` for each user, each as its own page, with nothing received yet."""
    views = []
    for number, user in enumerate(users):
        view = await mount(Lv4MessageList, id=f"list-{number}", user=user, room=room, live_session=BOUNDARY)
        view.clear_messages()
        views.append(view)
    return views


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_author_edits_and_every_list_of_the_room_replaces_the_message_where_it_stands(
    room, alice, bob_in_room
):
    message = await Message.objects.acreate(room=room, author=alice, content="helo")
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=alice)
    alices, bobs = await _lists(room, alice, bob_in_room)
    (other,) = await _lists(elsewhere, alice)

    await alices.call("edit_message", message_id=message.id)
    assert (alices.component.editing, alices.component.draft) == (message.id, "helo")
    assert 'value="helo"' in alices.render()
    await alices.call("save_edit", text=" hello ")

    stored = await Message.objects.aget(pk=message.pk)
    assert stored.content == "hello" and stored.edited_at is not None
    for view in (alices, bobs):
        (op,) = view.stream_ops("messages")
        # The same DOM id, and only where a page shows it: one that never loaded it inserts nothing
        assert (op["op"], [item["id"] for item in op["items"]], op.get("update_only")) == (
            "insert",
            [f"messages-{message.pk}"],
            True,
        )
        assert "hello" in op["items"][0]["html"] and "(edited)" in op["items"][0]["html"]
    assert other.stream_ops() == []
    assert alices.component.editing is None
    assert "message-editor" not in alices.render()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_author_deletes_and_every_list_of_the_room_drops_the_message(room, alice, bob_in_room):
    message = await Message.objects.acreate(room=room, author=alice, content="oops")
    alices, bobs = await _lists(room, alice, bob_in_room)

    await alices.call("delete_message", message_id=message.id)

    assert not await Message.objects.filter(pk=message.pk).aexists()
    for view in (alices, bobs):
        assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("messages")] == [
            ("delete", [f"messages-{message.pk}"])
        ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deleting_the_message_being_edited_closes_the_editor(room, alice):
    message = await Message.objects.acreate(room=room, author=alice, content="oops")
    (view,) = await _lists(room, alice)
    await view.call("edit_message", message_id=message.id)

    await view.call("delete_message", message_id=message.id)

    assert view.component.editing is None
    assert "message-editor" not in view.render()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "save", "delete"])
async def test_a_member_cannot_change_someone_elses_message(room, alice, bob_in_room, action):
    """bob names alice's message, as a forged event would: the buttons are not on it, the event can still come.

    "save" is the editor opened on it some other way: saving asks again rather
    than trusting what the editor was opened on.
    """
    message = await Message.objects.acreate(room=room, author=alice, content="alice's")
    alices, bobs = await _lists(room, alice, bob_in_room)

    if action == "edit":
        await bobs.call("edit_message", message_id=message.id)
        assert bobs.component.editing is None
    elif action == "save":
        bobs.component.editing = message.id
        await bobs.call("save_edit", text="bob was here")
    else:
        await bobs.call("delete_message", message_id=message.id)

    stored = await Message.objects.aget(pk=message.pk)
    assert (stored.content, stored.edited_at) == ("alice's", None)
    assert alices.stream_ops() == [] and bobs.stream_ops() == []


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "save", "delete"])
async def test_after_leaving_the_author_cannot_change_their_messages(room, alice, action):
    """The membership ends with no notice, so the page is still open and still alice's: the handler asks."""
    message = await Message.objects.acreate(room=room, author=alice, content="mine")
    (view,) = await _lists(room, alice)
    if action == "save":
        await view.call("edit_message", message_id=message.id)
    await Membership.objects.filter(room=room, user=alice).adelete()
    view.clear_messages()

    if action == "edit":
        await view.call("edit_message", message_id=message.id)
        assert view.component.editing is None
    elif action == "save":
        await view.call("save_edit", text="after I left")
    else:
        await view.call("delete_message", message_id=message.id)

    stored = await Message.objects.aget(pk=message.pk)
    assert (stored.content, stored.edited_at) == ("mine", None)
    assert view.stream_ops() == []


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "delete"])
async def test_a_message_of_a_room_the_author_left_cannot_be_changed_from_another_room(room, alice, action):
    """alice wrote in elsewhere and left it. From this room's page, where she is a member, she names that message.

    The membership asked is this room's, so the message must be this room's
    too: otherwise a room she is in would reach one she left.
    """
    elsewhere = await Room.objects.acreate(name="elsewhere")
    message = await Message.objects.acreate(room=elsewhere, author=alice, content="said in elsewhere")
    (view,) = await _lists(room, alice)

    if action == "edit":
        await view.call("edit_message", message_id=message.id)
        assert view.component.editing is None
        view.component.editing = message.id
        await view.call("save_edit", text="changed from lobby")
    else:
        await view.call("delete_message", message_id=message.id)

    stored = await Message.objects.aget(pk=message.pk)
    assert (stored.content, stored.edited_at) == ("said in elsewhere", None)
    assert view.stream_ops() == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_forged_event_over_the_socket_deletes_nothing_of_someone_else(room, alice, bob_in_room):
    """bob's socket sends delete and edit for alice's message, as the browser would with her id."""
    message = await Message.objects.acreate(room=room, author=alice, content="alice's")
    client = AsyncClient()
    await client.aforce_login(bob_in_room)
    list_id, state = _states((await client.get(_room_url(room))).content.decode())["Lv4MessageList"]

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv4MessageList", state)
        assert (await _answer(communicator, list_id))["command"] == "render"
        for command, args in (
            ("delete_message", {"message_id": str(message.pk)}),
            ("edit_message", {"message_id": str(message.pk)}),
        ):
            await communicator.send_json_to(
                {
                    "command": "user_event",
                    "payload": {"id": list_id, "command": command, "implicit_args": {}, "explicit_args": args},
                }
            )
        heard = await _drain(communicator)
    finally:
        await communicator.disconnect()

    assert await Message.objects.filter(pk=message.pk, content="alice's").aexists()
    assert not any("message-editor" in text or '"op":"delete"' in text.replace(" ", "") for text in heard), heard


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_page_whose_user_left_gets_no_edit_or_delete(room, alice, bob_in_room):
    """bob keeps a page of the room open, ignoring the redirect, and leaves on the room list.

    While he is a member his page gets alice's edit -- so what follows is not a
    socket that hears nothing anyway. After he leaves it gets neither her next
    edit nor her delete, which her own page does.

    "Neither" is told by order, not by a quiet spell: the same socket also holds
    the list of another room bob is still in, and a marker published to that
    room after the delete arrives through the same channel behind anything
    published before it. What came before the marker is all the lobby sent.
    """
    message = await Message.objects.acreate(room=room, author=alice, content="before")
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=bob_in_room)
    bob_client = AsyncClient()
    await bob_client.aforce_login(bob_in_room)
    bob_states = _states((await bob_client.get(_room_url(room))).content.decode())
    other_id, other_state = _states((await bob_client.get(_room_url(elsewhere))).content.decode())["Lv4MessageList"]
    kept = await _socket(bob_client)
    try:
        for name, (component_id, state) in [*bob_states.items(), ("Lv4MessageList", (other_id, other_state))]:
            await _join(kept, name, state)
            assert (await _answer(kept, component_id))["command"] == "render", name
        await _drain(kept)
        (speaker,) = await _lists(room, alice)

        await speaker.call("edit_message", message_id=message.id)
        await speaker.call("save_edit", text="while bob is here")
        assert any("while bob is here" in text for text in await _drain(kept))

        await (await mount(Lv4RoomList, user=bob_in_room, live_session=ROOMS)).call("leave_room", room_id=room.id)
        assert any('"redirect"' in text for text in await _drain(kept))

        speaker.clear_messages()
        await speaker.call("edit_message", message_id=message.id)
        await speaker.call("save_edit", text="after bob left")
        await speaker.call("delete_message", message_id=message.id)
        assert [op["op"] for op in speaker.stream_ops("messages")] == ["insert", "delete"]
        await Broadcast(Lv4MessageList, live.room_topic(elsewhere)).push_event("marker").asend()

        heard = []
        while not heard or '"marker"' not in heard[-1]:
            heard.append(json.dumps(await kept.receive_json_from(timeout=5)))
        assert heard[:-1] == []
        assert json.loads(heard[-1])["payload"]["component_id"] == other_id
    finally:
        await kept.disconnect()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_empty_rooms_note_is_outside_the_list_a_reset_empties(room, alice):
    """In the list, the first render showed it in a room with messages and the join's reset took it from an empty one.

    base.html shows it while the list holds nothing (the browser half: the E2E).
    """
    await Message.objects.acreate(room=room, author=alice, content="hello")
    view = await mount(Lv4MessageList, room=room, user=alice, live_session=BOUNDARY)

    html = view.render() or ""

    start = html.index('wire-stream="messages"')
    assert "No messages yet" not in html[start : html.index("</ul>", start)]
    assert "No messages yet" in html[html.index("</ul>", start) :]
    assert len(view.stream_items("messages")) == 1


# Unread badges: news that is one person's


def _notices(published) -> list[str]:
    """The users told, in order: the notices on user topics, of everything ``published`` saw."""
    return [topic for topic, _ in published if topic.startswith("chat_lv4.user.")]


class _Badges(HTMLParser):
    """Each room's unread badge on a room list, by room name: rooms without one are left out."""

    def __init__(self):
        super().__init__()
        self.badges: dict[str, str] = {}
        self._room: str | None = None
        self._in_badge = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "data-room" in attrs:
            self._room = attrs["data-room"]
        self._in_badge = "unread" in (attrs.get("class") or "").split()

    def handle_data(self, data):
        if self._in_badge and self._room is not None:
            self.badges[self._room] = data.strip()
            self._in_badge = False


async def _badges(view) -> dict[str, str]:
    """What the room list ``view`` shows now. Rendered on a worker thread: the count is a query."""
    parser = _Badges()
    parser.feed(await sync_to_async(view.render)() or "")
    return parser.badges


async def _said(room: Room, author, content: str, *, after: Message | None = None) -> Message:
    """A message in ``room``, a minute after ``after`` when given: two messages written at once may share a time."""
    message = await Message.objects.acreate(room=room, author=author, content=content)
    if after is not None:
        message.created_at = after.created_at + timedelta(minutes=1)
        await Message.objects.filter(pk=message.pk).aupdate(created_at=message.created_at)
    return message


async def _cursor(room: Room, user) -> tuple:
    return await Membership.objects.filter(room=room, user=user).values_list("read_at", "read_id").aget()


@pytest.fixture
def carol():
    return get_user_model().objects.create(username="carol")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_new_message_tells_each_other_member_on_their_own_topic(room, alice, bob_in_room, carol, published):
    """alice writes in the lobby. bob, a member, is told on his topic and nobody else is.

    Not alice, who wrote it; not carol, a member of another room only; not
    dave, who was in the lobby and left. Who is told is who is a member when
    the message is written.
    """
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=carol)
    dave = await get_user_model().objects.acreate(username="dave")
    await Membership.objects.acreate(room=room, user=dave)
    await sync_to_async(live.end_membership)(room, dave)
    speaker = await mount(Lv4ChatRoom, user=alice, room=room, live_session=BOUNDARY)
    published.clear()

    await speaker.call("send_message", content="hello")

    assert _notices(published) == [user_topic(bob_in_room.pk)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_delete_tells_the_other_members_and_an_edit_does_not(room, alice, bob_in_room, published):
    """A deleted message may be one bob has not read: his count changes. An edited one is still one message.

    Both are published while the handler runs, so the empty list after the
    edit is the whole answer, not a quiet spell.
    """
    message = await Message.objects.acreate(room=room, author=alice, content="hello")
    (alices,) = await _lists(room, alice)
    published.clear()

    await alices.call("edit_message", message_id=message.id)
    await alices.call("save_edit", text="hello again")
    assert _notices(published) == []

    await alices.call("delete_message", message_id=message.id)
    assert _notices(published) == [user_topic(bob_in_room.pk)]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_badge_counts_the_others_messages_and_opening_the_room_clears_it(room, alice, bob_in_room):
    """Never opened, every message alice wrote is unread; bob's own never are. Opening the room reads it all."""
    await sync_to_async(_seed)(room, alice, 3)
    await Message.objects.acreate(room=room, author=bob_in_room, content="bob's own")
    rooms = await mount(Lv4RoomList, user=bob_in_room, live_session=ROOMS)
    assert await sync_to_async(unread_counts)(bob_in_room) == {room.id: 3}
    assert await _badges(rooms) == {"lobby": "3"}

    # bob opens the room: its list shows the newest message
    await mount(Lv4MessageList, user=bob_in_room, room=room, live_session=BOUNDARY)
    newest = await Message.objects.order_by("-created_at", "-id").afirst()
    assert await _cursor(room, bob_in_room) == (newest.created_at, newest.id)
    assert await sync_to_async(unread_counts)(bob_in_room) == {}
    assert await _badges(rooms) == {}
    # bob's membership only: alice has read nothing by it
    assert await _cursor(room, alice) == (None, None)

    # What alice writes after is unread again; what bob writes is not
    later = await _said(room, alice, "later", after=newest)
    await _said(room, bob_in_room, "mine", after=later)
    assert await _badges(rooms) == {"lobby": "1"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_cursor_only_moves_forward(room, alice, bob_in_room, published):
    """A page that read less -- a tab that joined before another read on -- does not take the cursor back.

    Nor does it tell bob's lists anything: nothing moved.
    """
    message = await Message.objects.acreate(room=room, author=alice, content="hello")
    ahead = (message.created_at + timedelta(minutes=1), message.id)
    await Membership.objects.filter(room=room, user=bob_in_room).aupdate(read_at=ahead[0], read_id=ahead[1])
    published.clear()

    await mount(Lv4MessageList, user=bob_in_room, room=room, live_session=BOUNDARY)

    assert await _cursor(room, bob_in_room) == ahead
    assert _notices(published) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_notice_moves_the_cursor_to_the_newest_message_of_the_pages_own_room(room, alice, bob_in_room):
    """bob's lobby page is told of a new lobby message while another room of his has a newer one.

    The cursor is a place in the lobby's history: it names the lobby's newest
    message, never another room's, however much newer that is. The notice goes
    out as the app sends it (``tell_members``), and the mounted page hears it as
    an open page does (#201).
    """
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=bob_in_room)
    # bob's open lobby page, held so that it goes on hearing
    page = await mount(Lv4MessageList, user=bob_in_room, room=room, live_session=BOUNDARY)
    in_the_lobby = await _said(room, alice, "in the lobby")
    await _said(elsewhere, alice, "newer, elsewhere", after=in_the_lobby)

    await live.tell_members(room, alice, "message")

    assert not page.is_frozen
    assert await _cursor(room, bob_in_room) == (in_the_lobby.created_at, in_the_lobby.id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_messages_that_share_the_cursors_time_are_counted_by_id(room, alice, bob_in_room):
    """The cursor is (created_at, id), as the list's: by the time alone, the ones that share it are lost (``>``)
    or counted again (``>=``)."""
    messages = await sync_to_async(_seed)(room, alice, 6, same_time=True)
    cursor = messages[3]  # newest first: three come after it
    await Membership.objects.filter(room=room, user=bob_in_room).aupdate(read_at=cursor.created_at, read_id=cursor.id)

    assert await sync_to_async(unread_counts)(bob_in_room) == {room.id: 3}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_deleted_message_is_no_longer_unread(room, alice, bob_in_room):
    """The count is read from the room, so a delete is in it.

    One bob had not read takes one off; one he had does not.
    """
    messages = await sync_to_async(_seed)(room, alice, 4)
    cursor = messages[2]
    await Membership.objects.filter(room=room, user=bob_in_room).aupdate(read_at=cursor.created_at, read_id=cursor.id)
    (alices,) = await _lists(room, alice)
    assert await sync_to_async(unread_counts)(bob_in_room) == {room.id: 2}

    await alices.call("delete_message", message_id=messages[0].id)
    assert await sync_to_async(unread_counts)(bob_in_room) == {room.id: 1}

    # Older than the cursor, and the cursor itself: a place, not a row
    await alices.call("delete_message", message_id=messages[3].id)
    await alices.call("delete_message", message_id=cursor.id)
    assert await sync_to_async(unread_counts)(bob_in_room) == {room.id: 1}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_room_left_has_no_badge(room, alice, bob_in_room):
    await sync_to_async(_seed)(room, alice, 2)
    rooms = await mount(Lv4RoomList, user=bob_in_room, live_session=ROOMS)
    assert await _badges(rooms) == {"lobby": "2"}

    await rooms.call("leave_room", room_id=room.id)

    assert await sync_to_async(unread_counts)(bob_in_room) == {}
    assert await _badges(rooms) == {}
    assert "Join" in (await sync_to_async(rooms.render)() or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_badges_are_counted_in_one_query_however_many_rooms(alice, bob):
    """The list asks for every room's count at once: five rooms cost what one does."""

    async def rendering(rooms) -> int:
        async with capture_queries() as queries:
            await sync_to_async(rooms.render)()
        return len(queries)

    async def a_room_with_news(name: str) -> None:
        room = await Room.objects.acreate(name=name)
        for user in (alice, bob):
            await Membership.objects.acreate(room=room, user=user)
        await Message.objects.acreate(room=room, author=alice, content="news")

    await a_room_with_news("first")
    rooms = await mount(Lv4RoomList, user=bob, live_session=ROOMS)
    one = await rendering(rooms)
    for number in range(4):
        await a_room_with_news(f"room {number}")
    five = await rendering(rooms)

    assert await _badges(rooms) == {name: "1" for name in ("first", "room 0", "room 1", "room 2", "room 3")}
    assert one == five, (one, five)


@pytest.mark.integration
def test_the_count_reads_the_history_after_the_cursor_only(room, bob):
    """The count's plan, on SQLite: the room's messages are a range of the history index, from the cursor on.

    A cursor condition written as one OR (no cursor, or later, or the same
    time and a later id) is not a range: the plan reads every message of the
    room by room_id and filters them. Another database plans differently; what
    the query offers it, the cursor as a lower bound on the index, is the same.
    """
    if connection.vendor != "sqlite":
        pytest.skip("the plan is SQLite's")
    Membership.objects.create(room=room, user=bob)
    with CaptureQueriesContext(connection) as queries:
        unread_counts(bob)
    (query,) = queries.captured_queries
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + query["sql"])
        plan = [row[-1] for row in cursor.fetchall()]

    assert "SEARCH chat_lv4_message USING INDEX chat_lv4_history (room_id=? AND created_at>?)" in plan, plan


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_room_list_hears_its_users_topic_only(bob):
    from django.contrib.auth.models import AnonymousUser

    bobs = await mount(Lv4RoomList, user=bob, live_session=ROOMS)
    anonymous = await mount(Lv4RoomList, user=AnonymousUser(), live_session=ROOMS)

    assert bobs.component.get_subscriptions() == {user_topic(bob.pk)}
    assert anonymous.component.get_subscriptions() == set()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_open_room_page_reads_what_arrives_in_its_room_only(room, alice, bob_in_room, published):
    """bob has the lobby open over a real socket, and is a member of elsewhere too, which he has not open.

    alice writes in elsewhere, then in the lobby. The page reads the lobby's
    message -- bob's cursor moves, and his lists are told -- and not
    elsewhere's. "Not" is told by order, not by a quiet spell: both notices
    come to the page on bob's one topic, elsewhere's first, so once the page
    has told of reading the lobby it has passed elsewhere's by.
    """
    bob = bob_in_room
    elsewhere = await Room.objects.acreate(name="elsewhere")
    for user in (alice, bob):
        await Membership.objects.acreate(room=elsewhere, user=user)
    client = AsyncClient()
    await client.aforce_login(bob)
    states = _states((await client.get(_room_url(room))).content.decode())
    there = await mount(Lv4ChatRoom, id="there", user=alice, room=elsewhere, live_session=BOUNDARY)
    here = await mount(Lv4ChatRoom, id="here", user=alice, room=room, live_session=BOUNDARY)
    page = await _socket(client)
    try:
        for name, (component_id, state) in states.items():
            await _join(page, name, state)
            assert (await _answer(page, component_id))["command"] == "render", name
        published.clear()

        await there.call("send_message", content="in elsewhere")
        await here.call("send_message", content="in the lobby")
        # alice's two notices, then the page's "read"
        told = await eventually(lambda: _notices(published).count(user_topic(bob.pk)) == 3 and _notices(published))
    finally:
        await page.disconnect()

    assert told == [user_topic(bob.pk)] * 3
    in_the_lobby = await Message.objects.aget(room=room)
    assert await _cursor(room, bob) == (in_the_lobby.created_at, in_the_lobby.id)
    assert await _cursor(elsewhere, bob) == (None, None)
    assert await sync_to_async(unread_counts)(bob) == {elsewhere.id: 1}


#: A badge, as a render message's JSON spells it
_BADGE = json.dumps('<span class="unread"')[1:-1]


async def _open_room_list(client: AsyncClient) -> tuple[WebsocketCommunicator, str, dict]:
    """A room list joined over a real socket with ``client``'s cookies: the socket, the list's id, its first render."""
    list_id, state = _states((await client.get(reverse("chat_lv4:index"))).content.decode())["Lv4RoomList"]
    socket = await _socket(client)
    await _join(socket, "Lv4RoomList", state)
    first = await _answer(socket, list_id)
    assert first["command"] == "render"
    return socket, list_id, first


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("news", ["read", "deleted", "joined", "left"])
async def test_the_room_list_counts_again_on_each_notice_alone(room, alice, bob, news):
    """bob's room list, open over a real socket, hears one notice of one kind, and renders the new count.

    Nothing else reaches it: each case makes exactly the notice it names, from
    where the app makes it -- a page of the room joining (``read``), alice
    deleting a message bob has not read (``deleted``), bob's other tab joining
    or leaving the room (``joined``, ``left``; that tab is mounted, and its
    broadcast reaches the socket of the same process). The test waits for the list's
    render to the end, so a list that let one kind of notice pass would fail
    here by a timeout, and not by a quiet spell that ran out.
    """
    if news != "joined":
        await Membership.objects.acreate(room=room, user=bob)
    message = await Message.objects.acreate(room=room, author=alice, content="unread")
    client = AsyncClient()
    await client.aforce_login(bob)
    socket, list_id, first = await _open_room_list(client)
    other = None
    try:
        before = json.dumps(first)
        assert (_BADGE in before) == (news != "joined"), before
        if news == "read":
            # bob opens the lobby in another tab: its list's joined() moves the cursor and tells "read"
            msg_id, msg_state = _states((await client.get(_room_url(room))).content.decode())["Lv4MessageList"]
            other = await _socket(client)
            await _join(other, "Lv4MessageList", msg_state)
            assert (await _answer(other, msg_id))["command"] == "render"
        elif news == "deleted":
            (alices,) = await _lists(room, alice)
            await alices.call("delete_message", message_id=message.id)
        else:
            # bob's other tab of the room list, mounted: its notice reaches this socket as another tab's does (#201)
            tab = await mount(Lv4RoomList, user=bob, live_session=ROOMS)
            await tab.call("join_room" if news == "joined" else "leave_room", room_id=str(room.id))
        after = json.dumps(await _answer(socket, list_id))
    finally:
        if other is not None:
            await other.disconnect()
        await socket.disconnect()

    if news == "joined":
        # The link, the badge for alice's message, and the Leave button
        assert _BADGE in after and "Leave" in after, after
        assert await sync_to_async(unread_counts)(bob) == {room.id: 1}
    else:
        assert _BADGE not in after, after
        assert ("Join" if news == "left" else "Leave") in after, after
        assert await sync_to_async(unread_counts)(bob) == {}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_logout_closes_the_open_room_list(room, bob):
    """bob signs out in another tab. His room list tab, still open, is closed by the server.

    Its Join and Leave are events on the socket, and a socket keeps the user it
    connected with: open after the logout, it would go on joining and leaving
    rooms as bob. A form posted to a view would have been anonymous by then. The
    list's page is a live_session of its own, with no policy, for this: a
    logout reaches the connections inside a boundary, and closes them.
    """
    client = AsyncClient()
    await client.aforce_login(bob)
    socket, _, _ = await _open_room_list(client)
    try:
        await client.post(reverse("chat_lv4:sign_out"))
        output = await socket.receive_output(timeout=5)
        while output["type"] == "websocket.send":
            output = await socket.receive_output(timeout=5)
    finally:
        await socket.disconnect()

    assert output["type"] == "websocket.close", output


# In a browser: two people, two sets of cookies, one room


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


def _signed_in(browser, server: str, username: str):
    """A page of its own context, so its own cookies. ``context.new_page()`` is another tab of the same person."""
    page = browser.new_context().new_page()
    page.goto(f"{server}/chat-lv4/")
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
    wait_live(page, '[data-name=Lv4ChatRoom][data-is-live="true"]')
    wait_live(page, '[data-name=Lv4MessageList][data-is-live="true"]')
    wait_live(page, '[data-name=Lv4OnlineUsers][data-is-live="true"]')


def _say(page, text: str) -> None:
    page.locator("input[name=content]").fill(text)
    page.locator("input[name=content]").press("Enter")


@pytest.mark.e2e
def test_two_members_see_each_others_messages_and_presence(browser, server):
    alice = _signed_in(browser, server, "alice")
    alice.locator("input[name=name]").fill("tea")
    alice.get_by_role("button", name="Create").click()
    wait_live(alice, '[data-name=Lv4ChatRoom][data-is-live="true"]')
    room_url = alice.url

    bob = _signed_in(browser, server, "bob")
    # Not a member yet: the room sends him back to the list
    bob.goto(room_url)
    expect_count(bob.locator("[data-name=Lv4ChatRoom]"), 0)
    wait_live(bob, '[data-name=Lv4RoomList][data-is-live="true"]')
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


def _scroll_up(page) -> None:
    """Wheel up over the message list as far as it goes, as a person reading back would."""
    page.locator(".message-list").hover()
    page.mouse.wheel(0, -100_000)


@pytest.mark.e2e
def test_scrolling_up_loads_the_past_and_an_edit_or_delete_reaches_the_other_member(browser, server):
    """alice and bob in one room with more history than a page. Each scrolls on their own.

    alice edits while an older page loads into her list, and what she typed
    stays. Her edit and her delete reach bob's page. An edit of a message bob
    never loaded does not turn up in his list; when he scrolls up to it, it is
    the edited version, at the top.
    """
    users = get_user_model().objects
    alice_user, bob_user = users.create(username="alice"), users.create(username="bob")
    room = Room.objects.create(name="tea")
    for user in (alice_user, bob_user):
        Membership.objects.create(room=room, user=user)
    total = 2 * PAGE_SIZE + 5
    messages = _seed(room, alice_user, total)
    newest, oldest = messages[0], messages[-1]
    room_url = f"{server}{_room_url(room)}"

    alice = _signed_in(browser, server, "alice")
    _in_room(alice, room_url)
    bob = _signed_in(browser, server, "bob")
    _in_room(bob, room_url)

    def items(page):
        return page.locator("[wire-stream=messages] li.message")

    for page in (alice, bob):
        expect_count(items(page), PAGE_SIZE)
        expect(page.locator(".empty-message")).to_be_hidden()
    # The buttons are on the viewer's own messages: all of these are alice's
    expect_count(alice.get_by_role("button", name="Edit"), PAGE_SIZE)
    expect_count(bob.get_by_role("button", name="Edit"), 0)

    # alice edits her newest message. While she types, an older page loads into
    # her list and the list renders again: what she typed stays
    alice.locator(f"#messages-{newest.pk}").get_by_role("button", name="Edit").click()
    editor = alice.get_by_label("Edit your message")
    expect(editor).to_have_value(newest.content)
    editor.fill("tea, fixed")
    _scroll_up(alice)
    expect_count(items(alice), 2 * PAGE_SIZE)
    expect(editor).to_have_value("tea, fixed")
    editor.press("Enter")
    expect_count(alice.locator(".message-editor"), 0)

    for page in (alice, bob):
        expect_text(page.locator(f"#messages-{newest.pk} p"), "tea, fixed")
        expect_count(page.locator(f"#messages-{newest.pk} .edited"), 1)
    # Replaced where it stood: still the newest, first in the DOM and lowest on screen
    expect(items(bob).first).to_have_id(f"messages-{newest.pk}")

    # alice reads back to the room's first message; bob stays on the latest page
    _scroll_up(alice)
    expect_count(items(alice), total)
    expect(alice.locator(".history-start")).to_be_visible()
    alice.locator(f"#messages-{oldest.pk}").get_by_role("button", name="Edit").click()
    alice.get_by_label("Edit your message").fill("the first, fixed")
    alice.get_by_label("Edit your message").press("Enter")
    expect_text(alice.locator(f"#messages-{oldest.pk} p"), "the first, fixed")
    alice.locator(f"#messages-{newest.pk}").get_by_role("button", name="Delete").click()

    # The delete reached bob, after the edit before it: the edit of a message he
    # never loaded did not turn up in his list
    expect_count(items(bob), PAGE_SIZE - 1)
    expect_count(bob.locator(f"#messages-{newest.pk}"), 0)
    expect_count(bob.locator(f"#messages-{oldest.pk}"), 0)

    # bob reads back to it: the edited version, at the top of the room
    _scroll_up(bob)
    expect_count(items(bob), 2 * PAGE_SIZE - 1)
    _scroll_up(bob)
    expect_count(items(bob), total - 1)
    expect(bob.locator(".history-start")).to_be_visible()
    expect(items(bob).last).to_have_id(f"messages-{oldest.pk}")
    # Every page went in under the one before, in its order: the room, newest first
    shown = items(bob).evaluate_all("(els) => els.map((el) => el.id)")
    assert shown == [f"messages-{m.pk}" for m in messages if m != newest]
    expect_text(bob.locator(f"#messages-{oldest.pk} p"), "the first, fixed")
    expect_count(bob.locator(f"#messages-{oldest.pk} .edited"), 1)
    # Drawn bottom up: the last in the DOM is the highest on screen
    top = bob.locator(f"#messages-{oldest.pk}").bounding_box()
    below = bob.locator(f"#messages-{messages[-2].pk}").bounding_box()
    assert top is not None and below is not None and top["y"] < below["y"]


def _rooms(page, url: str) -> None:
    """The room list, live: its badges change from here."""
    open_live(page, url, '[data-name=Lv4RoomList][data-is-live="true"]')


def _badge(page, room_name: str):
    return page.locator(f'[data-room="{room_name}"] .unread')


@pytest.fixture
def two_members_of_tea(transactional_db):
    """alice and bob, both members of tea and of coffee, which have no messages yet."""
    users = get_user_model().objects
    alice, bob = users.create(username="alice"), users.create(username="bob")
    rooms = [Room.objects.create(name=name) for name in ("tea", "coffee")]
    for room in rooms:
        for user in (alice, bob):
            Membership.objects.create(room=room, user=user)
    return rooms


@pytest.mark.e2e
def test_a_badge_rises_for_the_member_not_looking_and_clears_when_they_open_the_room(
    browser, server, two_members_of_tea
):
    """bob is on the room list; alice writes in tea. Only bob's badge rises, and opening tea clears it."""
    tea, _ = two_members_of_tea
    alice = _signed_in(browser, server, "alice")
    _in_room(alice, f"{server}{_room_url(tea)}")
    bob = _signed_in(browser, server, "bob")
    _rooms(bob, f"{server}/chat-lv4/")
    expect_count(_badge(bob, "tea"), 0)

    _say(alice, "one")
    expect_text(_badge(bob, "tea"), "1")
    _say(alice, "two")
    expect_text(_badge(bob, "tea"), "2")
    expect_count(_badge(bob, "coffee"), 0)

    # bob opens tea: the page that shows the messages has read them
    bob.locator('[data-room="tea"] a').click()
    _joined_room(bob, f"{server}{_room_url(tea)}")
    expect_count(bob.locator("[wire-stream=messages] li.message"), 2)

    # Back on the list, nothing is unread
    bob.get_by_role("link", name="All Rooms").click()
    wait_live(bob, '[data-name=Lv4RoomList][data-is-live="true"]')
    expect_text(bob.locator('[data-room="tea"] a'), "tea")
    expect_count(_badge(bob, "tea"), 0)


class _Told:
    """The user topics the server publishes to, as they go: a test waits for one rather than sleeping."""

    def __init__(self):
        self.topics: list[str] = []
        self._changed = threading.Condition()

    def record(self, sender, topic, **info):
        if info.get("kind") == "notification" and topic.startswith("chat_lv4.user."):
            with self._changed:
                self.topics.append(topic)
                self._changed.notify_all()

    def wait_for(self, topic: str, count: int) -> None:
        with self._changed:
            assert self._changed.wait_for(lambda: self.topics.count(topic) >= count, timeout=10), self.topics


@pytest.fixture
def told():
    seen = _Told()
    telemetry.enable()
    telemetry.broadcast_published.connect(seen.record)
    try:
        yield seen
    finally:
        telemetry.broadcast_published.disconnect(seen.record)
        telemetry.disable()


@pytest.mark.e2e
def test_reading_in_one_tab_keeps_the_badge_at_zero_in_another(browser, server, two_members_of_tea, told):
    """bob has tea open in one tab and the room list in another. What alice writes in tea is read in the first.

    The list hears of alice's message and of bob's tab reading it, each on
    bob's topic. Which of the two it counts after first is a race, and the
    badge may show 1 for a moment. What this test holds is the browser's side
    and the end: the open tab reads what arrives, and the list's tea badge is 0
    after. It waits for the tab's read to be published, then alice writes in
    coffee; the render that shows coffee's badge counts tea from a database
    that has the read by then, so tea's is 0 in it. It does not show that the
    list counts again on the read notice itself -- by then any notice would do.
    test_the_room_list_counts_again_on_each_notice_alone does, over sockets.
    """
    tea, coffee = two_members_of_tea
    alice = _signed_in(browser, server, "alice")
    _in_room(alice, f"{server}{_room_url(tea)}")
    alice_in_coffee = alice.context.new_page()
    _in_room(alice_in_coffee, f"{server}{_room_url(coffee)}")
    bob_list = _signed_in(browser, server, "bob")
    _rooms(bob_list, f"{server}/chat-lv4/")
    # Another tab of the same browser: the same cookies, the same bob
    bob_in_tea = bob_list.context.new_page()
    _in_room(bob_in_tea, f"{server}{_room_url(tea)}")
    bobs = user_topic(get_user_model().objects.get(username="bob").pk)
    told.topics.clear()

    _say(alice, "hello")
    expect_count(bob_in_tea.locator("[wire-stream=messages] li", has_text="hello"), 1)
    # alice's notice and bob's tab reading it
    told.wait_for(bobs, 2)
    _say(alice_in_coffee, "and coffee")

    expect_text(_badge(bob_list, "coffee"), "1")
    assert _badge(bob_list, "tea").count() == 0
