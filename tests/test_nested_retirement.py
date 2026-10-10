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

from wireview import Component, LiveComponent, function_component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import GONE_SINCE, JOINED_SINCE
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TOPIC = "retire.nested"
HEARD: list[tuple[str, str]] = []
SENT: list[tuple[str, dict[str, t.Any]]] = []


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

    async def poke(self):
        HEARD.append(("poked", self.id))

    async def rename(self, note: str):
        self.note = note

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


class RetireKeptMiddle(Component):
    """Drawn by the root with an id; draws a leaf per row of a temporary assign it keeps."""

    class Meta:
        temporary_assigns = ["rows"]

    rows: list[str] = []
    note: str = ""
    count: int = 0

    async def load(self):
        self.rows = ["a", "b"]

    async def touch(self):
        self.count += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>{{ this.count }}"
            "{% for row in rows %}{% component 'RetireLeaf' note=this.note %}{% endfor %}</b>"
        )


class RetireKeptMiddleRoot(RetireRoot):
    """Draws the middle within its pass: the middle's reset rows are kept there too."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{{ this.note }}{% component 'RetireKeptMiddle' id='km' %}</p>"
        )


class RetireKeptSlotRoot(RetireRoot):
    """Fills the slot owner with the middle: the owner's own render draws it again."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component_block 'RetireSlotOwner' id='slot-owner' %}{% fill body %}"
            "{% component 'RetireKeptMiddle' id='km' %}"
            "{% endfill %}{% endcomponent %}</p>"
        )


class RetireBox(Component):
    draw: bool = False

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.draw %}{% component 'RetireLeaf' id='moving' %}{% endif %}</b>"
        )


class RetireMoveAfterRoot(RetireRoot):
    """Moves the id out of the box into its own pass, after the box's."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{% component 'RetireBox' id='box' draw=this.shown %}"
            "{% if not this.shown %}{% component 'RetireLeaf' id='moving' %}{% endif %}</p>"
        )


class RetireMoveBeforeRoot(RetireRoot):
    """Moves the id out of the box into its own pass, before the box's."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if not this.shown %}{% component 'RetireLeaf' id='moving' %}{% endif %}"
            "{% component 'RetireBox' id='box' draw=this.shown %}</p>"
        )


class RetireLive(LiveComponent):
    """A LiveComponent that draws a leaf without an id."""

    note: str = ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<u {% live_tag_header %}>{% component 'RetireLeaf' note=this.note %}</u>")


class RetireLiveRoot(RetireRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% live_component 'RetireLive' id='live' %}{% endif %}</p>"
        )


class RetireOwner(Component):
    """Drawn without an id; owns a LiveComponent that draws a leaf."""

    note: str = ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>{% live_component 'RetireLive' id='owned-'|concat:this.id %}</b>"
        )


class RetireOwnerRoot(RetireRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}>{% component 'RetireOwner' note=this.note %}</p>")


class RetireIgnoreRoot(RetireRoot):
    """Draws the leaf where the page keeps what it has: its drawer's new one never reaches the page."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{{ this.note }}"
            "<span wire-update='ignore'>{% component 'RetireLeaf' %}</span></p>"
        )


class _Outbound:
    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        SENT.append((command, payload))

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
    SENT.clear()
    yield
    HEARD.clear()
    SENT.clear()


async def _join(consumer: WireviewConsumer, component: Component) -> None:
    state = sign_state(component)
    await consumer.command_join(type(component).__name__, state, children={})


async def _join_drawn(consumer: WireviewConsumer) -> None:
    """Join what the latest render put on the page and the page has yet to join, as the page does."""
    while drawn := [
        c for c in consumer.repo.components.values() if not c.wire.has_joined and not isinstance(c, LiveComponent)
    ]:
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


async def test_a_leaf_a_render_that_raised_stopped_drawing_leaves_when_the_connection_ends():
    # The middle's pass within the root's stops drawing the leaf it drew
    # before; the root's render raises after it, so that render never reaches
    # the page and the leaf is still on it. It retires with a render that does,
    # or, as here, with the connection.
    consumer = await _page(RetireBoomRoot)
    (leaf,) = _leaves(consumer)

    await consumer.command_user_event("root", "explode", {}, {"note": "x"})
    assert consumer.repo.get(leaf.id) is leaf
    assert _ids("leaving") == []

    await consumer.stop()

    assert leaf.id in _ids("leaving")


async def test_a_leaf_a_kept_part_shows_stays_when_another_component_s_pass_draws_its_drawer():
    # The root draws the middle again within its pass: the middle's rows reset,
    # its loop keeps the leaves the page shows -- and so does the end of that pass
    consumer = await _page(RetireKeptMiddleRoot)
    await _event(consumer, "km", "load")
    leaves = _leaves(consumer)
    assert len(leaves) == 2

    await _event(consumer, "root", "write", note="x")
    await _event(consumer, "root", "write", note="y")

    assert _leaves(consumer) == leaves
    assert _ids("leaving") == []
    assert consumer.repo.get("km").wire.kept_components == {leaf.id for leaf in leaves}  # type: ignore[union-attr]


async def test_a_leaf_a_kept_part_shows_stays_when_the_slot_owner_draws_its_drawer_again():
    # The middle fills the slot owner's slot: the owner's own render draws it again
    consumer = await _page(RetireKeptSlotRoot)
    await _event(consumer, "km", "load")
    leaves = _leaves(consumer)
    assert len(leaves) == 2

    await _event(consumer, "slot-owner", "bump")
    await _event(consumer, "slot-owner", "bump")

    assert _leaves(consumer) == leaves
    assert _ids("leaving") == []


async def test_what_a_part_kept_before_named_does_not_hold_a_leaf_a_later_pass_draws_no_more():
    # The middle's own render keeps its loop. A leaf then renders something new
    # on its own, so the root's pass draws the middle's loop again -- over the
    # reset rows, without the leaves. What the middle's own render kept before
    # does not keep them: they are gone from the page.
    consumer = await _page(RetireKeptMiddleRoot)
    await _event(consumer, "km", "load")
    leaves = _leaves(consumer)
    assert len(leaves) == 2
    await _event(consumer, "km", "touch")
    assert _leaves(consumer) == leaves
    assert consumer.repo.get("km").wire.kept_components == {leaf.id for leaf in leaves}  # type: ignore[union-attr]
    await _event(consumer, leaves[0].id, "rename", note="moved")

    await _event(consumer, "root", "write", note="x")

    assert _leaves(consumer) == []
    assert sorted(_ids("leaving")) == sorted(leaf.id for leaf in leaves)


@pytest.mark.parametrize("root", [RetireMoveAfterRoot, RetireMoveBeforeRoot])
async def test_an_id_that_moves_out_of_its_drawer_within_one_render_stays_the_same_instance(root):
    # Whichever pass comes first, the render draws the id: nothing retires
    consumer = await _page(root)
    (leaf,) = _leaves(consumer)
    leaf.note = "own"

    await _event(consumer, "root", "toggle")
    assert _leaves(consumer) == [leaf]
    assert leaf.note == "own"

    await _event(consumer, "root", "toggle")
    assert _leaves(consumer) == [leaf]
    assert _ids("joined") == ["moving"]
    assert _ids("leaving") == []


async def test_a_leaf_a_live_component_drew_retires_with_the_live_component():
    consumer = await _page(RetireLiveRoot)
    (leaf,) = _leaves(consumer)

    await _event(consumer, "root", "toggle")

    assert consumer.repo.get("live") is None
    assert _leaves(consumer) == []
    assert _ids("leaving") == [leaf.id]
    await consumer.notification({"channel": TOPIC, "kwargs": {}})
    assert _ids("notified") == []


async def test_a_leaf_the_live_component_of_a_retired_component_drew_retires_with_it():
    consumer = await _page(RetireOwnerRoot)
    (first,) = _leaves(consumer)

    for n in range(3):
        await _event(consumer, "root", "write", note=f"n{n}")

    assert len(_leaves(consumer)) == 1
    assert first.id in _ids("leaving")
    assert len(_ids("leaving")) == 3


@pytest.mark.parametrize("vsn", [GONE_SINCE, JOINED_SINCE])
async def test_an_event_for_a_retired_leaf_the_page_kept_is_answered(vsn):
    # Under wire-update="ignore" the page keeps the leaf its drawer's render
    # retired. Its event runs nothing, and the answer ends its loading state;
    # a page that knows ``gone`` joins the element again
    consumer = await _page(RetireIgnoreRoot)
    consumer.repo.vsn = vsn
    (leaf,) = _leaves(consumer)
    await consumer.command_user_event("root", "write", {}, {"note": "x"})
    assert consumer.repo.get(leaf.id) is None
    SENT.clear()

    await consumer.command_user_event(leaf.id, "poke", {}, {}, ref=7)

    assert _ids("poked") == []
    if vsn >= GONE_SINCE:
        assert SENT == [("gone", {"id": leaf.id, "ref": 7})]
    else:
        assert SENT == [("render", {"id": leaf.id, "diff": None, "ref": 7})]

    # The join the page sends answers from then on
    await _join(consumer, leaf)
    await consumer.command_user_event(leaf.id, "poke", {}, {}, ref=8)
    assert _ids("poked") == [leaf.id]
