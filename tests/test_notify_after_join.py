"""A component hears its channels from its ``joined()`` on, not before (#207).

A component its drawer's pass builds anew (``{% component %}``) is in the
connection's repository from that render on, and the page's join for it comes a
round trip later. A notification on a channel it subscribes to that arrives in
between ran its ``notification()`` -- and rendered it -- before ``joined()``,
breaking the lifecycle's promise: ``joined()`` first. Nor may the notification be
lost: it changes what the component shows, so it is handed over once the
component joined. These go through ``WireviewConsumer`` on the in-memory layer.
"""

from __future__ import annotations

import asyncio
import json
import threading
import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.waiting import eventually

from wireview import Component, LiveComponent, broadcast
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.state import sign_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

ROOM = "naj-room"
CALLS: list[tuple[t.Any, ...]] = []


class NajLeaf(Component):
    """Hears the room; what it heard last is on the page."""

    class Meta:
        subscriptions = {ROOM}

    heard: int = 0

    async def joined(self):
        CALLS.append(("joined", self.id))

    async def notification(self, channel: str, **kwargs):
        CALLS.append(("notified", self.id, kwargs["n"]))
        if kwargs["n"] == 13:
            raise RuntimeError("unlucky")
        self.heard = kwargs["n"]

    async def params_changed(self, params, uri):
        CALLS.append(("params", self.id, params.get("q")))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% tag_header %}>heard {{ this.heard }}</i>")


class NajMover(NajLeaf):
    """Leaves the room in joined()."""

    moved: bool = False

    async def joined(self):
        await super().joined()
        self.moved = True

    def get_subscriptions(self) -> set[str]:
        return set() if self.moved else {ROOM}


class NajEcho(NajLeaf):
    """Tells the room in joined(): that comes in the channel ahead of what the join mails."""

    async def joined(self):
        await super().joined()
        await self.broadcast(ROOM, n=2)


class NajSprout(LiveComponent):
    class Meta:
        subscriptions = {ROOM}

    async def joined(self):
        CALLS.append(("joined", self.id))

    async def notification(self, channel: str, **kwargs):
        CALLS.append(("notified", self.id, kwargs["n"]))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% live_tag_header %}></b>")


class NajHost(Component):
    """Draws a leaf (or a LiveComponent) once shown; ``shout`` shows it and tells the room."""

    class Meta:
        subscriptions = {ROOM}

    shown: bool = False
    live: bool = False
    mover: bool = False
    echo: bool = False

    async def shout(self, n: int):
        self.shown = True
        await self.broadcast(ROOM, n=n)

    async def show(self):
        self.shown = True

    async def hide(self):
        self.shown = False

    async def notification(self, channel: str, **kwargs):
        CALLS.append(("notified", self.id, kwargs["n"]))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{% if this.shown %}"
            "{% if this.live %}{% live_component 'NajSprout' id='naj-sprout' %}"
            "{% elif this.mover %}{% component 'NajMover' id='naj-leaf' %}"
            "{% elif this.echo %}{% component 'NajEcho' id='naj-leaf' %}"
            "{% else %}{% component 'NajLeaf' id='naj-leaf' %}{% endif %}"
            "{% endif %}</div>"
        )


#: Set while a render of NajSlowHost waits for the test to publish
DRAWING = threading.Event()
PUBLISHED = threading.Event()


class NajSlowHost(NajHost):
    """Its render waits, in the worker thread, while the test publishes to the room."""

    @property
    def stall(self) -> str:
        if self.shown and not PUBLISHED.is_set():
            DRAWING.set()
            assert PUBLISHED.wait(5)
        return ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{{ this.stall }}{% if this.shown %}"
            "{% component 'NajLeaf' id='naj-leaf' %}{% endif %}</div>"
        )


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    DRAWING.clear()
    PUBLISHED.clear()


async def _open() -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), "/__wireview__?vsn=99")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _join(communicator: WebsocketCommunicator, component: type[Component], id: str, **state) -> None:
    signed = sign_state(component(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **state))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": component.__name__, "state": signed, "children": {}}}
    )
    await _joined(communicator, id)


async def _event(communicator: WebsocketCommunicator, id: str, command: str, **args) -> None:
    payload = {"id": id, "command": command, "implicit_args": {}, "explicit_args": args}
    await communicator.send_json_to({"command": "user_event", "payload": payload})


async def _joined(communicator: WebsocketCommunicator, id: str) -> None:
    """Read up to the ``joined`` that ends the join of ``id``."""
    for _ in range(20):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "joined" and message["payload"]["id"] == id:
            return
    raise AssertionError(f"no joined for {id}")


async def _render_of(communicator: WebsocketCommunicator, id: str, text: str) -> None:
    for _ in range(20):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "render" and message["payload"]["id"] == id and text in json.dumps(message):
            return
    raise AssertionError(f"no render of {id} with {text!r}")


async def _quiet(communicator: WebsocketCommunicator) -> list[dict[str, t.Any]]:
    """What comes until the connection has nothing more to say: a check that nothing else comes."""
    heard = []
    while not await communicator.receive_nothing(timeout=0.3):
        heard.append(await communicator.receive_json_from())
    return heard


def _leaf() -> list[tuple[t.Any, ...]]:
    """What the leaf's instances ran, in order."""
    return [call for call in CALLS if call[1] == "naj-leaf"]


async def _heard_the_room(communicator: WebsocketCommunicator, n: int) -> None:
    """The host heard ``n``: the notification is handled, the leaf's turn in it included."""
    await eventually(lambda: ("notified", "naj-host", n) in CALLS)
    await _quiet(communicator)


async def test_a_component_its_drawers_handler_shows_hears_that_handlers_broadcast_once_it_joined():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host")
        # The render draws the leaf; the broadcast comes back after it, before the page's join of the leaf
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        assert ("notified", "naj-leaf", 1) not in CALLS

        await _join(communicator, NajLeaf, "naj-leaf")
        # Not lost: heard after joined() -- behind ``joined``, read above -- and the render shows it
        await _render_of(communicator, "naj-leaf", '"1"')
        assert _leaf() == [
            ("joined", "naj-leaf"),
            ("notified", "naj-leaf", 1),
        ]
    finally:
        await communicator.disconnect()


async def test_what_a_component_heard_before_its_join_comes_in_order_and_once():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host")
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        await _event(communicator, "naj-host", "shout", n=2)
        await _heard_the_room(communicator, 2)

        await _join(communicator, NajLeaf, "naj-leaf")
        await _render_of(communicator, "naj-leaf", '"2"')
        await _quiet(communicator)
        assert _leaf() == [
            ("joined", "naj-leaf"),
            ("notified", "naj-leaf", 1),
            ("notified", "naj-leaf", 2),
        ]
        # After its join it hears the room as any component does
        await _event(communicator, "naj-host", "shout", n=3)
        await _render_of(communicator, "naj-leaf", '"3"')
        assert CALLS.count(("notified", "naj-leaf", 3)) == 1
    finally:
        await communicator.disconnect()


async def test_a_component_heard_from_while_its_drawers_render_runs_hears_it_once_it_joined():
    # The render runs in a worker thread (#138); the loop is free meanwhile
    communicator = await _open()
    try:
        await _join(communicator, NajSlowHost, "naj-host")
        await _event(communicator, "naj-host", "show")
        assert await asyncio.to_thread(DRAWING.wait, 5)
        # From a thread of its own: the render holds the thread sync_to_async would use
        await asyncio.to_thread(broadcast, ROOM, n=7)
        PUBLISHED.set()
        await _heard_the_room(communicator, 7)
        assert ("notified", "naj-leaf", 7) not in CALLS

        await _join(communicator, NajLeaf, "naj-leaf")
        await _render_of(communicator, "naj-leaf", '"7"')
        assert _leaf() == [
            ("joined", "naj-leaf"),
            ("notified", "naj-leaf", 7),
        ]
    finally:
        await communicator.disconnect()


async def test_what_a_component_its_drawer_stopped_drawing_before_its_join_heard_goes_with_it():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host")
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        await _event(communicator, "naj-host", "hide")
        await _quiet(communicator)
        await _event(communicator, "naj-host", "shout", n=2)
        await _heard_the_room(communicator, 2)
        # Shown anew: another instance, which hears what came after it was drawn and
        # nothing the one its drawer let go of heard
        await _join(communicator, NajLeaf, "naj-leaf")
        await _render_of(communicator, "naj-leaf", '"2"')
        await _quiet(communicator)
        assert _leaf() == [("joined", "naj-leaf"), ("notified", "naj-leaf", 2)]
    finally:
        await communicator.disconnect()


async def test_what_comes_while_it_joins_waits_behind_what_came_before():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host", echo=True)
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        await _join(communicator, NajEcho, "naj-leaf")
        await _render_of(communicator, "naj-leaf", '"2"')
        await _quiet(communicator)
        assert _leaf() == [("joined", "naj-leaf"), ("notified", "naj-leaf", 1), ("notified", "naj-leaf", 2)]
    finally:
        await communicator.disconnect()


async def test_a_component_whose_joined_leaves_the_channel_does_not_hear_what_came_on_it_before():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host", mover=True)
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        await _join(communicator, NajMover, "naj-leaf")
        await _quiet(communicator)
        assert _leaf() == [("joined", "naj-leaf")]
    finally:
        await communicator.disconnect()


async def test_a_live_component_its_parents_handler_shows_hears_that_handlers_broadcast_after_joined():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host", live=True)
        await _event(communicator, "naj-host", "shout", n=1)
        await _heard_the_room(communicator, 1)
        sprout = [call for call in CALLS if call[1] == "naj-sprout"]
        assert sprout[0] == ("joined", "naj-sprout")
        assert sprout.count(("notified", "naj-sprout", 1)) <= 1
    finally:
        await communicator.disconnect()


async def test_a_component_its_drawer_showed_hears_the_pages_params_once_it_joined_not_before():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host")
        await _event(communicator, "naj-host", "show")
        await _quiet(communicator)
        await communicator.send_json_to({"command": "params_changed", "payload": {"params": {"q": "x"}, "uri": "?q=x"}})
        await _quiet(communicator)
        assert _leaf() == []

        # Its join tells it the page's params, as every join does
        await _join(communicator, NajLeaf, "naj-leaf")
        assert _leaf() == [("joined", "naj-leaf"), ("params", "naj-leaf", "x")]
    finally:
        await communicator.disconnect()


async def test_a_receiver_that_raises_on_what_came_before_the_join_is_recovered_as_any_receiver_is():
    communicator = await _open()
    try:
        await _join(communicator, NajHost, "naj-host")
        await _event(communicator, "naj-host", "shout", n=13)
        await _heard_the_room(communicator, 13)
        await _event(communicator, "naj-host", "shout", n=14)
        await _heard_the_room(communicator, 14)
        await _join(communicator, NajLeaf, "naj-leaf")
        heard = await _quiet(communicator)
        # The page joins it again from what it shows; what came after the one that raised goes with the instance
        assert {"command": "error", "payload": {"id": "naj-leaf", "during": "event"}} in heard
        assert _leaf() == [("joined", "naj-leaf"), ("notified", "naj-leaf", 13)]
    finally:
        await communicator.disconnect()
