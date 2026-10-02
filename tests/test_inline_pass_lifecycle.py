"""The LiveComponents of a component another's pass draws inline settle in that pass's render.

R draws N with ``{% component %}`` and passes it props; N draws a LiveComponent.
R's render runs N's template within its own pass, so what N's template stopped
drawing, drew anew or drew with other props is known only there. The lifecycle
of N's LiveComponents rides on R's render (docs/design/live-component-ownership.md
§3-3): ``leaving()`` for the one N hid, ``joined()`` for the one N shows,
``update()`` for the one whose props changed, each rendered in R's frame.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import JOINED_SINCE
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

HEARD: list[tuple[str, str, t.Any]] = []


class InlineLeaf(LiveComponent):
    count: int = 0
    note: str = ""

    async def joined(self):
        HEARD.append(("joined", self.id, self.count))

    async def update(self, **assigns):
        HEARD.append(("update", self.id, dict(assigns)))
        await super().update(**assigns)

    async def leaving(self):
        HEARD.append(("leaving", self.id, self.count))

    async def bump(self):
        self.count += 1

    @property
    def label(self) -> str:
        return f"leaf={self.count}:{self.note}"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}>{{ this.label }}</i>")


class InlineNest(Component):
    shown: bool = True
    note: str = ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<s {% tag_header %}>"
            "{% if this.shown %}{% live_component 'InlineLeaf' id='in-leaf' note=this.note %}{% endif %}</s>"
        )


class InlineRoot(Component):
    shown: bool = True
    note: str = ""

    async def toggle(self):
        self.shown = not self.shown

    async def write(self, note: str):
        self.note = note

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'InlineNest' id='in-nest' shown=this.shown note=this.note %}</p>"
        )


class InlineFrame(Component):
    """A level between the root and the nest: the nest's pass runs within the frame's, within the root's."""

    shown: bool = True
    note: str = ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<u {% tag_header %}>"
            "{% component 'InlineNest' id='in-nest' shown=this.shown note=this.note %}</u>"
        )


class InlineDeepRoot(InlineRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'InlineFrame' id='in-frame' shown=this.shown note=this.note %}</p>"
        )


class InlineSlotOwner(Component):
    """Draws the slot its drawer filled behind a flag of its own."""

    shown: bool = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<section {% tag_header %}>{% if this.shown %}{% render_slot 'body' %}{% endif %}"
            "</section>"
        )


class InlineSlotRoot(InlineRoot):
    """Puts the leaf in the slot of a component its pass draws: the leaf is the root's, not the slot owner's."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component_block 'InlineSlotOwner' id='in-owner' shown=this.shown %}{% fill body %}"
            "{% live_component 'InlineLeaf' id='in-leaf' note=this.note %}"
            "{% endfill %}{% endcomponent %}</p>"
        )


class InlineMoveRoot(InlineRoot):
    """Takes the leaf over from the nest in the same pass that has the nest hide it."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'InlineNest' id='in-nest' shown=this.shown %}"
            "{% if not this.shown %}{% live_component 'InlineLeaf' id='in-leaf' %}{% endif %}</p>"
        )


class _Outbound:
    def __init__(self) -> None:
        self.renders: list[dict[str, t.Any]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        if command == "render":
            self.renders.append(payload)

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


@pytest.fixture(autouse=True)
def _reset_heard():
    HEARD.clear()
    yield
    HEARD.clear()


def _leaves(heard: t.Any) -> list[str]:
    """Every ``leaf=`` text a message draws, in order."""
    if isinstance(heard, str):
        return [heard] if heard.startswith("leaf=") else []
    values = heard.values() if isinstance(heard, dict) else heard if isinstance(heard, list) else []
    return [found for value in values for found in _leaves(value)]


async def _page(*joins: tuple[type[Component], str], **fields: t.Any) -> tuple[WireviewConsumer, _Outbound]:
    """A connection that joined the root and the components the page joins under it, in order."""
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=JOINED_SINCE)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.outbound = outbound = _Outbound()  # type: ignore[assignment]
    for component, id in joins:
        state = sign_state(component(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **fields))
        await consumer.command_join(component.__name__, state, children={})
    return consumer, outbound


async def _event(consumer: WireviewConsumer, outbound: _Outbound, id: str, command: str, **args: t.Any):
    outbound.renders.clear()
    await consumer.command_user_event(id, command, {}, args)
    return outbound.renders


PAGES = {
    "nest": [(InlineRoot, "in-root"), (InlineNest, "in-nest")],
    "frame": [(InlineDeepRoot, "in-root"), (InlineFrame, "in-frame"), (InlineNest, "in-nest")],
}


@pytest.mark.parametrize("page", PAGES)
async def test_a_live_component_the_nest_hides_by_its_drawers_prop_leaves_and_comes_back_anew(page):
    consumer, outbound = await _page(*PAGES[page])
    for _ in range(3):
        await _event(consumer, outbound, "in-leaf", "bump")
    assert HEARD == [("joined", "in-leaf", 0)]

    hidden = await _event(consumer, outbound, "in-root", "toggle")

    assert HEARD[1:] == [("leaving", "in-leaf", 3)]
    assert consumer.repo.get("in-leaf") is None
    assert _leaves(hidden) == []

    shown = await _event(consumer, outbound, "in-root", "toggle")

    assert HEARD[2:] == [("joined", "in-leaf", 0)]
    assert _leaves(shown) == ["leaf=0:"], shown
    assert shown[0]["id"] == "in-root"
    assert "in-leaf" in shown[0]["instances"]
    # Each record of a pass within the root's went with the root's batch
    assert consumer.repo._inline == {}
    assert consumer.repo._inline_rendered == {}


@pytest.mark.parametrize("page", PAGES)
async def test_a_prop_the_drawer_passes_through_the_nest_reaches_update_in_the_drawers_render(page):
    consumer, outbound = await _page(*PAGES[page])
    await _event(consumer, outbound, "in-leaf", "bump")

    rendered = await _event(consumer, outbound, "in-root", "write", note="hi")

    assert HEARD[1:] == [("update", "in-leaf", {"note": "hi"})]
    assert _leaves(rendered) == ["leaf=1:hi"], rendered


async def test_a_live_component_the_nest_shows_first_by_its_drawers_prop_joins_in_the_drawers_render():
    consumer, outbound = await _page(*PAGES["nest"], shown=False)
    assert HEARD == []

    shown = await _event(consumer, outbound, "in-root", "toggle")

    assert HEARD == [("joined", "in-leaf", 0)]
    assert _leaves(shown) == ["leaf=0:"], shown


async def test_the_nest_s_own_join_still_settles_what_the_drawers_first_pass_drew():
    # The root's first render builds the nest and its leaf; the page has yet to
    # join the nest, and that join is what settles them (§3-2).
    consumer, outbound = await _page(*PAGES["nest"][:1])
    assert HEARD == []

    state = sign_state(InlineNest(user=AnonymousUser(), wire=WireviewMeta(params={}), id="in-nest"))
    outbound.renders.clear()
    await consumer.command_join("InlineNest", state, children={})

    assert HEARD == [("joined", "in-leaf", 0)]
    assert _leaves(outbound.renders) == ["leaf=0:"]


async def test_a_leaf_in_a_slot_the_owner_hides_is_the_drawers_and_stays():
    # The slot owner's pass runs within the root's, and the leaf in its slot is
    # the root's: the owner's pass hiding the slot does not retire it.
    consumer, outbound = await _page((InlineSlotRoot, "in-root"), (InlineSlotOwner, "in-owner"))
    for _ in range(2):
        await _event(consumer, outbound, "in-leaf", "bump")

    await _event(consumer, outbound, "in-root", "write", note="x")

    assert HEARD == [("joined", "in-leaf", 0), ("update", "in-leaf", {"note": "x"})]
    assert consumer.repo.get("in-leaf").count == 2  # type: ignore[union-attr]


async def test_a_leaf_the_drawer_takes_over_in_the_same_pass_keeps_its_state():
    # The nest's pass stops drawing the leaf and the root's draws it further on:
    # the leaf moved to the root. The nest's batch, run after the pass, does not
    # count it among the nest's children any more.
    consumer, outbound = await _page((InlineMoveRoot, "in-root"), (InlineNest, "in-nest"))
    for _ in range(2):
        await _event(consumer, outbound, "in-leaf", "bump")

    await _event(consumer, outbound, "in-root", "toggle")

    assert HEARD == [("joined", "in-leaf", 0)]
    assert consumer.repo.get("in-leaf").count == 2  # type: ignore[union-attr]
    assert consumer.repo.get("in-leaf")._parent_id == "in-root"  # type: ignore[union-attr]
