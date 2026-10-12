"""Two real connections on the in-memory channel layer (#110).

What one connection does has to reach another: a broadcast, a presence change, a
model saved anywhere. The only end-to-end broadcast test needed a nats-server, so
CI never ran it, and every presence test called ``notification()`` by hand
instead of letting the layer deliver it. These go through ``WireviewConsumer``
over ``WebsocketCommunicator``, the layer included -- the default ``memory`` one,
so they run wherever the suite does.
"""

from __future__ import annotations

import json
import typing as t

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component, PresenceConfig, PresenceMixin, PresenceTrackerMixin, broadcast
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.state import sign_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

CALLS: list[tuple[str, str]] = []


class InlineTemplate:
    """Components here carry their template; no loader, no files."""

    source: t.ClassVar[str]
    _compiled: t.ClassVar[Template | None] = None

    @classmethod
    def _get_template(cls, template_name=None):
        if cls.__dict__.get("_compiled") is None:
            cls._compiled = Template("{% load wireview %}" + cls.source)
        return cls._compiled


class CnBoard(InlineTemplate, Component):
    source: t.ClassVar[str] = "<div {% tag_header %}><b>{{ this.count }}</b></div>"

    class Meta:
        subscriptions = {"cn-topic"}

    count: int = 0

    async def shout(self, n: int):
        await self.broadcast("cn-topic", count=n)

    async def notification(self, channel: str, **kwargs):
        self.count = kwargs["count"]

    async def leaving(self):
        CALLS.append(("leaving", self.id))


class CnTyping(InlineTemplate, PresenceMixin, Component):
    source: t.ClassVar[str] = "<div {% tag_header %}>{{ this.name }}</div>"

    name: str = ""

    def _presence_topic(self) -> str:
        return "cn-room"

    def _presence_user_id(self) -> str:
        return self.name

    def _presence_username(self) -> str:
        return self.name

    async def joined(self):
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def typing(self):
        await self.presence_set_typing(True)


class CnCountedTyping(CnTyping):
    """A producer whose every render differs: a render of it is a frame on the socket."""

    source: t.ClassVar[str] = "<div {% tag_header %}>{{ this.name }} {{ this.renders }}</div>"

    @property
    def renders(self) -> int:
        self.__dict__["_renders"] = self.__dict__.get("_renders", 0) + 1
        return self.__dict__["_renders"]


class CnOnline(InlineTemplate, PresenceTrackerMixin, Component):
    source: t.ClassVar[str] = (
        "<ul {% tag_header %}>{% for user in this.presence_users %}"
        # One dynamic per user, so a diff carries "ann-typing" in one piece
        '<li>{{ user.username|add:"-"|add:user.state.value }}</li>{% endfor %}'
        '{% with n=this.presence_online_count|stringformat:"s" %}<b>{{ "online-"|add:n }}</b>{% endwith %}</ul>'
    )

    name: str = ""

    def _presence_topic(self) -> str:
        return "cn-room"

    def _presence_my_user_id(self) -> str:
        return self.name

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.name)


class CnFragileOnline(CnOnline):
    """A list of the room whose handler raises: the session drops it, and the page joins it again."""

    async def boom(self):
        raise RuntimeError("boom")


class CnTab(InlineTemplate, PresenceMixin, PresenceTrackerMixin, Component):
    """A tab of the room: it types and lists who is here, on one connection (#202)."""

    source: t.ClassVar[str] = "<div {% tag_header %}><i>{{ this.roster }}</i></div>"

    class Meta:
        # Longer than any test here: only stop() clears typing
        presence = PresenceConfig(typing_timeout=600)

    name: str = ""

    def _presence_topic(self) -> str:
        return "cn-tabs"

    def _presence_user_id(self) -> str:
        return self.name

    def _presence_my_user_id(self) -> str:
        return self.name

    def _presence_username(self) -> str:
        return self.name

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}

    @property
    def roster(self) -> str:
        """Every listed user and their state, in one dynamic, so a render says the whole list."""
        return "[" + " ".join(sorted(f"{u.username}:{u.state.value}" for u in self.presence_users)) + "]"

    async def joined(self):
        await self.presence_track_self(username=self.name)
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def typing(self):
        await self.presence_set_typing(True)

    async def stop(self):
        await self.presence_set_typing(False)


class CnViewer(InlineTemplate, PresenceTrackerMixin, Component):
    """A page of the room that only lists who is here: nothing on it announces the connection."""

    source: t.ClassVar[str] = CnTab.source

    name: str = ""

    roster = CnTab.roster

    def _presence_topic(self) -> str:
        return "cn-tabs"

    def _presence_my_user_id(self) -> str:
        return self.name

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.name)


class CnChat(InlineTemplate, PresenceMixin, Component):
    """A chat input that hears its room's messages, with the README's receiver: no super(), ``kwargs["message"]``."""

    source: t.ClassVar[str] = "<div {% tag_header %}>{{ this.last }}</div>"

    class Meta:
        subscriptions = {"cn-chat"}

    name: str = ""
    last: str = ""

    _presence_topic = CnTyping._presence_topic
    _presence_user_id = CnTyping._presence_user_id
    _presence_username = CnTyping._presence_username
    joined = CnTyping.joined
    leaving = CnTyping.leaving

    async def notification(self, channel: str, **kwargs):
        self.last = kwargs["message"]


class CnQuietChat(CnChat):
    """The same, reading with ``.get()``: it never passes a presence message on."""

    async def notification(self, channel: str, **kwargs):
        self.last = kwargs.get("message", "")


class CnCounter(InlineTemplate, PresenceMixin, Component):
    """A producer that hears its presence channel itself, as before #202, and counts who came."""

    source: t.ClassVar[str] = "<div {% tag_header %}><b>{{ this.came }}</b></div>"

    name: str = ""
    joins: int = 0

    _presence_topic = CnTyping._presence_topic
    _presence_user_id = CnTyping._presence_user_id
    _presence_username = CnTyping._presence_username
    joined = CnTyping.joined
    leaving = CnTyping.leaving

    @property
    def came(self) -> str:
        return f"came-{self.joins}"

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}

    async def notification(self, channel: str, **kwargs):
        await super().notification(channel, **kwargs)
        if kwargs.get("action") == "presence_join" and kwargs.get("user_id") != self.name:
            self.joins += 1


class CnLateRoom(InlineTemplate, PresenceMixin, Component):
    """A producer whose topic is known once joined() has loaded its room."""

    source: t.ClassVar[str] = CnTyping.source

    name: str = ""
    room: str | None = None

    def _presence_topic(self) -> str:
        assert self.room is not None, "the topic was read before joined() loaded the room"
        return self.room

    _presence_user_id = CnTyping._presence_user_id
    _presence_username = CnTyping._presence_username
    leaving = CnTyping.leaving

    async def joined(self):
        self.room = "cn-room"
        await self.presence_join()


class CnProducerBase(PresenceMixin, Component):
    """A reusable producer base: presence only, no subscriptions of its own."""

    name: str = ""

    _presence_topic = CnTyping._presence_topic
    _presence_user_id = CnTyping._presence_user_id
    _presence_username = CnTyping._presence_username
    joined = CnTyping.joined
    leaving = CnTyping.leaving


class CnRoomBase(Component):
    """An app base that hears its room, cooperatively."""

    last: str = ""

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {"cn-chat"}

    async def notification(self, channel: str, **kwargs):
        if channel == "cn-chat":
            self.last = kwargs.get("message", "")
        await super().notification(channel, **kwargs)


class CnDiamond(InlineTemplate, CnProducerBase, CnRoomBase):
    """The two combined: the MRO puts CnRoomBase's methods before Component's."""

    source: t.ClassVar[str] = '<div {% tag_header %}>{{ "last-"|add:this.last }}</div>'


class CnHideable(CnTyping):
    """A second producer of a page that can go quiet, as a widget hidden by the page."""

    async def hide(self):
        await self.presence_leave()


class CnNamedPage(InlineTemplate, Component):
    """A page that draws its producer, under the id a producer needs (#212)."""

    source: t.ClassVar[str] = (
        '<section {% tag_header %}>{% component "CnTyping" id="a-named-typing" name=this.name %}</section>'
    )

    name: str = ""


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()


async def open_socket(tab: str = "") -> WebsocketCommunicator:
    """A socket, as a page opens it; ``tab`` names the page load, as the bundle does (#202)."""
    path = "/__wireview__?vsn=99" + (f"&tab={tab}" if tab else "")
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), path)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def join(communicator: WebsocketCommunicator, component: type[Component], id: str, **state) -> None:
    signed = sign_state(component(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **state))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": component.__name__, "state": signed, "children": {}}}
    )


async def connect(component: type[Component], id: str, *, tab: str = "", **state) -> WebsocketCommunicator:
    communicator = await open_socket(tab)
    await join(communicator, component, id, **state)
    await render_containing(communicator, "")
    return communicator


async def render_containing(communicator: WebsocketCommunicator, text: str) -> str:
    """The first render whose payload contains ``text``."""
    for _ in range(10):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] != "render":
            continue
        rendered = json.dumps(message["payload"])
        if text in rendered:
            return rendered
    raise AssertionError(f"no render with {text!r}")


async def event(communicator: WebsocketCommunicator, id: str, command: str, **args) -> None:
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {"id": id, "command": command, "implicit_args": {}, "explicit_args": args},
        }
    )


async def test_a_broadcast_from_one_connection_rerenders_another():
    listener = await connect(CnBoard, "b1")
    speaker = await connect(CnBoard, "b2")
    try:
        await event(speaker, "b2", "shout", n=5)
        assert "5" in await render_containing(listener, '"5"')
    finally:
        await listener.disconnect()
        await speaker.disconnect()


async def test_the_module_broadcast_reaches_a_connection_from_sync_code():
    # wireview.broadcast() is the synchronous one, for views and signal handlers
    listener = await connect(CnBoard, "b1")
    try:
        await sync_to_async(broadcast)("cn-topic", count=9)
        await render_containing(listener, '"9"')
    finally:
        await listener.disconnect()


async def test_presence_join_typing_and_leave_reach_the_tracker():
    tracker = await connect(CnOnline, "online", name="bob")
    ann = await connect(CnTyping, "typing", name="ann")
    try:
        await render_containing(tracker, "ann-online")
        await event(ann, "typing", "typing")
        await render_containing(tracker, "ann-typing")
        await ann.disconnect()
        await render_containing(tracker, '"online-1"')
    finally:
        await tracker.disconnect()


async def test_a_user_stays_listed_until_their_last_connection_leaves():
    """Closing one of ann's two tabs leaves her listed; closing the other removes her (#202)."""
    bob = await connect(CnTab, "bob", name="bob")
    ann_tabs = [await connect(CnTab, "ann1", name="ann"), await connect(CnTab, "ann2", name="ann")]
    carol = None
    try:
        await render_containing(bob, "[ann:online bob:online]")
        await ann_tabs[0].disconnect()
        # carol's join renders bob's list again, after ann1's leave
        carol = await connect(CnTab, "carol", name="carol")
        await render_containing(bob, "[ann:online bob:online carol:online]")
        await ann_tabs[1].disconnect()
        await render_containing(bob, "[bob:online carol:online]")
    finally:
        for communicator in (bob, *ann_tabs, *([carol] if carol else [])):
            await communicator.disconnect()


async def test_a_user_types_while_any_of_their_connections_types():
    """One tab typing makes the user typing; another tab's stop does not clear it (#202)."""
    bob = await connect(CnTab, "bob", name="bob")
    ann_tabs = [await connect(CnTab, "ann1", name="ann"), await connect(CnTab, "ann2", name="ann")]
    carol = None
    try:
        await render_containing(bob, "[ann:online bob:online]")
        await event(ann_tabs[0], "ann1", "typing")
        await render_containing(bob, "[ann:typing bob:online]")
        await event(ann_tabs[1], "ann2", "typing")
        await event(ann_tabs[1], "ann2", "stop")
        carol = await connect(CnTab, "carol", name="carol")
        await render_containing(bob, "[ann:typing bob:online carol:online]")
        await event(ann_tabs[0], "ann1", "stop")
        await render_containing(bob, "[ann:online bob:online carol:online]")
    finally:
        for communicator in (bob, *ann_tabs, *([carol] if carol else [])):
            await communicator.disconnect()


async def test_a_late_observer_hears_every_connection_of_a_user():
    """The sync answers are per connection: one of ann's tabs leaving keeps her on a later list (#202)."""
    ann_tabs = [await connect(CnTab, "ann1", name="ann"), await connect(CnTab, "ann2", name="ann")]
    dave = None
    try:
        await event(ann_tabs[1], "ann2", "typing")
        await render_containing(ann_tabs[0], "[ann:typing]")
        dave = await connect(CnTab, "dave", name="dave")
        await render_containing(dave, "[ann:typing dave:online]")
        await ann_tabs[1].disconnect()
        await render_containing(dave, "[ann:online dave:online]")
        await ann_tabs[0].disconnect()
        await render_containing(dave, "[dave:online]")
    finally:
        for communicator in (*ann_tabs, *([dave] if dave else [])):
            await communicator.disconnect()


async def test_a_connection_that_only_tracks_is_not_listed_by_others():
    """A tracker alone never announces its connection, nor its leave: it does not answer a sync either (#202).

    Answering put it on the lists of those who came later, and nothing took it off.
    """
    bob = await connect(CnViewer, "viewer", name="bob")
    ann = await connect(CnTab, "ann", name="ann")
    carol = None
    try:
        await render_containing(ann, "[ann:online]")
        await bob.disconnect()
        carol = await connect(CnTab, "carol", name="carol")
        await render_containing(ann, "[ann:online carol:online]")
        await render_containing(carol, "[ann:online carol:online]")
    finally:
        for communicator in (bob, ann, *([carol] if carol else [])):
            await communicator.disconnect()


@pytest.mark.parametrize("producer_first", [True, False], ids=["producer-first", "tracker-first"])
async def test_a_page_whose_producer_and_tracker_are_apart_is_listed_by_a_later_observer(producer_first):
    """The producer answers the sync, whichever of the page's roots joined first (#202).

    A tracker that answered for its page once its producer's join came back
    never heard that join when the producer was a root joined before it.
    """
    ann = await open_socket()
    bob = None
    try:
        roots = [(CnTyping, "a-typing"), (CnOnline, "a-list")]
        for component, id in roots if producer_first else roots[::-1]:
            await join(ann, component, id, name="ann")
        await render_containing(ann, "ann-online")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_tracker_joined_again_after_it_raised_hears_who_is_here_and_its_page_stays_listed():
    """A tracker made after the page's producer joined: the producer still answers for the page (#202)."""
    carol = await connect(CnTyping, "c-typing", name="carol")
    ann = await open_socket()
    bob = None
    try:
        await join(ann, CnTyping, "a-typing", name="ann")
        await join(ann, CnFragileOnline, "a-list", name="ann")
        await render_containing(ann, "carol-online")
        await event(ann, "a-list", "boom")
        error = await ann.receive_json_from(timeout=5)
        while error["command"] != "error":
            error = await ann.receive_json_from(timeout=5)
        assert error["payload"]["id"] == "a-list"
        # The page joins it again, with the state its element carries
        await join(ann, CnFragileOnline, "a-list", name="ann")
        await render_containing(ann, "carol-online")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (carol, ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_sticky_producer_answers_for_the_page_it_moved_to():
    """A boosted navigation keeps the producer and joins the next page's tracker (#202)."""
    carol = await connect(CnTyping, "c-typing", name="carol")
    ann = await open_socket()
    bob = None
    try:
        await join(ann, CnTyping, "a-typing", name="ann")
        await join(ann, CnOnline, "a-list-1", name="ann")
        await render_containing(ann, "carol-online")
        # The navigation: the old page's list leaves, the producer is carried, the next page's list joins
        await ann.send_json_to({"command": "leave", "payload": {"id": "a-list-1"}})
        await ann.send_json_to(
            {"command": "navigated", "payload": {"params": {}, "uri": "/next/", "carried": ["a-typing"]}}
        )
        await join(ann, CnOnline, "a-list-2", name="ann")
        await render_containing(ann, "carol-online")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (carol, ann, *([bob] if bob else [])):
            await communicator.disconnect()


ANN_TAB = "ann-first-page-load"


async def test_a_tab_that_reconnects_replaces_what_a_lost_leave_left(monkeypatch):
    """The socket's process died without the leave; the tab came back on a new socket, then closed (#202).

    The page names its load in the socket URL and keeps it across reconnects,
    so the second join replaces the first one's meta and its leave removes it.
    """
    bob = await connect(CnOnline, "b-list", name="bob")
    try:
        lost = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
        await render_containing(bob, "ann-online")

        async def dies(self):
            pass  # the process ends: leaving() never runs

        monkeypatch.setattr(CnTyping, "leaving", dies)
        await lost.disconnect()
        monkeypatch.undo()

        back = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
        await render_containing(bob, "ann-online")
        await back.disconnect()
        await render_containing(bob, '"online-1"')
    finally:
        await bob.disconnect()


async def join_page(communicator: WebsocketCommunicator, page: type[Component], children=None) -> str:
    """Join ``page`` as its element carries it, then the producer it drew, as the bundle does; its id."""
    signed = sign_state(page(user=AnonymousUser(), wire=WireviewMeta(params={}), id="a-page", name="ann"))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": page.__name__, "state": signed, "children": children or {}}}
    )
    while True:
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "render" and message["payload"]["id"] == "a-page":
            break
    diff = message["payload"]["diff"]
    statics = "".join(diff["s"])
    producer_id = statics.split('" data-name="CnTyping"')[0].rsplit('id="', 1)[1]
    at = next(i for i, part in enumerate(diff["s"]) if part.endswith('data-name="CnTyping" data-state="'))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": "CnTyping", "state": diff["d"][at], "children": {}}}
    )
    return producer_id


async def test_a_tab_that_reconnects_replaces_what_its_drawn_producer_left(monkeypatch):
    """The producer is drawn by the page's component; its process died without the leave (#202).

    The rejoin after the reconnect draws it under the same id, so its join
    replaces the meta the lost leave left, and the tab's close takes it off.
    """
    page = CnNamedPage
    bob = await connect(CnOnline, "b-list", name="bob")
    try:
        lost = await open_socket(ANN_TAB)
        first = await join_page(lost, page)
        await render_containing(bob, "ann-online")

        async def dies(self):
            pass  # the process ends: leaving() never runs

        monkeypatch.setattr(CnTyping, "leaving", dies)
        await lost.disconnect()
        monkeypatch.undo()

        back = await open_socket(ANN_TAB)
        again = await join_page(back, page)
        assert again == first
        await render_containing(bob, "ann-online")
        await back.disconnect()
        await render_containing(bob, '"online-1"')
    finally:
        await bob.disconnect()


class CnToggle(InlineTemplate, Component):
    """A page that hides and shows its producer."""

    source: t.ClassVar[str] = (
        '<section {% tag_header %}><i>{{ "n-"|add:this.n }}</i>{% if this.show %}'
        '{% component "CnTyping" id="a-toggled" name=this.name %}{% endif %}</section>'
    )

    name: str = ""
    show: bool = True
    n: str = "0"

    async def toggle(self):
        self.show = not self.show

    async def bump(self):
        self.n = str(int(self.n) + 1)


def drawn_producer_state(node: t.Any) -> str | None:
    """The signed state of the CnTyping a render drew, wherever its part sits in the diff."""
    if isinstance(node, dict):
        statics = node.get("s") or node.get("r")
        if isinstance(statics, list):
            for i, part in enumerate(statics):
                if isinstance(part, str) and part.endswith('data-name="CnTyping" data-state="'):
                    return node["d"][i]
        node = list(node.values())
    if isinstance(node, list):
        for value in node:
            if (state := drawn_producer_state(value)) is not None:
                return state
    return None


async def page_render(communicator: WebsocketCommunicator) -> dict:
    while True:
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "render" and message["payload"]["id"] == "a-page":
            return message["payload"]


async def test_a_hidden_producers_late_leave_keeps_the_one_shown_in_its_place(monkeypatch):
    """Hidden, then shown again before the hidden one's leaving() ran (#202, #204).

    The hidden producer retires with the render that stops drawing it and
    leaves when its tombstone is buried: here the page held its element, so
    when its time is up. Under its id, it shares its meta with the one shown
    in its place; its leave, said after that one's join, took the user off
    bob's list while the page showed them joined.
    """
    from wireview import repository

    monkeypatch.setattr(repository, "TOMBSTONE_SECONDS", 3600.0)
    bob = await connect(CnOnline, "b-list", name="bob")
    ann = await open_socket(ANN_TAB)
    carol = None
    try:
        page = CnToggle(user=AnonymousUser(), wire=WireviewMeta(params={}), id="a-page", name="ann")
        await ann.send_json_to(
            {"command": "join", "payload": {"name": "CnToggle", "state": sign_state(page), "children": {}}}
        )
        drawn = drawn_producer_state((await page_render(ann))["diff"])
        await ann.send_json_to({"command": "join", "payload": {"name": "CnTyping", "state": drawn, "children": {}}})
        await render_containing(bob, "ann-online")

        await event(ann, "a-page", "toggle")  # hidden; the page holds its element and sends no leave
        await page_render(ann)
        await event(ann, "a-page", "toggle")  # shown again, as a new producer
        drawn = drawn_producer_state((await page_render(ann))["diff"])
        await ann.send_json_to({"command": "join", "payload": {"name": "CnTyping", "state": drawn, "children": {}}})
        await render_containing(bob, "ann-online")

        # The hidden one's time is up: the next render of the page buries it
        monkeypatch.setattr(repository, "TOMBSTONE_SECONDS", 0.0)
        await event(ann, "a-page", "bump")
        await page_render(ann)
        carol = await connect(CnTyping, "c-typing", name="carol")
        # bob, ann and carol: had the hidden one's leave removed ann, two
        assert '"online-3"' in await render_containing(bob, "carol-online")
    finally:
        for communicator in (bob, ann, carol):
            if communicator is not None:
                await communicator.disconnect()


async def test_a_late_leave_from_the_socket_a_tab_left_keeps_it_listed():
    """The tab reconnected while the server still held its old socket; that one's leave comes after (#202)."""
    bob = await connect(CnOnline, "b-list", name="bob")
    old = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
    new = None
    try:
        await render_containing(bob, "ann-online")
        new = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
        await old.disconnect()
        # A render after the old socket's leave: carol arrives
        carol = await connect(CnTyping, "c-typing", name="carol")
        # bob, ann and carol: had the old socket's leave removed ann, two
        assert '"online-3"' in await render_containing(bob, "carol-online")
        await carol.disconnect()
        await new.disconnect()
        await render_containing(bob, '"online-1"')
    finally:
        for communicator in (bob, old, *([new] if new else [])):
            await communicator.disconnect()


async def test_two_sockets_of_one_tab_answer_a_new_observer_and_the_old_ones_leave_keeps_it_listed():
    """The tab reconnected before the server noticed its old socket, then someone came (#202).

    Both sockets answer the newcomer's sync. Keeping the first answer's socket
    alone, the old socket's leave then removed a tab still open on the new one.
    """
    old = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
    new = await connect(CnTyping, "a-typing", tab=ANN_TAB, name="ann")
    bob = carol = None
    try:
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
        await old.disconnect()
        # A render after the old socket's leave: carol arrives
        carol = await connect(CnTyping, "c-typing", name="carol")
        assert '"online-3"' in await render_containing(bob, "carol-online")
        await new.disconnect()
        await render_containing(bob, '"online-2"')
    finally:
        for communicator in (old, new, *(c for c in (bob, carol) if c)):
            await communicator.disconnect()


async def drain(communicator: WebsocketCommunicator, quiet: float = 0.5) -> list[dict]:
    """Every message until the socket has been quiet for ``quiet`` seconds."""
    messages = []
    while not await communicator.receive_nothing(timeout=quiet):
        messages.append(await communicator.receive_json_from())
    return messages


async def test_a_producers_receiver_does_not_hear_the_presence_channel_only_the_framework_hears():
    """A producer listens on its presence channel to answer syncs; its notification() does not hear it (#202).

    The README's receiver reads ``kwargs["message"]``: handed ann's own join
    and bob's, it raised, and the page joined it again into the same join.
    """
    ann = await open_socket()
    bob = None
    try:
        for _ in range(2):
            # The page joins it again as it would after an error
            await join(ann, CnChat, "a-chat", name="ann")
            assert [m["command"] for m in await drain(ann)].count("error") == 0
        bob = await connect(CnChat, "b-chat", name="bob")
        assert [m["command"] for m in await drain(ann)].count("error") == 0
        await sync_to_async(broadcast)("cn-chat", message="hello")
        await render_containing(ann, "hello")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_producer_whose_receiver_passes_nothing_on_still_answers_for_its_page():
    """No super() in its notification(), no error either: its page is still on a later list (#202)."""
    ann = await connect(CnQuietChat, "a-chat", name="ann")
    bob = None
    try:
        await join(ann, CnOnline, "a-list", name="ann")
        await render_containing(ann, "ann-online")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_producer_that_hears_its_presence_channel_itself_renders_what_it_did_with_it():
    """Subscribed by the component, the channel is the component's: its receiver hears it and renders (#202)."""
    ann = await connect(CnCounter, "a-count", name="ann")
    bob = None
    try:
        bob = await connect(CnTyping, "b-typing", name="bob")
        await render_containing(ann, "came-1")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_producer_whose_topic_joined_decides_joins_and_answers():
    """The channel is added once it has joined: before joined() its topic may not be known (#202)."""
    ann = await connect(CnLateRoom, "a-late", name="ann")
    bob = None
    try:
        assert [m["command"] for m in await drain(ann)].count("error") == 0
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_producer_renders_nothing_for_what_only_the_framework_hears():
    """Joins, leaves and syncs on the channel the framework listens on reach no app code and no render (#202).

    ann's producer renders something new every time, so a render would be a frame.
    """
    ann = await connect(CnCountedTyping, "a-typing", name="ann")
    bob = carol = None
    try:
        await drain(ann)
        bob = await connect(CnTyping, "b-typing", name="bob")
        carol = await connect(CnOnline, "c-list", name="carol")
        await render_containing(carol, "ann-online")
        await event(bob, "b-typing", "typing")
        await bob.disconnect()
        bob = None
        assert [m["command"] for m in await drain(ann)] == []
    finally:
        for communicator in (ann, *(c for c in (bob, carol) if c)):
            await communicator.disconnect()


async def test_a_producer_mixed_with_an_app_base_keeps_the_app_bases_room():
    """The mixin does not touch get_subscriptions() or notification(): the app base's are the class's (#202)."""
    ann = await connect(CnDiamond, "a-diamond", name="ann")
    bob = None
    try:
        await sync_to_async(broadcast)("cn-chat", message="hello")
        await render_containing(ann, "last-hello")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_receiver_patched_onto_the_class_does_not_hear_the_frameworks_channel(monkeypatch):
    """What the framework hears is the session's to route, whatever the class's methods are (#202)."""

    async def notification(self, channel: str, **kwargs):
        self.last = kwargs["message"]  # the README's shape

    monkeypatch.setattr(CnChat, "notification", notification)
    ann = await connect(CnChat, "a-chat", name="ann")
    bob = None
    try:
        bob = await connect(CnChat, "b-chat", name="bob")
        assert [m["command"] for m in await drain(ann)].count("error") == 0
        await sync_to_async(broadcast)("cn-chat", message="hello")
        await render_containing(ann, "hello")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_a_producer_whose_subscriptions_are_patched_still_answers(monkeypatch):
    monkeypatch.setattr(CnChat, "get_subscriptions", lambda self: {"cn-chat"})
    ann = await connect(CnChat, "a-chat", name="ann")
    bob = None
    try:
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "ann-online")
    finally:
        for communicator in (ann, *([bob] if bob else [])):
            await communicator.disconnect()


async def test_two_producers_of_one_tab_one_leaves_the_user_stays():
    """A meta per producer, as Phoenix keeps one per track: a page hiding one of two keeps its user (E1, #202)."""
    ann = await open_socket("e" * 20)
    bob = carol = None
    try:
        await join(ann, CnTyping, "a-input", name="ann")
        await render_containing(ann, "a-input")
        await join(ann, CnHideable, "a-widget", name="ann")
        await render_containing(ann, "a-widget")
        bob = await connect(CnOnline, "b-list", name="bob")
        await render_containing(bob, "online-2")

        await event(ann, "a-widget", "hide")
        assert not [m for m in await drain(bob) if "online-1" in json.dumps(m)]
        # And one who comes later sees the same list: carol and ann
        carol = await connect(CnOnline, "c-list", name="carol")
        assert "ann-online" in await render_containing(carol, "online-2")

        # The page goes: its other producer's leave takes the user off
        await ann.disconnect()
        ann = None
        await render_containing(bob, "online-1")
        await render_containing(carol, "online-1")
    finally:
        for communicator in (c for c in (ann, bob, carol) if c):
            await communicator.disconnect()


async def test_a_leaving_that_raises_still_takes_the_user_off(monkeypatch):
    """The producer's leaving() raised before presence_leave(): the framework leaves for it (#212).

    Phoenix's tracker drops a process's metas when the process ends, whatever
    the process did; the end of a component is its last leaving().
    """
    bob = await connect(CnOnline, "b-list", name="bob")
    ann = carol = None
    try:
        ann = await connect(CnTyping, "a-input", name="ann")
        await render_containing(bob, "ann-online")

        async def broken(self):
            raise RuntimeError("leaving() failed")

        monkeypatch.setattr(CnTyping, "leaving", broken)
        await ann.disconnect()
        ann = None
        monkeypatch.undo()
        carol = await connect(CnTyping, "c-input", name="carol")
        final = await render_containing(bob, "carol-online")
        assert "ann-" not in final and '"online-2"' in final, final
    finally:
        for communicator in (ann, carol, bob):
            if communicator is not None:
                await communicator.disconnect()


async def test_closing_the_socket_calls_leaving():
    communicator = await connect(CnBoard, "b1")

    await communicator.disconnect()

    assert CALLS == [("leaving", "b1")]
