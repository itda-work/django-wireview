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


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()


async def connect(component: type[Component], id: str, **state) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), "/__wireview__?vsn=99")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    signed = sign_state(component(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **state))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": component.__name__, "state": signed, "children": {}}}
    )
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


async def test_closing_the_socket_calls_leaving():
    communicator = await connect(CnBoard, "b1")

    await communicator.disconnect()

    assert CALLS == [("leaving", "b1")]
