"""A nested component its drawer's pass stops drawing retires with that pass (#204).

``{% component %}`` without an id builds a new instance under a new
``rx-<uuid>`` in every pass of its drawer, so a drawer that renders again
leaves the one before behind. The page lets that element go and says so with a
leave, one round trip later, and a page that never says so (a socket of
another client, a leave lost with the connection) left it for good: it
stayed reachable, heard broadcasts, received patches and kept its tasks until
the connection ended, and ``leaving()`` came only then.

The end of the drawer's render retires it instead (``ComponentRepository.end_pass``):
it is removed, with what its own passes drew, and from then on nothing but the
page's events reaches it. Those the page sent before it saw that render still
run its handlers, until the page's leave says no more will come; its
``leaving()`` runs then (``Tombstone``). A page that never leaves it gets it
buried by the first render ``TOMBSTONE_SECONDS`` later, or with the connection.
A component drawn with an id that the pass hides by an ``{% if %}`` retires
the same way. One a part kept for a reset temporary assign still shows stays.
"""

import asyncio
import typing as t

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from django.test import override_settings

from wireview import Component, LiveComponent, function_component, repository
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

    async def explode(self):
        raise RuntimeError("boom")

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


class RetirePlacedRoot(Component):
    """Draws leaves without ids: one per row of a temporary assign, then one more, and a middle with its own (#212)."""

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
            "{% load wireview %}<p {% tag_header %}>"
            "{% for row in rows %}{% component 'RetireLeaf' note=row %}{% endfor %}"
            "{% component 'RetireLeaf' note=this.note %}{% component 'RetireMiddle' note=this.note %}</p>"
        )


class RetireGapRoot(RetirePlacedRoot):
    """Draws a leaf it can hide before the rows' leaves: hidden, it leaves a gap before what the loop keeps (#212)."""

    show: bool = True

    async def toggle(self):
        self.show = not self.show

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.show %}{% component 'RetireLeaf' note='shown' %}{% endif %}"
            "{% for row in rows %}{% component 'RetireLeaf' note=row %}{% endfor %}"
            "{% component 'RetireLeaf' note=this.note %}</p>"
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


async def _page(root: type[Component], *, channel_name: str | None = None, **fields: t.Any) -> WireviewConsumer:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=JOINED_SINCE, channel_name=channel_name)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.outbound = _Outbound()  # type: ignore[assignment]
    await _join(consumer, root(user=AnonymousUser(), wire=WireviewMeta(params={}), id="root", **fields))
    await _join_drawn(consumer)
    return consumer


async def _event(consumer: WireviewConsumer, id: str, command: str, **args: t.Any) -> None:
    """An event, and what the page does once its answer is on screen: the leaves, then the joins."""
    await consumer.command_user_event(id, command, {}, args)
    await _leave_dropped(consumer)
    await _join_drawn(consumer)


async def _leave_dropped(consumer: WireviewConsumer) -> None:
    """Leave what the latest render took off the page, as the page does: the retired ones kept for its events."""
    for id in list(consumer.repo._tombstones):
        await consumer.command_leave(id)


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

    task = leaf._async_tasks["wait"]
    await consumer.command_user_event("root", "toggle", {}, {})

    # Out of the repository with the render: no broadcast, patch or topic of it
    assert _leaves(consumer) == []
    assert consumer.repo.get(leaf.id) is None
    assert TOPIC not in consumer.subscriptions
    assert (RetireLeaf._fqn, TOPIC) not in consumer._patch_book
    await consumer.notification({"channel": TOPIC, "kwargs": {}})
    assert _ids("notified") == []
    # Its leaving() waits for the page to say it let the element go
    assert _ids("leaving") == []
    assert not task.done()

    await consumer.command_leave(leaf.id)

    assert _ids("leaving") == [leaf.id]
    await asyncio.wait([task], timeout=5)
    assert task.done()


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


def _places(consumer: WireviewConsumer) -> list[tuple[str, str, int, str]]:
    """Each shown component drawn without an id: its class, its drawer's, its number, its note."""
    return sorted(
        (type(c).__name__, type(c.wire.drawn_at[0]).__name__, c.wire.drawn_at[1], getattr(c, "note", ""))
        for c in consumer.repo.reachable_components()
        if c.wire.drawn_at is not None
    )


async def test_what_a_pass_draws_without_an_id_is_numbered_within_its_class_and_drawer():
    """In the order the pass draws them, a kept part's where the part is (#212)."""
    consumer = await _page(RetirePlacedRoot)
    assert _places(consumer) == [
        ("RetireLeaf", "RetireMiddle", 0, ""),
        ("RetireLeaf", "RetirePlacedRoot", 0, ""),
        ("RetireMiddle", "RetirePlacedRoot", 0, ""),
    ]

    await _event(consumer, "root", "load")
    loaded = _places(consumer)
    assert loaded == [
        ("RetireLeaf", "RetireMiddle", 0, ""),
        ("RetireLeaf", "RetirePlacedRoot", 0, "a"),
        ("RetireLeaf", "RetirePlacedRoot", 1, "b"),
        ("RetireLeaf", "RetirePlacedRoot", 2, ""),
        ("RetireMiddle", "RetirePlacedRoot", 0, ""),
    ]
    rows = [leaf for leaf in _leaves(consumer) if leaf.note in ("a", "b")]

    # The rows reset: the loop keeps its leaves in their places, and the leaf
    # drawn anew after them comes after them
    await _event(consumer, "root", "write", note="x")
    assert [leaf for leaf in _leaves(consumer) if leaf.note in ("a", "b")] == rows
    assert _places(consumer) == [
        ("RetireLeaf", "RetireMiddle", 0, "x"),
        ("RetireLeaf", "RetirePlacedRoot", 0, "a"),
        ("RetireLeaf", "RetirePlacedRoot", 1, "b"),
        ("RetireLeaf", "RetirePlacedRoot", 2, "x"),
        ("RetireMiddle", "RetirePlacedRoot", 0, "x"),
    ]
    # The middle's leaf is the new middle's
    [middle] = [c for c in consumer.repo.reachable_components() if isinstance(c, RetireMiddle)]
    [inner] = [leaf for leaf in _leaves(consumer) if leaf.wire.drawn_at[0] is not consumer.repo.get("root")]
    assert inner.wire.drawn_at[0] is middle


async def test_what_a_kept_part_shows_closes_the_gap_a_hidden_one_leaves():
    """The numbers are a function of what the drawer shows, kept or drawn anew (#212).

    Held to their numbers, the rows a reset temporary assign keeps left the
    hidden leaf's number free, and the first pass after a reconnect, which
    draws them all anew, numbered them otherwise.
    """
    consumer = await _page(RetireGapRoot)
    await _event(consumer, "root", "load")
    assert _places(consumer) == [
        ("RetireLeaf", "RetireGapRoot", 0, "shown"),
        ("RetireLeaf", "RetireGapRoot", 1, "a"),
        ("RetireLeaf", "RetireGapRoot", 2, "b"),
        ("RetireLeaf", "RetireGapRoot", 3, ""),
    ]
    rows = [leaf for leaf in _leaves(consumer) if leaf.note in ("a", "b")]

    # Hidden: the rows the loop keeps move up, the same instances
    await _event(consumer, "root", "toggle")
    assert [leaf for leaf in _leaves(consumer) if leaf.note in ("a", "b")] == rows
    assert _places(consumer) == [
        ("RetireLeaf", "RetireGapRoot", 0, "a"),
        ("RetireLeaf", "RetireGapRoot", 1, "b"),
        ("RetireLeaf", "RetireGapRoot", 2, ""),
    ]

    # Shown again: before them, where it is drawn
    await _event(consumer, "root", "toggle")
    assert [leaf for leaf in _leaves(consumer) if leaf.note in ("a", "b")] == rows
    assert _places(consumer) == [
        ("RetireLeaf", "RetireGapRoot", 0, "shown"),
        ("RetireLeaf", "RetireGapRoot", 1, "a"),
        ("RetireLeaf", "RetireGapRoot", 2, "b"),
        ("RetireLeaf", "RetireGapRoot", 3, ""),
    ]


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


async def test_a_leaf_a_hidden_live_component_drew_runs_an_event_the_page_sent_before():
    # The LiveComponent goes with its parent's render; the leaf it drew is kept
    # for the page's events like one its own drawer stopped drawing
    consumer = await _page(RetireLiveRoot)
    consumer.repo.vsn = GONE_SINCE
    (leaf,) = _leaves(consumer)
    await consumer.command_user_event("root", "toggle", {}, {})
    SENT.clear()

    await consumer.command_user_event(leaf.id, "poke", {}, {}, ref=5)

    assert _ids("poked") == [leaf.id]
    assert SENT == [("gone", {"id": leaf.id, "ref": 5})]
    assert _ids("leaving") == []
    await consumer.command_leave(leaf.id)
    assert _ids("leaving") == [leaf.id]


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
    # retired, and never leaves it. Its event runs on the retired instance,
    # nothing of it is rendered, and the answer ends its loading state; a page
    # that knows ``gone`` joins the element again
    consumer = await _page(RetireIgnoreRoot)
    consumer.repo.vsn = vsn
    (leaf,) = _leaves(consumer)
    await consumer.command_user_event("root", "write", {}, {"note": "x"})
    assert consumer.repo.get(leaf.id) is None
    SENT.clear()

    await consumer.command_user_event(leaf.id, "poke", {}, {}, ref=7)

    assert _ids("poked") == [leaf.id]
    if vsn >= GONE_SINCE:
        assert SENT == [("gone", {"id": leaf.id, "ref": 7})]
    else:
        assert SENT == [("render", {"id": leaf.id, "diff": None, "ref": 7})]

    # The join the page sends takes the id: the retired one leaves before the new one joins
    del HEARD[:]
    await _join(consumer, leaf)
    assert HEARD == [("leaving", leaf.id), ("joined", leaf.id)]
    joined = consumer.repo.get(leaf.id)
    assert joined is not None and joined is not leaf
    await consumer.command_user_event(leaf.id, "poke", {}, {}, ref=8)
    assert _ids("poked") == [leaf.id]
    assert consumer.repo._tombstones == {} and consumer.repo._entombed == {}


async def test_an_event_the_page_sent_before_the_drawer_s_render_runs_its_handler():
    # The page clicked the leaf before the root's render that draws a new one
    # reached it: the click is on its way while the server renders
    consumer = await _page(RetireRoot)
    consumer.repo.vsn = GONE_SINCE
    (old,) = _leaves(consumer)
    await consumer.command_user_event("root", "write", {}, {"note": "x"}, ref=1)
    SENT.clear()

    await consumer.command_user_event(old.id, "poke", {}, {}, ref=2)

    assert _ids("poked") == [old.id]
    # Nothing of the retired one is rendered; the page has its element going
    assert SENT == [("gone", {"id": old.id, "ref": 2})]
    assert _ids("leaving") == []

    # The page saw the render: its leave buries it, and nothing reaches it after
    await consumer.command_leave(old.id)
    assert _ids("leaving") == [old.id]
    SENT.clear()
    await consumer.command_user_event(old.id, "poke", {}, {}, ref=3)
    assert _ids("poked") == [old.id]
    assert SENT == [("gone", {"id": old.id, "ref": 3})]


async def test_a_retired_leaf_whose_handler_raises_is_buried_without_a_rejoin():
    consumer = await _page(RetireRoot)
    consumer.repo.vsn = GONE_SINCE
    (old,) = _leaves(consumer)
    await consumer.command_user_event("root", "write", {}, {"note": "x"})
    SENT.clear()

    await consumer.command_user_event(old.id, "explode", {}, {}, ref=4)

    # No error: there is nothing to join again
    assert SENT == [("gone", {"id": old.id, "ref": 4})]
    assert _ids("leaving") == [old.id]
    assert consumer.repo._tombstones == {} and consumer.repo._entombed == {}
    assert len(_leaves(consumer)) == 1


async def test_a_retired_leaf_the_page_never_leaves_is_buried_by_a_later_render(monkeypatch):
    # A page that keeps the element (wire-update="ignore") or lost the leave:
    # the first render TOMBSTONE_SECONDS after the one that retired it buries it
    consumer = await _page(RetireIgnoreRoot)
    now = [1000.0]
    consumer.repo.clock = lambda: now[0]
    (first,) = _leaves(consumer)
    await consumer.command_user_event("root", "write", {}, {"note": "a"})
    await _join_drawn(consumer)

    now[0] += repository.TOMBSTONE_SECONDS - 1
    await consumer.command_user_event("root", "write", {}, {"note": "b"})
    await _join_drawn(consumer)
    assert _ids("leaving") == []
    # One kept for each element the page joined, never more than those
    assert len(consumer.repo._tombstones) == 2

    now[0] += 1
    await consumer.command_user_event("root", "write", {}, {"note": "c"})
    assert _ids("leaving") == [first.id]
    assert len(consumer.repo._tombstones) == 2

    # The last one the page has yet to join: no leaving() for it
    await consumer.stop()
    assert len(_ids("leaving")) == 3
    assert consumer.repo._tombstones == {} and consumer.repo._entombed == {}


async def test_an_id_drawn_again_buries_the_retired_one_under_it():
    # Hidden and shown again before the page's leave came: the new instance
    # takes the id, and the one retired under it leaves with that render
    consumer = await _page(RetireNamedRoot)
    (leaf,) = _leaves(consumer)
    await consumer.command_user_event("root", "toggle", {}, {})
    assert consumer.repo.buried("named-leaf") is leaf

    await consumer.command_user_event("root", "toggle", {}, {})

    assert consumer.repo.buried("named-leaf") is None
    assert _ids("leaving") == ["named-leaf"]
    shown = consumer.repo.get("named-leaf")
    assert shown is not None and shown is not leaf


class RetireShowingMiddle(Component):
    show: bool = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.show %}{% component 'RetireLeaf' id='shown-leaf' %}{% endif %}</b>"
        )


class RetireHideBoomRoot(Component):
    """Hides the middle's leaf and raises after the middle's pass, in the same render."""

    boom: bool = False

    async def explode(self):
        self.boom = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'RetireShowingMiddle' id='middle' show=this.boom|yesno:'0,1'|add:0 %}"
            "{% component 'RetireFuse' id='fuse' armed=this.boom %}</p>"
        )


async def test_a_leaf_a_raised_render_stopped_drawing_survives_another_component_s_render():
    # The render that raised never reached the page. What its passes stopped
    # drawing goes back to its drawer: another component's render before the
    # page's join of the root does not retire it, and that join finds it
    consumer = await _page(RetireHideBoomRoot)
    await _join(consumer, RetireSlotOwner(user=AnonymousUser(), wire=WireviewMeta(params={}), id="other"))
    leaf = consumer.repo.get("shown-leaf")
    assert leaf is not None and leaf.wire.has_joined
    states = {
        id: [type(component).__name__, sign_state(component)]
        for id in ("middle", "fuse", "shown-leaf")
        if (component := consumer.repo.get(id)) is not None
    }
    root_state = sign_state(consumer.repo.get("root"))  # type: ignore[arg-type]

    await consumer.command_user_event("root", "explode", {}, {})
    assert [command for command, _ in SENT][-1] == "error"
    await consumer.command_user_event("other", "bump", {}, {})
    assert consumer.repo.get("shown-leaf") is leaf
    assert _ids("leaving") == []

    await consumer.command_join("RetireHideBoomRoot", root_state, children=states)

    assert consumer.repo.get("shown-leaf") is leaf
    assert leaf.wire.has_joined
    assert _ids("leaving") == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "command": "user_event",
            "payload": {"id": ["x"], "command": "poke", "implicit_args": {}, "explicit_args": {}},
        },
        {
            "command": "user_event",
            "payload": {"id": {"x": 1}, "command": "poke", "implicit_args": {}, "explicit_args": {}},
        },
        {"command": "leave", "payload": {"id": ["x"]}},
        {
            "command": "hook_event",
            "payload": {"component_id": ["x"], "hook_id": "h", "event": "e", "payload": {}},
        },
    ],
)
async def test_a_message_naming_a_component_by_what_is_not_a_string_is_dropped(payload, caplog):
    consumer = await _page(RetireRoot)
    SENT.clear()

    await consumer.handle_message(payload)

    assert SENT == []
    assert "not a string" in caplog.text


class RetireLiveFuse(LiveComponent):
    armed: bool = False

    @property
    def fuse(self) -> str:
        if self.armed:
            raise RuntimeError("boom")
        return ""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<s {% live_tag_header %}>{{ this.fuse }}</s>")


class RetireNamedLive(LiveComponent):
    """A LiveComponent that draws a leaf with an id."""

    async def joined(self):
        HEARD.append(("joined", self.id))

    async def leaving(self):
        HEARD.append(("leaving", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<u {% live_tag_header %}>{% component 'RetireLeaf' id='live-leaf' %}</u>")


class RetireChildBoomRoot(Component):
    """Hides a leaf and a LiveComponent, then arms a LiveComponent whose render raises: all in one render."""

    shown: bool = True
    boom: bool = False

    async def explode(self):
        self.shown = False
        self.boom = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'RetireLeaf' id='named-leaf' %}"
            "{% live_component 'RetireNamedLive' id='live' %}{% endif %}"
            "{% live_component 'RetireLiveFuse' id='live-fuse' armed=this.boom %}</p>"
        )


async def test_a_raised_render_of_a_live_component_retires_nothing_its_tree_stopped_drawing(monkeypatch):
    # The root's level retires the leaf and the LiveComponent its pass no
    # longer draws, then the render of another LiveComponent of that render
    # raises. Nothing of the render reached the page, which still shows them
    rejoined: list[str] = []

    async def join_again(self, component, ref=None):
        rejoined.append(component.id)

    consumer = await _page(RetireChildBoomRoot)
    leaf, live_leaf, live = (consumer.repo.get(id) for id in ("named-leaf", "live-leaf", "live"))
    assert leaf is not None and live_leaf is not None and live is not None
    monkeypatch.setattr(WireviewConsumer, "_join_again", join_again)
    HEARD.clear()

    await consumer.command_user_event("root", "explode", {}, {})

    # The recovery joins the root again, the LiveComponent's owner
    assert rejoined == ["root"]
    assert [consumer.repo.get(id) for id in ("named-leaf", "live-leaf", "live")] == [leaf, live_leaf, live]
    assert consumer.repo.buried("named-leaf") is None and consumer.repo.buried("live-leaf") is None
    assert HEARD == []


async def test_a_leaf_a_raised_render_of_a_live_component_stopped_drawing_is_the_one_the_page_joins_again():
    # The crash's recovery joins the root again from the state its element
    # carries: the leaves are the instances the page has, joined, and none of
    # them left. Retired, they came back as new instances the page's elements,
    # kept by its morph, never joined
    consumer = await _page(RetireChildBoomRoot)
    leaves = {id: consumer.repo.get(id) for id in ("named-leaf", "live-leaf")}
    states = {
        id: [type(component).__name__, sign_state(component)]
        for id in ("named-leaf", "live", "live-leaf", "live-fuse")
        if (component := consumer.repo.get(id)) is not None
    }
    root_state = sign_state(consumer.repo.get("root"))  # type: ignore[arg-type]
    HEARD.clear()

    await consumer.command_user_event("root", "explode", {}, {})
    assert [command for command, _ in SENT][-1] == "error"
    await consumer.command_join("RetireChildBoomRoot", root_state, children=states)

    for id, leaf in leaves.items():
        assert consumer.repo.get(id) is leaf and leaf is not None and leaf.wire.has_joined
    assert not set(_ids("leaving")) & set(leaves)
    # The LiveComponent its root's recovery took leaves once, and joins anew
    assert _ids("leaving") == ["live"] and _ids("joined") == ["live"]


class RetireTapLive(LiveComponent):
    async def tap(self):
        HEARD.append(("tapped", self.id))
        await self.send_to_parent("got", which=self.id)

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<u {% live_tag_header %}>c</u>")


class RetireHolder(Component):
    async def got(self, which: str):
        HEARD.append(("got", which))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>{% live_component 'RetireTapLive' id='tap-'|concat:this.id %}</b>"
        )


class RetireHolderRoot(RetireRoot):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}>{{ this.note }}{% component 'RetireHolder' %}</p>")


async def test_a_live_component_a_tombstone_keeps_reaches_its_parent_kept_with_it(monkeypatch):
    # The page clicked the LiveComponent of a holder its drawer's render just
    # replaced. The click runs, and what the handler sends its parent reaches
    # the parent, kept with it: the documented LiveComponent-to-parent way
    mail: list[tuple[str, dict[str, t.Any]]] = []

    async def send_to(self, _channel, _command, **kwargs):
        mail.append((_command, kwargs))

    monkeypatch.setattr(WireviewMeta, "send_to", send_to)
    consumer = await _page(RetireHolderRoot, channel_name="test-channel")
    consumer.repo.vsn = GONE_SINCE
    (holder,) = [c for c in consumer.repo.components.values() if isinstance(c, RetireHolder)]
    await consumer.command_user_event("root", "write", {}, {"note": "x"})
    assert consumer.repo.buried(holder.id) is holder
    mail.clear()
    SENT.clear()

    await consumer.command_user_event(f"tap-{holder.id}", "tap", {}, {}, ref=3)
    while mail:
        command, kwargs = mail.pop(0)
        await consumer.message_from_component({"command": command, "kwargs": kwargs})

    assert HEARD == [("tapped", f"tap-{holder.id}"), ("got", f"tap-{holder.id}")]
    # Nothing of the holder is rendered: the page let it go
    assert SENT == [("gone", {"id": f"tap-{holder.id}", "ref": 3})]
