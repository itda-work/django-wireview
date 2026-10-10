"""A nested component its drawer's pass stops drawing retires with that pass (#204).

``{% component %}`` without an id builds a new instance under a new
``rx-<uuid>`` in every pass of its drawer, so a drawer that renders again
leaves the one before behind. The page lets that element go and says so with a
leave, one round trip later, and a page that never says so (a socket of
another client, a leave lost with the connection) left it for good: it
stayed reachable, heard broadcasts, received patches and kept its tasks until
the connection ended, and ``leaving()`` came only then.

The end of the drawer's pass retires it instead (``ComponentRepository.end_pass``):
it is removed, with what its own passes drew, and its ``leaving()`` runs with
the drawer's render. A component drawn with an id that the pass hides by an
``{% if %}`` retires the same way. One a part kept for a reset temporary
assign still shows stays.
"""

import asyncio
import typing as t

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from django.test import override_settings

from wireview import Component, function_component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import JOINED_SINCE
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TOPIC = "retire.nested"
HEARD: list[tuple[str, str]] = []


class RetireLeaf(Component):
    class Meta:
        subscriptions = {TOPIC}

    note: str = ""

    async def joined(self):
        HEARD.append(("joined", self.id))
        await self.start_async("wait", asyncio.sleep(3600))

    async def leaving(self):
        HEARD.append(("leaving", self.id))

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        HEARD.append(("notified", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% tag_header %}>{{ this.note }}</i>")


class RetireMiddle(Component):
    """Draws a leaf without an id of its own: what it drew goes with it."""

    note: str = ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% tag_header %}>{% component 'RetireLeaf' note=this.note %}</b>")


class RetireRoot(Component):
    shown: bool = True
    note: str = ""

    async def write(self, note: str):
        self.note = note

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'RetireLeaf' note=this.note %}{% endif %}</p>"
        )


class RetireNamedRoot(RetireRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'RetireLeaf' id='named-leaf' note=this.note %}{% endif %}</p>"
        )


class RetireDeepRoot(RetireRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'RetireMiddle' note=this.note %}{% endif %}</p>"
        )


#: A function component's template gets no ``this``: the leaf it draws is still the root's pass's
FUNC_TEMPLATES = {"retire/card.html": "{% load wireview %}{% component 'RetireLeaf' note=note %}"}


@function_component(template="retire/card.html")
def retire_card(note: str = ""):
    return {"note": note}


class RetireFuncRoot(RetireRoot):
    """Draws the leaf through a function component: the drawer is still the root."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{% if this.shown %}{% func 'retire_card' note=this.note %}"
            "{% endif %}</p>"
        )


class RetireSlotOwner(Component):
    count: int = 0

    async def bump(self):
        self.count += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<section {% tag_header %}>{{ this.count }}{% render_slot 'body' %}</section>"
        )


class RetireSlotRoot(RetireRoot):
    """Fills a slot with the leaf: the root draws it, the owner shows it."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component_block 'RetireSlotOwner' id='slot-owner' %}{% fill body %}"
            "{% if this.shown %}{% component 'RetireLeaf' note=this.note %}{% endif %}"
            "{% endfill %}{% endcomponent %}</p>"
        )


class RetireKeptRoot(Component):
    """Draws a leaf per row of a temporary assign: a render that leaves the rows alone keeps them."""

    class Meta:
        temporary_assigns = ["rows"]

    rows: list[str] = []
    note: str = ""

    async def load(self):
        self.rows = ["a", "b"]

    async def write(self, note: str):
        self.note = note

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{{ this.note }}"
            "{% for row in rows %}{% component 'RetireLeaf' note=row %}{% endfor %}</p>"
        )


class _Outbound:
    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None: ...

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


@pytest.fixture(autouse=True)
def _func_templates():
    engine = settings.TEMPLATES[0]
    options = {**engine["OPTIONS"], "loaders": [("django.template.loaders.locmem.Loader", FUNC_TEMPLATES)]}
    options["loaders"].append("django.template.loaders.app_directories.Loader")
    with override_settings(TEMPLATES=[{**engine, "APP_DIRS": False, "OPTIONS": options}]):
        yield


@pytest.fixture(autouse=True)
def _reset_heard():
    HEARD.clear()
    yield
    HEARD.clear()


async def _join(consumer: WireviewConsumer, component: Component) -> None:
    state = sign_state(component)
    await consumer.command_join(type(component).__name__, state, children={})


async def _join_drawn(consumer: WireviewConsumer) -> None:
    """Join what the latest render put on the page and the page has yet to join, as the page does."""
    while drawn := [c for c in consumer.repo.components.values() if not c.wire.has_joined]:
        await _join(consumer, drawn[0])


async def _page(root: type[Component], **fields: t.Any) -> WireviewConsumer:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=JOINED_SINCE)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.outbound = _Outbound()  # type: ignore[assignment]
    await _join(consumer, root(user=AnonymousUser(), wire=WireviewMeta(params={}), id="root", **fields))
    await _join_drawn(consumer)
    return consumer


async def _event(consumer: WireviewConsumer, id: str, command: str, **args: t.Any) -> None:
    await consumer.command_user_event(id, command, {}, args)
    await _join_drawn(consumer)


def _leaves(consumer: WireviewConsumer) -> list[RetireLeaf]:
    return [c for c in consumer.repo.reachable_components() if isinstance(c, RetireLeaf)]


def _ids(kind: str) -> list[str]:
    return [id for heard, id in HEARD if heard == kind]


@pytest.mark.parametrize("root", [RetireRoot, RetireFuncRoot, RetireSlotRoot])
async def test_each_render_of_the_drawer_retires_the_leaf_it_drew_before(root):
    consumer = await _page(root)
    first = _leaves(consumer)
    assert len(first) == 1
    tasks = [first[0]._async_tasks["wait"]]

    for n in range(3):
        await _event(consumer, "root", "write", note=f"n{n}")
        tasks += [leaf._async_tasks["wait"] for leaf in _leaves(consumer)]

    leaves = _leaves(consumer)
    assert len(leaves) == 1
    assert leaves[0].note == "n2"
    joined = _ids("joined")
    assert len(joined) == 4
    # Each one the drawer drew before left with the render that dropped it, without the page's leave
    assert _ids("leaving") == joined[:-1]
    assert sum(isinstance(c, RetireLeaf) for c in consumer.repo.components.values()) == 1
    # Their tasks were cancelled; the shown one's runs on
    await asyncio.wait(tasks[:-1], timeout=5)
    assert [task.done() for task in tasks] == [True, True, True, False]

    # The page's leave for an element it dropped finds nothing to run again
    for id in joined[:-1]:
        await consumer.command_leave(id)
    assert _ids("leaving") == joined[:-1]

    await consumer.notification({"channel": TOPIC, "kwargs": {}})
    assert _ids("notified") == [leaves[0].id]
    assert [c.id for c in consumer._patch_book[(RetireLeaf._fqn, TOPIC)]] == [leaves[0].id]


async def test_a_leaf_the_drawer_hides_retires_and_the_topic_goes():
    consumer = await _page(RetireRoot)
    (leaf,) = _leaves(consumer)
    assert TOPIC in consumer.subscriptions

    await _event(consumer, "root", "toggle")

    assert _leaves(consumer) == []
    assert _ids("leaving") == [leaf.id]
    assert TOPIC not in consumer.subscriptions
    assert (RetireLeaf._fqn, TOPIC) not in consumer._patch_book


async def test_a_leaf_drawn_with_an_id_retires_when_hidden_and_comes_back_anew():
    consumer = await _page(RetireNamedRoot)
    (leaf,) = _leaves(consumer)
    leaf.note = "own"

    await _event(consumer, "root", "toggle")
    assert _ids("leaving") == ["named-leaf"]
    assert consumer.repo.get("named-leaf") is None

    await _event(consumer, "root", "toggle")
    (shown,) = _leaves(consumer)
    assert shown is not leaf
    assert shown.note == ""
    assert _ids("joined") == ["named-leaf", "named-leaf"]

    # Drawn again in every pass, it stays the one instance
    await _event(consumer, "root", "write", note="x")
    assert _leaves(consumer) == [shown]
    assert _ids("leaving") == ["named-leaf"]


async def test_what_a_retired_component_drew_retires_with_it():
    consumer = await _page(RetireDeepRoot)
    (leaf,) = _leaves(consumer)

    await _event(consumer, "root", "write", note="x")

    (now,) = _leaves(consumer)
    assert now is not leaf
    assert _ids("leaving") == [leaf.id]
    assert sum(isinstance(c, RetireMiddle) for c in consumer.repo.components.values()) == 1


async def test_the_slot_owner_s_own_render_keeps_the_leaf_its_filler_drew():
    consumer = await _page(RetireSlotRoot)
    (leaf,) = _leaves(consumer)

    await _event(consumer, "slot-owner", "bump")

    assert _leaves(consumer) == [leaf]
    assert _ids("leaving") == []


async def test_a_leaf_in_a_part_kept_for_a_reset_temporary_assign_stays():
    consumer = await _page(RetireKeptRoot)
    await _event(consumer, "root", "load")
    leaves = _leaves(consumer)
    assert [leaf.note for leaf in leaves] == ["a", "b"]

    # The rows reset: the loop keeps the leaves the page shows
    await _event(consumer, "root", "write", note="x")
    assert _leaves(consumer) == leaves
    assert _ids("leaving") == []

    # New rows draw new leaves over them
    await _event(consumer, "root", "load")
    assert [leaf.note for leaf in _leaves(consumer)] == ["a", "b"]
    assert sorted(_ids("leaving")) == sorted(leaf.id for leaf in leaves)


async def test_a_render_outside_an_event_drops_the_retired_leaf_s_topic():
    # A render of its own -- a task's result, a deferred call -- is not followed
    # by an event's subscription chores: the render that retired the leaf drops its topic
    consumer = await _page(RetireRoot)
    root = consumer.repo.get("root")
    assert TOPIC in consumer.subscriptions

    root.shown = False  # type: ignore[union-attr]
    await consumer.send_render(root)

    assert _leaves(consumer) == []
    assert TOPIC not in consumer.subscriptions


class RetireFuse(Component):
    armed: bool = False

    @property
    def fuse(self) -> str:
        if self.armed:
            raise RuntimeError("boom")
        return ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<s {% tag_header %}>{{ this.fuse }}</s>")


class RetireBoomRoot(Component):
    """Draws a middle with an id, which draws a leaf without one, and then a fuse it can arm."""

    note: str = ""
    boom: bool = False

    async def explode(self, note: str):
        self.note = note
        self.boom = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'RetireMiddle' id='middle' note=this.note %}"
            "{% component 'RetireFuse' id='fuse' armed=this.boom %}</p>"
        )


async def test_a_leaf_retired_by_a_render_that_raised_still_leaves_when_the_connection_ends():
    # The middle's pass within the root's retires the leaf it drew before; the
    # root's render raises after it, so no batch takes the leaf's leaving().
    # The connection's end does.
    consumer = await _page(RetireBoomRoot)
    (leaf,) = _leaves(consumer)

    await consumer.command_user_event("root", "explode", {}, {"note": "x"})
    assert consumer.repo.get(leaf.id) is None
    assert _ids("leaving") == []

    await consumer.stop()

    assert leaf.id in _ids("leaving")
