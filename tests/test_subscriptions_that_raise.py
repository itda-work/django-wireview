"""A component whose ``get_subscriptions()`` raises costs itself alone (#233).

The connection asks every component what it subscribes to after each message
(``after_mutation_chores``), and once used to let the first exception out: the
socket closed and every component of the page joined again. Now, as for any
code of a component that raises (``docs/features/errors.md``), that component
is joined again and the rest carry on.

A render asks the components it drew first (``_render_tree``): the frame that
would hand the page the state that raised never goes, so the page joins the
component again with the state before the message, and the event is answered
by the ``error`` alone. A component no render of the message drew -- its topics
read something besides its state -- is caught by the chores, after the
message's answer. In a join, one of the joining tree that raises fails the join,
which is not retried: the state it joined with would raise again.
"""

from __future__ import annotations

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.outbound import RecordingOutbound

from wireview import Component, LiveComponent, mount
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.features.presence import PresenceMixin
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

#: Ids whose get_subscriptions() raises whatever their state: topics read from elsewhere
BROKEN: set[str] = set()


@pytest.fixture(autouse=True)
def _reset():
    BROKEN.clear()
    yield
    BROKEN.clear()


def _topics(component: t.Any, room: int | None, prefix: str) -> set[str]:
    if component.id in BROKEN:
        raise RuntimeError(f"topics of {component.id} went wrong")
    # The breaking input of #233: a field the handler emptied, read to name the channel
    return {f"{prefix}-{room:d}"}  # type: ignore[str-format]


class SrRoom(Component):
    room: int | None = 1
    heard: int = 0

    def get_subscriptions(self) -> set[str]:
        return _topics(self, self.room, "sr-room")

    async def leave_room(self, **_rest: t.Any) -> None:
        self.room = None

    async def break_other(self, other: str, **_rest: t.Any) -> None:
        BROKEN.add(other)

    async def break_self(self, **_rest: t.Any) -> None:
        BROKEN.add(self.id)

    async def bump(self, **_rest: t.Any) -> None:
        self.heard += 1

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.heard += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}>{{ room }}/{{ heard }}</div>")


class SrDesk(LiveComponent):
    room: int | None = 1

    def get_subscriptions(self) -> set[str]:
        return _topics(self, self.room, "sr-desk")

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.room = None

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% live_tag_header %}>{{ room }}</div>")


class SrHall(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template('{% load wireview %}<div {% tag_header %}>{% live_component "SrDesk" id="desk" %}</div>')


class SrProducer(PresenceMixin, Component):
    room: int | None = 1

    def _presence_topic(self) -> str:
        return "sr-presence"

    def _presence_user_id(self) -> str:
        return self.id

    def _presence_username(self) -> str:
        return self.id

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | _topics(self, self.room, "sr-producer")

    async def joined(self) -> None:
        await self.presence_join()

    async def leaving(self) -> None:
        await self.presence_leave()

    async def leave_room(self, **_rest: t.Any) -> None:
        self.room = None

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}>{{ room }}</div>")


def _session() -> tuple[WireviewConsumer, RecordingOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.page_uri = None
    consumer.channel_name = "sr-channel"
    outbound = RecordingOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def _state(cls: type[Component], id: str, **fields: t.Any) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **fields))


async def _join(session: WireviewConsumer, cls: type[Component], id: str, ref: int, **fields: t.Any) -> str:
    """Join it; returns the state the page holds for it, the one its join's render carried."""
    state = _state(cls, id, **fields)
    await session.command_join(cls.__name__, state, children={}, ref=ref)
    return state


async def _event(session: WireviewConsumer, id: str, command: str, ref: int, **args: t.Any) -> None:
    await session.command_user_event(id, command, {}, args, ref=ref)


def _answers(outbound: RecordingOutbound, ref: int) -> list[tuple[str, dict[str, t.Any]]]:
    return [(command, payload) for command, payload in outbound.commands if payload.get("ref") == ref]


async def test_an_event_that_breaks_the_topics_is_answered_by_the_error_alone():
    """The page keeps the state before the event: the render that would carry the broken one never goes."""
    session, outbound = _session()
    held = await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2, room=2)
    assert outbound.topics == {"sr-room-1", "sr-room-2"}
    outbound.commands.clear()

    await _event(session, "a", "leave_room", ref=3)

    # One answer to the event, and not a render: the page settles it once, and rolls back
    assert _answers(outbound, 3) == [("error", {"id": "a", "during": "event", "ref": 3})]
    assert not outbound.closed
    # What only it heard is left; the one beside it hears what it did
    assert outbound.topics == {"sr-room-2"}
    assert session.repo.get("b") is not None

    # The page joins it again with the state its element still carries: whole again
    await session.command_join("SrRoom", held, children={}, ref=4)
    assert [command for command, _ in _answers(outbound, 4)] == ["render"]
    assert outbound.topics == {"sr-room-1", "sr-room-2"}


async def test_a_topic_another_component_hears_is_kept():
    session, outbound = _session()
    await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2)

    await _event(session, "a", "leave_room", ref=3)

    assert [error["id"] for command, error in outbound.commands if command == "error"] == ["a"]
    assert outbound.topics == {"sr-room-1"}


async def test_one_no_render_drew_is_recovered_after_the_answer():
    """Its topics read something besides its state: the chores find it, after the event's own answer."""
    session, outbound = _session()
    await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2, room=2)
    outbound.commands.clear()

    await _event(session, "a", "break_other", ref=3, other="b")

    commands = [(command, payload.get("id"), payload.get("ref")) for command, payload in outbound.commands]
    # The event is answered by its render; the error for b comes after, naming no event
    assert commands.index(("render", "a", 3)) < commands.index(("error", "b", None))
    assert session.repo.get("a") is not None and session.repo.get("b") is None
    assert outbound.topics == {"sr-room-1"}
    assert not outbound.closed


async def test_joined_again_and_raising_again_fails_the_join_and_stops():
    """No loop: the event's error makes the page join it again; that join raises and is not retried."""
    session, outbound = _session()
    held = await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2, room=2)

    await _event(session, "a", "break_self", ref=3)
    assert _answers(outbound, 3) == [("error", {"id": "a", "during": "event", "ref": 3})]

    # What the page does on that error: join it with the state its element carries
    await session.command_join("SrRoom", held, children={}, ref=4)

    assert _answers(outbound, 4) == [("error", {"id": "a", "during": "join", "ref": 4})]
    # The page does not join it again (wireview.js: a join that failed is not retried), and the
    # server runs nothing of it meanwhile: an event is answered without its handler
    assert session.repo.refused("a")
    await _event(session, "a", "bump", ref=5)
    assert _answers(outbound, 5) == [("render", {"id": "a", "diff": None, "ref": 5})]
    assert [error["during"] for command, error in outbound.commands if command == "error"] == ["event", "join"]
    assert outbound.topics == {"sr-room-2"}
    assert not outbound.closed


async def test_a_join_whose_topics_raise_fails_and_is_not_retried():
    session, outbound = _session()
    BROKEN.add("a")

    await _join(session, SrRoom, "a", ref=1)

    assert _answers(outbound, 1) == [("error", {"id": "a", "during": "join", "ref": 1})]
    assert session.repo.refused("a")
    assert not outbound.closed


async def test_a_join_whose_topics_raise_only_when_settled_fails_the_join_too():
    """Past the render's question, at the join's own chores: still the join's failure, not an event's."""
    session, outbound = _session()
    await _join(session, SrRoom, "b", ref=1, room=2)
    settle = session.after_mutation_chores

    async def breaking_as_it_settles(joining: t.Any = None) -> None:
        if joining is not None:
            BROKEN.add(joining.id)
        await settle(joining=joining)

    session.after_mutation_chores = breaking_as_it_settles  # type: ignore[method-assign]

    await _join(session, SrRoom, "a", ref=2)

    assert [(command, payload.get("during")) for command, payload in _answers(outbound, 2)] == [
        ("render", None),
        ("error", "join"),
    ]
    assert session.repo.refused("a") and session.repo.get("b") is not None
    assert outbound.topics == {"sr-room-2"}


async def test_a_live_component_that_breaks_costs_its_root():
    session, outbound = _session()
    await _join(session, SrHall, "hall", ref=1)
    await _join(session, SrRoom, "b", ref=2, room=2)
    assert outbound.topics == {"sr-desk-1", "sr-room-2"}

    await session.notification({"type": "notification", "channel": "sr-desk-1", "kwargs": {}})

    assert [(error["id"], error["during"]) for command, error in outbound.commands if command == "error"] == [
        ("hall", "event")
    ]
    assert session.repo.get("desk") is None and session.repo.get("b") is not None
    assert outbound.topics == {"sr-room-2"}


async def test_one_that_raises_while_a_notification_is_handed_out_costs_itself():
    """Asked who hears the channel, it raises: recovered there, and the others still hear it."""
    session, outbound = _session()
    await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2)
    BROKEN.add("a")
    outbound.commands.clear()

    await session.notification({"type": "notification", "channel": "sr-room-1", "kwargs": {}})

    assert [error["id"] for command, error in outbound.commands if command == "error"] == ["a"]
    assert t.cast(SrRoom, session.repo.get("b")).heard == 1
    assert not outbound.closed


async def test_one_that_raises_as_what_it_missed_is_handed_over_costs_itself():
    """``deliver_unheard`` asks each one whether it still hears the channel: in its own recovery."""
    session, outbound = _session()
    await _join(session, SrRoom, "a", ref=1)
    await _join(session, SrRoom, "b", ref=2)
    a, b = session.repo.get("a"), t.cast(SrRoom, session.repo.get("b"))
    assert a is not None
    a.wire.unheard = [(0, "notification", "sr-room-1", {})]
    b.wire.unheard = [(1, "notification", "sr-room-1", {})]
    BROKEN.add("a")
    outbound.commands.clear()

    await session.component_deliver_unheard("a", a.wire.instance)
    await session.component_deliver_unheard("b", b.wire.instance)

    assert [error["id"] for command, error in outbound.commands if command == "error"] == ["a"]
    assert b.heard == 1
    assert not outbound.closed


async def test_a_presence_producer_that_breaks_leaves_its_presence():
    session, outbound = _session()
    await _join(session, SrProducer, "p", ref=1)
    producer = t.cast(SrProducer, session.repo.get("p"))
    presence_channel = producer._presence_channel()
    assert presence_channel in outbound.topics

    await _event(session, "p", "leave_room", ref=2)

    assert _answers(outbound, 2) == [("error", {"id": "p", "during": "event", "ref": 2})]
    assert not producer._presence_joined
    assert presence_channel not in outbound.topics and "sr-producer-1" not in outbound.topics


async def test_mount_raises_it_to_the_test():
    """``mount`` hears what a session recovers: its ``_crashed`` raises, so the test sees the error."""
    view = await mount(SrRoom, id="a")

    with pytest.raises(TypeError):
        await view.call("leave_room")
