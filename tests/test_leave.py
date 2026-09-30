"""``leave``: a component that disappears from the DOM gets ``leaving()`` and is cleaned up (#80)."""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

CALLS: list[tuple[str, str]] = []


class LeaveProbeParent(Component):
    class Meta:
        template_name = "livecomp/dashboard.html"
        subscriptions = {"leave-probe-topic"}

    async def leaving(self):
        CALLS.append(("leaving", self.id))


class LeaveProbeChild(LiveComponent):
    class Meta:
        template_name = "livecomp/counter.html"

    count: int = 0

    async def leaving(self):
        CALLS.append(("leaving", self.id))


class LeaveProbeFailing(Component):
    class Meta:
        template_name = "livecomp/dashboard.html"

    async def leaving(self):
        CALLS.append(("leaving", self.id))
        raise RuntimeError("cleanup went wrong")


class LeaveProbeShown(LiveComponent):
    async def leaving(self):
        CALLS.append(("leaving", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<span {% live_tag_header %}>shown</span>")


class LeaveProbeToggle(Component):
    """Shows its LiveComponent while ``shown``: hiding and showing it again makes a new instance."""

    shown: bool = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>"
            "{% if this.shown %}{% live_component 'LeaveProbeShown' id='t-child' %}{% endif %}</div>"
        )


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)


def make_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


async def test_leave_calls_leaving_and_removes_the_component():
    consumer, _ = make_consumer()
    consumer.repo.build("LeaveProbeParent", {"id": "p1"})

    await consumer.command_leave("p1")

    assert CALLS == [("leaving", "p1")]
    assert consumer.repo.get("p1") is None


async def test_leave_cascades_to_live_components_under_the_parent():
    consumer, _ = make_consumer()
    consumer.repo.build("LeaveProbeParent", {"id": "p1"})
    consumer.repo.build_live_component("LeaveProbeChild", {"id": "c1"}, parent_id="p1")
    consumer.repo.build_live_component("LeaveProbeChild", {"id": "c2"}, parent_id="p1")
    consumer.repo.build("LeaveProbeParent", {"id": "other"})

    await consumer.command_leave("p1")

    assert CALLS == [("leaving", "p1"), ("leaving", "c1"), ("leaving", "c2")]
    assert consumer.repo.get("c1") is None and consumer.repo.get("c2") is None
    assert consumer.repo.get("other") is not None, "an unrelated component is untouched"


async def test_a_failing_leaving_hook_is_logged_and_the_component_still_goes():
    consumer, _ = make_consumer()
    consumer.repo.build("LeaveProbeFailing", {"id": "bad"})

    await consumer.command_leave("bad")

    assert CALLS == [("leaving", "bad")]
    assert consumer.repo.get("bad") is None


async def test_leave_drops_subscriptions_nobody_needs_any_more():
    consumer, outbound = make_consumer()
    consumer.repo.build("LeaveProbeParent", {"id": "p1"})
    await consumer.after_mutation_chores()
    assert outbound.subscribed == ["leave-probe-topic"]

    await consumer.command_leave("p1")

    assert "leave-probe-topic" in outbound.unsubscribed
    assert consumer.subscriptions == set()


async def test_leave_of_an_unknown_id_is_a_no_op():
    consumer, outbound = make_consumer()

    await consumer.command_leave("ghost")

    assert CALLS == []
    assert outbound.commands == []


async def test_disconnect_still_reaches_every_registered_component():
    consumer, _ = make_consumer()
    consumer.repo.build("LeaveProbeParent", {"id": "p1"})
    consumer.repo.build_live_component("LeaveProbeChild", {"id": "c1"}, parent_id="p1")

    # Consumer.disconnect() on a bare consumer has no transport; the leaving loop is what we test.
    await consumer._call_leaving(list(consumer.repo.components.values()))

    assert sorted(CALLS) == [("leaving", "c1"), ("leaving", "p1")]


@pytest.mark.django_db
async def test_a_late_leave_for_a_live_component_id_spares_the_instance_shown_again():
    """#140: the page used to send ``leave`` for a LiveComponent's id when its element went.

    The server had already retired that child in the parent's render. Hidden and
    shown again before the leave arrived -- two renders a frame apart, the first
    morph's leave sent after the second render was handled -- the leave removed
    the new instance: its events went nowhere until the page joined again. A
    LiveComponent belongs to its parent, so a leave naming one is not the page's
    to send and is ignored (an older bundle still sends it).
    """
    consumer, _ = make_consumer()
    board = await consumer.repo.join("LeaveProbeToggle", {"id": "toggle"})
    await consumer.send_render(board, announce=True)
    first = consumer.repo.get("t-child")
    board.shown = False
    await consumer.send_render(board)
    board.shown = True
    await consumer.send_render(board)
    again = consumer.repo.get("t-child")
    assert again is not None and again is not first
    CALLS.clear()

    await consumer.handle_message({"command": "leave", "payload": {"id": "t-child"}})

    assert consumer.repo.get("t-child") is again
    assert CALLS == []
