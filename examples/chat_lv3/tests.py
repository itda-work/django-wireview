"""What this example is for: the server decides who is speaking, and who is in the room.

A message's author is the connection's user, whatever the browser sends. The
room page and every component on it are for the room's members: the view
refuses anyone else before it renders, and the boundary's hook refuses them on
every join -- a socket that never went through the view included.

And a stream is more than a list things are appended to: the list loads the
room's past a page at a time as the user scrolls up, and an author's edit or
delete reaches every page of the room -- the pages that show the message.
"""

import json
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import AsyncClient, Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect
from testproj.asgi import application
from testproj.e2e_browser import expect_count, expect_text, wait_live
from testproj.e2e_server import serve
from testproj.queries import capture_queries

from wireview import Broadcast, abroadcast, mount, telemetry

from . import live
from .live import PAGE_SIZE, Lv3ChatRoom, Lv3MessageList, Lv3OnlineUsers, membership_topic
from .models import Membership, Message, Room

# Handlers and the socket write from other threads, on connections of their own,
# so each test commits and the tables are flushed after it (#133).
pytestmark = pytest.mark.django_db(transaction=True)

BOUNDARY = "chat_lv3"


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
    return reverse("chat_lv3:room", args=[room.id])


# The room page: refused before it renders


@pytest.mark.integration
def test_an_anonymous_visitor_is_sent_to_sign_in_and_back(room):
    response = Client().get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("chat_lv3:index") + "?next=")
    assert _room_url(room) in response["Location"].replace("%2F", "/")


@pytest.mark.integration
def test_a_signed_in_user_who_is_not_a_member_is_sent_to_the_room_list(room, bob):
    client = Client()
    client.force_login(bob)

    response = client.get(_room_url(room))

    assert response.status_code == 302
    assert response["Location"] == reverse("chat_lv3:index")
    assert b"Lv3ChatRoom" not in response.content


@pytest.mark.integration
def test_a_member_gets_the_room(room, alice):
    client = Client()
    client.force_login(alice)

    response = client.get(_room_url(room))

    assert response.status_code == 200
    assert {"Lv3ChatRoom", "Lv3MessageList", "Lv3OnlineUsers"} <= set(_states(response.content.decode()))


@pytest.mark.integration
def test_joining_from_the_list_makes_a_member_and_leaving_ends_it(room, bob):
    client = Client()
    client.force_login(bob)

    assert client.post(reverse("chat_lv3:join_room", args=[room.id]))["Location"] == _room_url(room)
    assert client.get(_room_url(room)).status_code == 200

    client.post(reverse("chat_lv3:leave_room", args=[room.id]))
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

    client.post(reverse("chat_lv3:leave_room", args=[room.id]))

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

    response = client.post(reverse("chat_lv3:create_room"), {"name": "bob's"})

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
        list_id, list_state = states["Lv3MessageList"]
        await _join(communicator, "Lv3MessageList", list_state)
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
        speaker = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
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
            await bob_client.post(reverse("chat_lv3:leave_room", args=[room.id]))
        else:
            other = await mount(Lv3ChatRoom, id="other-tab", user=bob, room=room, live_session=BOUNDARY)
            await other.call("leave_room")
        told = [json.loads(text) for text in await _drain(kept)]
        assert {"command": "redirect", "url": reverse("chat_lv3:index")} in [
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
        chat_id = alice_states["Lv3ChatRoom"][0]
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
    room_id, room_state = _states(page.content.decode())["Lv3ChatRoom"]

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv3ChatRoom", room_state)
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
    for component in (Lv3ChatRoom, Lv3MessageList, Lv3OnlineUsers):
        assert not {"username", "author", "user_id"} & set(component.model_fields), component


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_author_is_the_user_and_presence_is_keyed_by_pk(room, alice):
    view = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)

    await view.call("send_message", content="  hi  ")

    message = await Message.objects.select_related("author").aget()
    assert (message.author, message.content) == (alice, "hi")
    assert view.component._presence_user_id() == str(alice.pk)
    assert view.component._presence_username() == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_leaving_the_open_page_writes_nothing(room, alice):
    """Events of a mounted component are not authorized again by the boundary; the handler asks."""
    view = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
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
    view = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    if not still_member:
        await Membership.objects.filter(room=room, user=alice).adelete()

    await view.call("on_typing")

    typing = [b["kwargs"]["state"] for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_typing"]
    assert typing == (["typing"] if still_member else [])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_leaving_from_the_room_ends_the_membership_and_the_page_hears_nothing_more(room, alice):
    view = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)

    await view.call("leave_room")

    assert not await Membership.objects.filter(room=room, user=alice).aexists()
    view.assert_redirected_to(reverse("chat_lv3:index"))
    assert view.component.get_subscriptions() == set()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("left", [True, False])
async def test_a_page_says_once_that_its_user_is_gone(room, alice, left):
    """A page that left the room said so then. When its socket closes later it does
    not say it again: by then the user may have joined again on another page."""
    view = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
    if left:
        await view.call("leave_room")

    await view.component.leaving()

    leaves = [b for b in view.presence_broadcasts if b["kwargs"]["action"] == "presence_leave"]
    assert len(leaves) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("component", [Lv3ChatRoom, Lv3MessageList, Lv3OnlineUsers])
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
    if component is Lv3ChatRoom:
        view.assert_redirected_to(reverse("chat_lv3:index"))
        # Gone from the others' online lists too, whatever the page does next
        assert view.presence_broadcasts[-1]["kwargs"]["action"] == "presence_leave"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_member_is_refused_when_mounted(room, bob):
    """The boundary's hook, as a unit test sees it: nothing of the room renders for bob."""
    view = await mount(Lv3MessageList, user=bob, room=room, live_session=BOUNDARY)

    assert not view.render()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_message_reaches_the_lists_of_its_room_only(room, alice):
    """send_message broadcasts the new message: every list of the room shows it, another room's does not."""
    elsewhere = await Room.objects.acreate(name="elsewhere")
    await Membership.objects.acreate(room=elsewhere, user=alice)
    lists = [
        await mount(Lv3MessageList, id="a", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv3MessageList, id="b", user=alice, room=room, live_session=BOUNDARY),
        await mount(Lv3MessageList, id="c", user=alice, room=elsewhere, live_session=BOUNDARY),
    ]
    chat = await mount(Lv3ChatRoom, id="room", user=alice, room=room, live_session=BOUNDARY)
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
    view = await mount(Lv3MessageList, user=alice, room=room, live_session=BOUNDARY)

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
    view = await mount(Lv3MessageList, user=alice, room=room, live_session=BOUNDARY)
    target = messages[PAGE_SIZE + 2]
    history = live._history

    async def changed_while_read(room, before):
        read = history(room, before)
        async for message in read:
            yield message
            if before is not None and message.pk == messages[PAGE_SIZE].pk:
                broadcast = Broadcast(Lv3MessageList, live.room_topic(room))
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

    view = await mount(Lv3MessageList, user=alice, room=room, live_session=BOUNDARY)

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
    view = await mount(Lv3MessageList, user=alice, room=room, live_session=BOUNDARY)
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
    list_id, state = _states((await client.get(_room_url(room))).content.decode())["Lv3MessageList"]
    tampered = state[:-3] + ("AAA" if not state.endswith("AAA") else "BBB")

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv3MessageList", tampered)
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
    view = await mount(Lv3MessageList, user=alice, room=room, live_session=BOUNDARY)
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
        view = await mount(Lv3MessageList, id=f"list-{number}", user=user, room=room, live_session=BOUNDARY)
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
    list_id, state = _states((await client.get(_room_url(room))).content.decode())["Lv3MessageList"]

    communicator = await _socket(client)
    try:
        await _join(communicator, "Lv3MessageList", state)
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
    other_id, other_state = _states((await bob_client.get(_room_url(elsewhere))).content.decode())["Lv3MessageList"]
    kept = await _socket(bob_client)
    try:
        for name, (component_id, state) in [*bob_states.items(), ("Lv3MessageList", (other_id, other_state))]:
            await _join(kept, name, state)
            assert (await _answer(kept, component_id))["command"] == "render", name
        await _drain(kept)
        (speaker,) = await _lists(room, alice)

        await speaker.call("edit_message", message_id=message.id)
        await speaker.call("save_edit", text="while bob is here")
        assert any("while bob is here" in text for text in await _drain(kept))

        await bob_client.post(reverse("chat_lv3:leave_room", args=[room.id]))
        assert any('"redirect"' in text for text in await _drain(kept))

        speaker.clear_messages()
        await speaker.call("edit_message", message_id=message.id)
        await speaker.call("save_edit", text="after bob left")
        await speaker.call("delete_message", message_id=message.id)
        assert [op["op"] for op in speaker.stream_ops("messages")] == ["insert", "delete"]
        await Broadcast(Lv3MessageList, live.room_topic(elsewhere)).push_event("marker").asend()

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
    view = await mount(Lv3MessageList, room=room, user=alice, live_session=BOUNDARY)

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
    page.goto(f"{server}/chat-lv3/")
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
    wait_live(page, '[data-name=Lv3ChatRoom][data-is-live="true"]')
    wait_live(page, '[data-name=Lv3MessageList][data-is-live="true"]')
    wait_live(page, '[data-name=Lv3OnlineUsers][data-is-live="true"]')


def _say(page, text: str) -> None:
    page.locator("input[name=content]").fill(text)
    page.locator("input[name=content]").press("Enter")


@pytest.mark.e2e
def test_two_members_see_each_others_messages_and_presence(browser, server):
    alice = _signed_in(browser, server, "alice")
    alice.locator("input[name=name]").fill("tea")
    alice.get_by_role("button", name="Create").click()
    wait_live(alice, '[data-name=Lv3ChatRoom][data-is-live="true"]')
    room_url = alice.url

    bob = _signed_in(browser, server, "bob")
    # Not a member yet: the room sends him back to the list
    bob.goto(room_url)
    expect_count(bob.locator("[data-name=Lv3ChatRoom]"), 0)
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
