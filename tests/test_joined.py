"""``joined``: the server says a join has landed, everything joined() queued included (#112).

The render that answers a join goes out first; what joined() queued (a stream's
first page, a title) follows through the session's queue. A client that judged
the page on the render alone judged it too early -- infinite scroll saw an
empty list and asked for a second page. ``joined`` comes through the same
queue, after all of it, to a client that says it understands it.

A LiveComponent hears its own: a render that brings one in runs its joined(),
and the page has to know when that one's list is there too.
"""

import asyncio
import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.core.signing import BadSignature
from django.template import Template

from wireview import AsyncResult, Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import JOINED_SINCE
from wireview.core.state import sign_state, unsign_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]


class JoinedProbe(Component):
    async def joined(self):
        await self.push_title("queued in joined()")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}></p>")


class JoinedSprout(LiveComponent):
    async def joined(self):
        await self.push_title("queued in the child's joined()")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}></i>")


class JoinedParent(Component):
    sprouted: bool = False

    async def sprout(self):
        self.sprouted = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.sprouted %}{% live_component 'JoinedSprout' id='j-child' %}{% endif %}</p>"
        )


def _named(heard: list[dict[str, t.Any]]) -> list[str]:
    return [m["command"] + (f":{m['payload']['id']}" if m["command"] == "joined" else "") for m in heard]


async def _session(vsn: int, last: str, component: type[Component] = JoinedProbe, **fields) -> list[str]:
    """The commands a client hears for one join, in order: up to ``last`` (``joined:<id>`` for a
    ``joined``), then until nothing more comes.

    Each message up to ``last`` is waited for as long as it takes, so a slow
    runner cannot cut the list short. Only what follows ``last`` is judged by a
    short quiet window: that part is a check that nothing else comes, and no
    message can say so (#143).
    """
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={vsn}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        state = sign_state(component(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-1", **fields))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": component.__name__, "state": state, "children": {}}}
        )
        heard: list[dict[str, t.Any]] = []
        while not heard or _named(heard[-1:]) != [last]:
            heard.append(await communicator.receive_json_from(timeout=5))
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
        return _named(heard)
    finally:
        await communicator.disconnect()


async def test_joined_comes_after_everything_the_join_sent():
    assert await _session(JOINED_SINCE, last="joined:j-1") == ["render", "title", "joined:j-1"]


async def test_a_client_that_does_not_know_it_does_not_get_it():
    # No end signal for this client -- that is the point. The title is the last
    # thing joined() queues; the quiet window after it is where joined would be.
    assert await _session(JOINED_SINCE - 1, last="title") == ["render", "title"]


async def test_a_live_component_in_the_joins_render_hears_its_own_joined_first():
    # Its joined() ran during the parent's render and its ops follow that render
    assert await _session(JOINED_SINCE, last="joined:j-1", component=JoinedParent, sprouted=True) == [
        "render",
        "title",
        "joined:j-child",
        "joined:j-1",
    ]


def _sprout(ref: int) -> dict[str, t.Any]:
    payload = {"id": "j-1", "command": "sprout", "implicit_args": {}, "explicit_args": {}, "ref": ref}
    return {"command": "user_event", "payload": payload}


async def test_a_live_component_an_event_brings_in_hears_its_own_joined():
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        state = sign_state(JoinedParent(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-1"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedParent", "state": state, "children": {}, "ref": 1}}
        )
        while (await communicator.receive_json_from(timeout=5))["command"] != "joined":
            pass
        await communicator.send_json_to(_sprout(2))
        heard: list[dict[str, t.Any]] = []
        while not heard or heard[-1]["command"] != "joined":
            heard.append(await communicator.receive_json_from(timeout=5))
        assert _named(heard) == ["render", "title", "joined:j-child"]
        # The event's ref, as on the render: the page drops both while it waits
        # for a join that replaces the parent (#146)
        assert heard[0]["payload"]["ref"] == heard[-1]["payload"]["ref"] == 2
        assert "j-child" in heard[0]["payload"]["children"]
        # Its next render is not a first one: no second joined
        await communicator.send_json_to(_sprout(3))
        heard = [await communicator.receive_json_from(timeout=5)]
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
        assert _named(heard) == ["render"]
    finally:
        await communicator.disconnect()


async def test_a_client_that_does_not_know_it_does_not_get_a_live_components_either():
    assert await _session(JOINED_SINCE - 1, last="title", component=JoinedParent, sprouted=True) == [
        "render",
        "title",
    ]


class JoinedNested(Component):
    """An ordinary component a parent's render draws with ``{% component %}``."""

    left: t.ClassVar[list[str]] = []

    async def joined(self):
        await self.push_title("queued in the nested component's joined()")

    async def leaving(self):
        JoinedNested.left.append(self.id)

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% tag_header %}></b>")


class JoinedHost(Component):
    shown: bool = False

    async def show(self):
        self.shown = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'JoinedNested' id='j-nested' %}{% endif %}</p>"
        )


async def test_a_component_an_event_draws_is_joined_by_the_page_and_hears_its_own_joined():
    # The parent's template pass builds and mounts the nested component inline; its
    # joined() is not the pass's to run. The page joins the element it was drawn
    # into, and the server takes up the instance the pass left: one joined(), no
    # leaving(), and ``joined`` behind what joined() queued, as on any join.
    JoinedNested.left.clear()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        state = sign_state(JoinedHost(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-1"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedHost", "state": state, "children": {}, "ref": 1}}
        )
        while (await communicator.receive_json_from(timeout=5))["command"] != "joined":
            pass
        payload = {"id": "j-1", "command": "show", "implicit_args": {}, "explicit_args": {}, "ref": 2}
        await communicator.send_json_to({"command": "user_event", "payload": payload})
        heard = [await communicator.receive_json_from(timeout=5)]
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
        # The render that draws it, and nothing that says it joined: it has not
        assert _named(heard) == ["render"]
        assert "j-nested" in str(heard[0]["payload"]["diff"])

        nested = sign_state(JoinedNested(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-nested"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedNested", "state": nested, "children": {}, "ref": 3}}
        )
        heard = []
        while not heard or heard[-1]["command"] != "joined":
            heard.append(await communicator.receive_json_from(timeout=5))
        assert _named(heard) == ["render", "title", "joined:j-nested"]
        assert heard[-1]["payload"]["ref"] == 3
        # The instance the event's render built, taken up rather than replaced
        assert JoinedNested.left == []
    finally:
        await communicator.disconnect()


class JoinedSprig(LiveComponent):
    """A LiveComponent inside a nested component, with a field of its own."""

    note: str = "fresh-note"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}>{{ this.note }}</i>")


class JoinedBox(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% tag_header %}>{% live_component 'JoinedSprig' id='j-sprig' %}</b>")


class JoinedShelf(Component):
    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'JoinedBox' id='j-box' %}{% endif %}</p>"
        )


async def _until(communicator: WebsocketCommunicator, command: str) -> list[dict[str, t.Any]]:
    heard = [await communicator.receive_json_from(timeout=5)]
    while heard[-1]["command"] != command:
        heard.append(await communicator.receive_json_from(timeout=5))
    return heard


async def test_a_joins_restore_map_is_for_that_join_only():
    # The page joins a nested component with the signed states of the components
    # inside it -- after a reconnect, their current ones. The root's pass has built
    # them already, so nothing took those entries, and they stayed for the life of
    # the connection: the next instance built under the id, once an {% if %} showed
    # it again, started from the old one's state.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedShelf(**meta, id="j-shelf"))
        box = sign_state(JoinedBox(**meta, id="j-box"))
        sprig = ["JoinedSprig", sign_state(JoinedSprig(**meta, id="j-sprig", note="stale-note"))]

        def join_box(ref: int, children: dict[str, t.Any]) -> dict[str, t.Any]:
            return {"command": "join", "payload": {"name": "JoinedBox", "state": box, "children": children, "ref": ref}}

        # What a reconnect sends: the root's join with every state under it, then
        # the nested component's with the states under it
        children = {"j-box": ["JoinedBox", box], "j-sprig": sprig}
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedShelf", "state": shelf, "children": children, "ref": 1}}
        )
        await _until(communicator, "joined")
        await communicator.send_json_to(join_box(2, {"j-sprig": sprig}))
        assert "stale-note" in str(await _until(communicator, "joined")), "the root's pass restored it"

        toggle = {"id": "j-shelf", "command": "toggle", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 3}})
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-box"}})
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 4}})
        await _until(communicator, "render")
        # The page joins the box the render drew; nothing inside it was on the page
        await communicator.send_json_to(join_box(5, {}))
        shown_again = str(await _until(communicator, "joined"))

        assert "fresh-note" in shown_again, shown_again
        assert "stale-note" not in shown_again
    finally:
        await communicator.disconnect()


class JoinedLateSprig(LiveComponent):
    note: str = "fresh-note"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}>{{ this.note }}</i>")


class JoinedLateShelf(Component):
    """Draws its LiveComponent only once the work joined() starts has landed."""

    data: AsyncResult[str] | None = None
    landed: t.ClassVar[asyncio.Event]

    async def joined(self):
        # What the docs advise after a reconnect: start the work again
        self.data = await self.assign_async(self._load())

    async def _load(self) -> str:
        await JoinedLateShelf.landed.wait()
        return "ok"

    async def boom(self):
        raise RuntimeError("the shelf's handler raised on purpose")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}</p>"
        )


class JoinedSprigHost(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>{% live_component 'JoinedLateSprig' id='j-late-sprig' %}</p>"
        )


def _late_shelf_join(ref: int) -> dict[str, t.Any]:
    meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
    # The page as it was before the reconnect: the work had landed and the
    # sprig was on it with a state of its own
    shelf = sign_state(JoinedLateShelf(**meta, id="j-late-shelf", data=AsyncResult.success("ok")))
    sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
    payload = {"name": "JoinedLateShelf", "state": shelf, "children": {"j-late-sprig": sprig}, "ref": ref}
    return {"command": "join", "payload": payload}


async def test_a_live_component_a_later_render_draws_takes_up_what_the_join_carried():
    # joined() puts the result back to loading, so the join's render does not
    # draw the sprig; the render after the work lands does. The entry the join
    # carried for it is still there then, and the sprig comes back as it was.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.send_json_to(_late_shelf_join(1))
        joined = str(await _until(communicator, "joined"))
        assert "-note" not in joined, "the join's render does not draw the sprig"

        JoinedLateShelf.landed.set()
        drawn = str(await _until(communicator, "render"))

        assert "kept-note" in drawn, drawn
        assert "fresh-note" not in drawn
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


async def test_a_join_s_restore_map_goes_with_its_root():
    # The shelf leaves before its work lands, so it never draws the sprig. The
    # entry its join carried goes with it: a sprig another component draws
    # under the id later is a new instance and starts anew.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.send_json_to(_late_shelf_join(1))
        await _until(communicator, "joined")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-late-shelf"}})

        host = sign_state(JoinedSprigHost(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-host"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedSprigHost", "state": host, "children": {}, "ref": 2}}
        )
        drawn = str(await _until(communicator, "joined"))

        assert "fresh-note" in drawn, drawn
        assert "kept-note" not in drawn
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedFoldBox(Component):
    open: bool = True

    async def fold(self):
        self.open = not self.open

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.open %}{% live_component 'JoinedSprig' id='j-fold-sprig' %}{% endif %}</b>"
        )


class JoinedFoldShelf(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}>{% component 'JoinedFoldBox' id='j-fold-box' %}</p>")


async def test_a_nested_join_does_not_carry_what_the_outer_pass_built():
    # The box stays, so its entries would live as long as it does. The sprig
    # under it was built by the shelf's pass already, and its entry in the box's
    # join is not kept: the box folding the sprig away and back builds a new one,
    # which starts anew.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedFoldShelf(**meta, id="j-fold-shelf"))
        box = sign_state(JoinedFoldBox(**meta, id="j-fold-box"))
        sprig = ["JoinedSprig", sign_state(JoinedSprig(**meta, id="j-fold-sprig", note="stale-note"))]
        children = {"j-fold-box": ["JoinedFoldBox", box], "j-fold-sprig": sprig}
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedFoldShelf", "state": shelf, "children": children, "ref": 1}}
        )
        await _until(communicator, "joined")
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedFoldBox", "state": box, "children": {"j-fold-sprig": sprig}, "ref": 2},
            }
        )
        assert "stale-note" in str(await _until(communicator, "joined")), "the shelf's pass restored it"

        fold = {"id": "j-fold-box", "command": "fold", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**fold, "ref": 3}})
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "user_event", "payload": {**fold, "ref": 4}})
        shown_again = str(await _until(communicator, "render"))

        assert "fresh-note" in shown_again, shown_again
        assert "stale-note" not in shown_again
    finally:
        await communicator.disconnect()


async def test_a_crash_keeps_what_the_join_carried_for_the_rollback():
    # A handler of the shelf raises before its work lands. The rollback joins
    # it again from the element, which the join's render left without the
    # sprig, so the second join carries nothing for it. The crash had dropped
    # what the first join carried as if the shelf had left, and the sprig the
    # landed work draws started anew.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.send_json_to(_late_shelf_join(1))
        await _until(communicator, "joined")
        boom = {"id": "j-late-shelf", "command": "boom", "implicit_args": {}, "explicit_args": {}, "ref": 2}
        await communicator.send_json_to({"command": "user_event", "payload": boom})
        await _until(communicator, "error")

        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        loading = sign_state(JoinedLateShelf(**meta, id="j-late-shelf", data=AsyncResult.loading_state()))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedLateShelf", "state": loading, "children": {}, "ref": 3}}
        )
        await _until(communicator, "joined")
        JoinedLateShelf.landed.set()
        drawn = str(await _until(communicator, "render"))

        assert "kept-note" in drawn, drawn
        assert "fresh-note" not in drawn
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedLateNest(Component):
    """A plain component holding the sprig."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>{% live_component 'JoinedLateSprig' id='j-late-sprig' %}</b>"
        )


class JoinedLateNestShelf(Component):
    """Like ``JoinedLateShelf``, with the sprig in a plain component inside what the work draws."""

    data: AsyncResult[str] | None = None

    async def joined(self):
        self.data = await self.assign_async(self._load())

    async def _load(self) -> str:
        await JoinedLateShelf.landed.wait()
        return "ok"

    async def poke(self):
        pass

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedLateNest' id='j-late-nest' %}{% endif %}</p>"
        )


async def test_a_component_the_root_has_yet_to_draw_joins_without_its_entries():
    # What a reconnect sends: the shelf's join, carrying the nest and the
    # sprig, and the nest's own join right behind it -- the page sends it once
    # the shelf is marked live, before the shelf's render, which does not draw
    # the nest, is patched in. That join built the nest from the shelf's entry
    # and the sprig from its own, and the page let the nest go a moment later:
    # once the work landed, both were drawn from their defaults. The join goes
    # ahead -- the id may be a root of the page's own (tests/test_sticky_e2e.py)
    # -- with the states the page sent, and leaves the shelf's entries to the
    # shelf. The sprig's differs here only to tell the two apart.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedLateNestShelf(**meta, id="j-late-nshelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedLateNest(**meta, id="j-late-nest"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-late-nest": ["JoinedLateNest", nest], "j-late-sprig": sprig}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedLateNestShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        joined = str(await _until(communicator, "joined"))
        assert "-note" not in joined, "the join's render does not draw the nest"
        page = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedLateNest", "state": nest, "children": {"j-late-sprig": page}, "ref": 2},
            }
        )
        answered = str(await _until(communicator, "joined"))
        assert "page-note" in answered, "the nest's join draws the sprig as the page had it"
        assert "kept-note" not in answered, "the shelf's entry for the sprig stays the shelf's"
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-late-nest"}})

        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-late-nshelf":
            heard += await _until(communicator, "render")

        # The page joins the nest the landed render drew, and the nest's join
        # settles the sprig the render's pass built for it
        fresh = sign_state(JoinedLateNest(**meta, id="j-late-nest"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedLateNest", "state": fresh, "children": {}, "ref": 4}}
        )
        adopted = str(await _until(communicator, "joined"))
        assert "kept-note" in adopted, adopted
        assert "fresh-note" not in adopted and "page-note" not in adopted
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


def _signed(heard: t.Any, name: str) -> str:
    """The first signed state of ``name`` the messages carry: what the page's element holds after them."""
    if isinstance(heard, str):
        try:
            unsign_state(heard, name)
        except BadSignature:
            return ""
        return heard
    values = heard.values() if isinstance(heard, dict) else heard if isinstance(heard, list) else []
    return next((found for value in values if (found := _signed(value, name))), "")


class JoinedRaceNest(Component):
    """``JoinedLateNest`` with a field of its own."""

    label: str = "fresh-label"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>{{ this.label }}"
            "{% live_component 'JoinedLateSprig' id='j-late-sprig' %}</b>"
        )


class JoinedRaceShelf(JoinedLateNestShelf):
    """``JoinedLateNestShelf`` with an ``{% if %}`` of its own around the nest."""

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok and this.shown %}{% component 'JoinedRaceNest' id='j-race-nest' %}{% endif %}</p>"
        )


async def test_a_root_whose_work_lands_before_the_page_lets_the_nest_go_keeps_the_sprig():
    # As above, with the shelf's work landing after the nest's join and before
    # the page's leave for the nest reached the server. The shelf's render drew
    # the nest the page had joined, and the late leave took it with its
    # sprig. The page joins the nest the render drew without the sprig, which
    # it let go with the old element: the sprig comes back as it was. What the
    # shelf carried for the two is spent once its render drew them, and the
    # nest it shows again later starts anew.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedRaceShelf(**meta, id="j-late-nshelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedRaceNest(**meta, id="j-race-nest", label="kept-label"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-race-nest": ["JoinedRaceNest", nest], "j-late-sprig": sprig}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedRaceShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        await _until(communicator, "joined")
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedRaceNest", "state": nest, "children": {"j-late-sprig": sprig}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")

        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-late-nshelf":
            heard += await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-race-nest"}})
        assert "kept-note" in str(answered), "the nest's join drew the sprig as the page had it"
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedRaceNest",
                    "state": _signed(heard, "JoinedRaceNest"),
                    "children": {},
                    "ref": 4,
                },
            }
        )
        adopted = await _until(communicator, "joined")
        while adopted[-1]["payload"]["id"] != "j-race-nest":
            adopted += await _until(communicator, "joined")

        assert "kept-note" in str(adopted), adopted
        assert "fresh-note" not in str(adopted)
        assert "kept-label" in str(heard), "the shelf's render drew the nest the page joined"

        toggle = {"id": "j-late-nshelf", "command": "toggle", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 5}})
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-race-nest"}})
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 6}})
        shown_again = await _until(communicator, "render")
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedRaceNest",
                    "state": _signed(shown_again, "JoinedRaceNest"),
                    "children": {},
                    "ref": 7,
                },
            }
        )
        shown_again += await _until(communicator, "joined")
        while shown_again[-1]["payload"]["id"] != "j-race-nest":
            shown_again += await _until(communicator, "joined")
        assert "fresh-note" in str(shown_again) and "fresh-label" in str(shown_again), shown_again
        assert "kept-note" not in str(shown_again) and "kept-label" not in str(shown_again)
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


async def test_a_root_whose_work_lands_before_the_nest_joins_keeps_the_sprig():
    # The shelf's work lands before the nest's join right behind the shelf's
    # is handled: the shelf's render drew a new nest and restored it and the
    # sprig, and the nest's join took that nest up. The page's leave for the
    # nest it let go, patching the shelf's first render in, came after that and
    # took the new nest with its sprig. The page joined the nest the render
    # drew without the sprig, which it had let go with the old element, and
    # the sprig started from its defaults.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedRaceShelf(**meta, id="j-late-nshelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedRaceNest(**meta, id="j-race-nest", label="kept-label"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-race-nest": ["JoinedRaceNest", nest], "j-late-sprig": sprig}
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedRaceShelf", "state": shelf, "children": children, "ref": 1}}
        )
        await _until(communicator, "joined")
        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-late-nshelf":
            heard += await _until(communicator, "render")
        assert "kept-label" in str(heard), "the shelf's render drew the nest from its entry"

        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedRaceNest", "state": nest, "children": {"j-late-sprig": sprig}, "ref": 2},
            }
        )
        adopted = await _until(communicator, "joined")
        while adopted[-1]["payload"]["id"] != "j-race-nest":
            adopted += await _until(communicator, "joined")
        assert "kept-note" in str(adopted), adopted
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-race-nest"}})
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedRaceNest",
                    "state": _signed(heard, "JoinedRaceNest"),
                    "children": {},
                    "ref": 3,
                },
            }
        )
        again = await _until(communicator, "joined")
        while again[-1]["payload"]["id"] != "j-race-nest":
            again += await _until(communicator, "joined")

        assert "kept-note" in str(again), again
        assert "fresh-note" not in str(again)
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedToggleNest(Component):
    """Holds the sprig behind an ``{% if %}`` of its own."""

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.shown %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}</b>"
        )


class JoinedToggleShelf(JoinedLateNestShelf):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedToggleNest' id='j-toggle-nest' %}{% endif %}</p>"
        )


async def test_a_root_of_the_next_page_under_an_id_a_sticky_root_carried_starts_from_its_own_page():
    # A sticky shelf crosses a boosted navigation with the entries its join
    # carried for a nest and the sprig in it, and the next page draws the nest
    # as a root of its own. Its join drew the sprig from the props, not as the
    # page had it, and the sprig the nest showed again later took the shelf's
    # entry: the previous page's state.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedToggleShelf(**meta, id="j-toggle-shelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedToggleNest(**meta, id="j-toggle-nest"))
        kept = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-toggle-nest": ["JoinedToggleNest", nest], "j-late-sprig": kept}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedToggleShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        await _until(communicator, "joined")

        page = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedToggleNest", "state": nest, "children": {"j-late-sprig": page}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-toggle-nest":
            answered += await _until(communicator, "joined")
        assert "page-note" in str(answered), answered

        toggle = {"id": "j-toggle-nest", "command": "toggle", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 3}})
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 4}})
        shown_again = str(await _until(communicator, "render"))

        assert "fresh-note" in shown_again, shown_again
        assert "kept-note" not in shown_again
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedAsyncNest(Component):
    """Draws the sprig only once its own work, started again in joined(), lands."""

    data: AsyncResult[str] | None = None
    landed: t.ClassVar[asyncio.Event]

    async def joined(self):
        self.data = await self.assign_async(self._load())

    async def _load(self) -> str:
        await JoinedAsyncNest.landed.wait()
        return "ok"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.data.ok %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}</b>"
        )


class JoinedAsyncNestShelf(JoinedLateNestShelf):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedAsyncNest' id='j-async-nest' %}{% endif %}</p>"
        )


async def test_a_render_of_the_nest_before_the_page_lets_it_go_leaves_the_shelfs_entries():
    # The nest's join right behind its shelf's, and the nest's own work lands
    # before the page lets it go. That render drew the sprig after the join was
    # over, from the shelf's entry, and the shelf's render after its work drew
    # the sprig from the props.
    JoinedLateShelf.landed = asyncio.Event()
    JoinedAsyncNest.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedAsyncNestShelf(**meta, id="j-async-shelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedAsyncNest(**meta, id="j-async-nest", data=AsyncResult.success("ok")))
        kept = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-async-nest": ["JoinedAsyncNest", nest], "j-late-sprig": kept}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedAsyncNestShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        await _until(communicator, "joined")
        page = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedAsyncNest", "state": nest, "children": {"j-late-sprig": page}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-async-nest":
            answered += await _until(communicator, "joined")
        JoinedAsyncNest.landed.set()
        early = await _until(communicator, "render")
        assert "page-note" in str(early) and "kept-note" not in str(early), early
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-async-nest"}})

        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-async-shelf":
            heard += await _until(communicator, "render")
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedAsyncNest",
                    "state": _signed(heard, "JoinedAsyncNest"),
                    "children": {},
                    "ref": 5,
                },
            }
        )
        adopted = await _until(communicator, "joined")
        while adopted[-1]["payload"]["id"] != "j-async-nest":
            adopted += await _until(communicator, "joined")

        # Its first render draws the sprig from the shelf's entry; the work its
        # joined() starts again then draws a new one
        first = adopted[0]
        assert (first["command"], first["payload"]["id"]) == ("render", "j-async-nest")
        assert "kept-note" in str(first), first
        assert "page-note" not in str(adopted)
    finally:
        JoinedLateShelf.landed.set()
        JoinedAsyncNest.landed.set()
        await communicator.disconnect()


class JoinedLateCounter(Component):
    count: int = 0

    async def increment(self):
        self.count += 1

    @property
    def label(self) -> str:
        # One dynamic part, so a diff carries the whole text
        return f"count={self.count}"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% tag_header %}>{{ this.label }}</b>")


class JoinedLateCounterShelf(JoinedLateNestShelf):
    """Draws ``j-late-counter`` once its work lands."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedLateCounter' id='j-late-counter' %}{% endif %}</p>"
        )


async def test_a_join_under_an_id_another_root_carried_goes_ahead_and_leaves_the_entry():
    # A sticky root crosses a boosted navigation with the entry its join carried
    # for a component it has yet to draw, and the next page draws that id as a
    # root of its own. Its join was taken for a nested one's and dropped: no
    # answer, and every click on it went nowhere.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedLateCounterShelf(**meta, id="j-late-cshelf", data=AsyncResult.success("ok")))
        carried = ["JoinedLateCounter", sign_state(JoinedLateCounter(**meta, id="j-late-counter", count=5))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedLateCounterShelf",
                    "state": shelf,
                    "children": {"j-late-counter": carried},
                    "ref": 1,
                },
            }
        )
        assert "count=" not in str(await _until(communicator, "joined"))

        own = sign_state(JoinedLateCounter(**meta, id="j-late-counter", count=1))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedLateCounter", "state": own, "children": {}, "ref": 2}}
        )
        assert "count=1" in str(await _until(communicator, "joined"))
        increment = {"id": "j-late-counter", "command": "increment", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**increment, "ref": 3}})
        assert "count=2" in str(await _until(communicator, "render"))

        # The root goes; the shelf's later render draws the id from the shelf's entry
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-late-counter"}})
        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-late-cshelf":
            heard += await _until(communicator, "render")
        assert "count=5" in str(heard[-1]), heard[-1]
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedSprigPage(Component):
    """Draws ``j-late-sprig`` as a LiveComponent of its own, behind an ``{% if %}``."""

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<section {% tag_header %}>"
            "{% if this.shown %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}</section>"
        )


async def test_a_live_component_another_root_carried_starts_from_its_own_page():
    # A sticky shelf crosses a boosted navigation with the entry its join carried
    # for a sprig it has yet to draw, and the next page draws that id under a root
    # of its own. That root's join carries the sprig as the page has it, and it is
    # that state the answer draws, not the props. The shelf's entry goes: the
    # sprig the page shows again later is a new instance and starts anew, not
    # from the state the shelf carried from the previous page.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.send_json_to(_late_shelf_join(1))
        assert "-note" not in str(await _until(communicator, "joined"))

        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        page = sign_state(JoinedSprigPage(**meta, id="j-sprig-page"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedSprigPage", "state": page, "children": {"j-late-sprig": sprig}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-sprig-page":
            answered += await _until(communicator, "joined")
        assert "page-note" in str(answered), answered

        toggle = {"id": "j-sprig-page", "command": "toggle", "implicit_args": {}, "explicit_args": {}}
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 3}})
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "user_event", "payload": {**toggle, "ref": 4}})
        shown_again = str(await _until(communicator, "render"))

        assert "fresh-note" in shown_again, shown_again
        assert "kept-note" not in shown_again
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedParamsNest(Component):
    """Draws the sprig only once ``params_changed`` has run."""

    shown: bool = False

    async def params_changed(self, params, uri):
        self.shown = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.shown %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}</b>"
        )


class JoinedParamsShelf(JoinedLateNestShelf):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedParamsNest' id='j-params-nest' %}{% endif %}</p>"
        )


async def test_the_params_render_of_a_join_leaves_another_roots_entries_too():
    # As in the nest's join right behind its shelf above, with the URL carrying
    # params: the render params_changed brings answers the join too, and the
    # sprig it draws first is built from what the nest's join carried, not
    # from the shelf's entry.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedParamsShelf(**meta, id="j-params-shelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedParamsNest(**meta, id="j-params-nest"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-params-nest": ["JoinedParamsNest", nest], "j-late-sprig": sprig}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedParamsShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        assert "-note" not in str(await _until(communicator, "joined"))
        # The shelf hears it and has nothing to send
        await communicator.send_json_to({"command": "params_changed", "payload": {"params": {"p": "1"}, "uri": "?p=1"}})

        page = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedParamsNest", "state": nest, "children": {"j-late-sprig": page}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-params-nest":
            answered += await _until(communicator, "joined")

        assert "page-note" in str(answered), answered
        assert "kept-note" not in str(answered)
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()


class JoinedTally(LiveComponent):
    count: int = 0

    async def bump(self):
        self.count += 1

    @property
    def label(self) -> str:
        # One dynamic part, so a diff carries the whole text
        return f"tally={self.count}"

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}>{{ this.label }}</i>")


class JoinedTallyBox(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<b {% tag_header %}>{% live_component 'JoinedTally' id='j-tally' %}</b>")


class JoinedTallyFrame(Component):
    """Draws the box behind an ``{% if %}`` on the prop its own drawer passes."""

    shown: bool = True

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<s {% tag_header %}>"
            "{% if this.shown %}{% component 'JoinedTallyBox' id='j-tally-box' %}{% endif %}</s>"
        )


class JoinedTallyPage(Component):
    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component 'JoinedTallyFrame' id='j-tally-frame' shown=this.shown %}</p>"
        )


class JoinedTallyShelf(JoinedTallyPage):
    """Draws the box behind an ``{% if %}`` of its own: one level."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.shown %}{% component 'JoinedTallyBox' id='j-tally-box' %}{% endif %}</p>"
        )


def _event(id: str, command: str, ref: int) -> dict[str, t.Any]:
    payload = {"id": id, "command": command, "implicit_args": {}, "explicit_args": {}, "ref": ref}
    return {"command": "user_event", "payload": payload}


def _tallies(heard: t.Any) -> list[str]:
    """Every ``tally=`` text the messages draw, in order."""
    if isinstance(heard, str):
        return [heard] if heard.startswith("tally=") else []
    values = heard.values() if isinstance(heard, dict) else heard if isinstance(heard, list) else []
    return [found for value in values for found in _tallies(value)]


async def _tally_page(communicator: WebsocketCommunicator, *joins: tuple[type[Component], str]) -> None:
    """Join the root and the components the page joins under it, then the box, and bump the tally to 3."""
    meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
    nested = [*joins, (JoinedTallyBox, "j-tally-box")]
    for ref, (component, id) in enumerate(nested, start=1):
        state = sign_state(component(**meta, id=id))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": component.__name__, "state": state, "children": {}, "ref": ref}}
        )
        heard = await _until(communicator, "joined")
        while heard[-1]["payload"]["id"] != id:
            heard += await _until(communicator, "joined")
    assert _tallies(heard) == ["tally=0"], heard
    for ref in (11, 12, 13):
        await communicator.send_json_to(_event("j-tally", "bump", ref))
        bumped = await _until(communicator, "render")
    assert _tallies(bumped) == ["tally=3"], bumped


async def _join_box_again(communicator: WebsocketCommunicator, ref: int) -> list[dict[str, t.Any]]:
    """The page joins the box a render drew: the tally inside it went with the old element."""
    box = sign_state(JoinedTallyBox(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-tally-box"))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": "JoinedTallyBox", "state": box, "children": {}, "ref": ref}}
    )
    heard = await _until(communicator, "joined")
    while heard[-1]["payload"]["id"] != "j-tally-box":
        heard += await _until(communicator, "joined")
    return heard


async def test_a_live_component_two_component_levels_down_an_if_shows_again_starts_anew():
    # The page passes the frame a flag, and the frame draws the box behind it.
    # The page's render draws the frame within its own pass, which never told
    # the repository the frame no longer drew the box: the box's leave took it
    # for one the frame had drawn again, and kept the tally's state for it.
    # The box the page showed again brought the old tally back.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await _tally_page(communicator, (JoinedTallyPage, "j-tally-page"), (JoinedTallyFrame, "j-tally-frame"))

        await communicator.send_json_to(_event("j-tally-page", "toggle", 21))
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-tally-box"}})
        await communicator.send_json_to(_event("j-tally-page", "toggle", 22))
        await _until(communicator, "render")
        shown_again = await _join_box_again(communicator, 23)

        assert _tallies(shown_again) == ["tally=0"], shown_again
    finally:
        await communicator.disconnect()


@pytest.mark.parametrize(("late", "expected"), [(False, "tally=0"), (True, "tally=3")])
async def test_a_box_hidden_and_shown_again_before_its_leave_lands_keeps_its_tally(late, expected):
    # The shelf hides the box and shows it again. When the page's leave for the
    # hidden element comes between the two renders, the box shown again is a
    # new instance and starts anew. When it comes after both, the second render
    # has drawn the instance the page had, tally and all, and the leave cannot
    # be told from the one a reconnect's root sends late (the tests above): the
    # page joins the box with the tally the render drew.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await _tally_page(communicator, (JoinedTallyShelf, "j-tally-shelf"))

        await communicator.send_json_to(_event("j-tally-shelf", "toggle", 21))
        await _until(communicator, "render")
        if not late:
            await communicator.send_json_to({"command": "leave", "payload": {"id": "j-tally-box"}})
        await communicator.send_json_to(_event("j-tally-shelf", "toggle", 22))
        await _until(communicator, "render")
        if late:
            await communicator.send_json_to({"command": "leave", "payload": {"id": "j-tally-box"}})
        shown_again = await _join_box_again(communicator, 23)

        assert _tallies(shown_again) == [expected], shown_again
    finally:
        await communicator.disconnect()


class JoinedTallyOwner(Component):
    """Renders its slot, and renders on its own on a click: the frame in the slot is drawn outside the frame's pass."""

    clicks: int = 0

    async def click(self):
        self.clicks += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<section {% tag_header %}><u>{{ this.clicks }}</u>{% render_slot 'body' %}</section>"
        )


class JoinedTallySlotPage(JoinedTallyPage):
    """Passes the frame its flag from inside the owner's slot."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<main {% tag_header %}>"
            "{% component_block 'JoinedTallyOwner' id='j-tally-owner' %}{% fill body %}"
            "{% component 'JoinedTallyFrame' id='j-tally-frame' shown=this.shown %}"
            "{% endfill %}{% endcomponent %}</main>"
        )


class JoinedTallyBlockFrame(JoinedTallyFrame):
    """``JoinedTallyFrame`` with a slot: the page draws it with a block, so its pass is given slots."""

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<s {% tag_header %}>"
            "{% if this.shown %}{% component 'JoinedTallyBox' id='j-tally-box' %}{% endif %}"
            "{% render_slot 'body' %}</s>"
        )


class JoinedTallyBlockPage(JoinedTallyPage):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% component_block 'JoinedTallyBlockFrame' id='j-tally-frame' shown=this.shown %}"
            "{% fill body %}<em>x</em>{% endfill %}{% endcomponent %}</p>"
        )


async def test_a_frame_in_a_slot_whose_owner_drew_it_again_hides_the_box_by_the_pages_flag():
    # The owner's render on its own draws the frame in its slot outside any
    # pass of the frame's, and the box the frame drew there stays on record
    # as named. The frame's next pass, within the page's, has to start from
    # nothing, or that record says it drew the box it now hides: the box's
    # leave then keeps the tally's state for it.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await _tally_page(
            communicator,
            (JoinedTallySlotPage, "j-tally-page"),
            (JoinedTallyOwner, "j-tally-owner"),
            (JoinedTallyFrame, "j-tally-frame"),
        )

        await communicator.send_json_to(_event("j-tally-owner", "click", 20))
        await _until(communicator, "render")
        await communicator.send_json_to(_event("j-tally-page", "toggle", 21))
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-tally-box"}})
        await communicator.send_json_to(_event("j-tally-page", "toggle", 22))
        await _until(communicator, "render")
        shown_again = await _join_box_again(communicator, 23)

        assert _tallies(shown_again) == ["tally=0"], shown_again
    finally:
        await communicator.disconnect()


async def test_a_frame_a_block_draws_hides_the_box_by_the_pages_flag():
    # The page draws the frame with {% component_block %}: the frame's pass
    # within the page's is given slots, and ends there all the same.
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        await _tally_page(
            communicator, (JoinedTallyBlockPage, "j-tally-page"), (JoinedTallyBlockFrame, "j-tally-frame")
        )

        await communicator.send_json_to(_event("j-tally-page", "toggle", 21))
        await _until(communicator, "render")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-tally-box"}})
        await communicator.send_json_to(_event("j-tally-page", "toggle", 22))
        await _until(communicator, "render")
        shown_again = await _join_box_again(communicator, 23)

        assert _tallies(shown_again) == ["tally=0"], shown_again
    finally:
        await communicator.disconnect()


class JoinedFoldingNest(JoinedAsyncNest):
    """``JoinedAsyncNest`` that can fold the sprig away and back."""

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<b {% tag_header %}>"
            "{% if this.data.ok and this.shown %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}"
            "</b>"
        )


class JoinedFoldingNestShelf(JoinedLateNestShelf):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok %}{% component 'JoinedFoldingNest' id='j-folding-nest' %}{% endif %}</p>"
        )


async def test_an_entry_further_out_is_spent_by_the_pass_that_takes_one_nearer():
    # The nest's join and its shelf's both carry the sprig. The shelf's render
    # draws the nest the page joined, and the nest's later render draws the
    # sprig from the nest's own entry. The shelf's entry for it is spent too:
    # the sprig the nest folds away and back is a new instance and starts anew.
    JoinedLateShelf.landed = asyncio.Event()
    JoinedAsyncNest.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedFoldingNestShelf(**meta, id="j-folding-shelf", data=AsyncResult.success("ok")))
        nest = sign_state(JoinedFoldingNest(**meta, id="j-folding-nest", data=AsyncResult.success("ok")))
        kept = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        children = {"j-folding-nest": ["JoinedFoldingNest", nest], "j-late-sprig": kept}
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedFoldingNestShelf", "state": shelf, "children": children, "ref": 1},
            }
        )
        await _until(communicator, "joined")
        page = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedFoldingNest", "state": nest, "children": {"j-late-sprig": page}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-folding-nest":
            answered += await _until(communicator, "joined")
        assert "-note" not in str(answered), "the nest's work has yet to land"

        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-folding-shelf":
            heard += await _until(communicator, "render")
        JoinedAsyncNest.landed.set()
        drawn = await _until(communicator, "render")
        assert "page-note" in str(drawn) and "kept-note" not in str(drawn), drawn

        await communicator.send_json_to(_event("j-folding-nest", "toggle", 3))
        await _until(communicator, "render")
        await communicator.send_json_to(_event("j-folding-nest", "toggle", 4))
        shown_again = str(await _until(communicator, "render"))

        assert "fresh-note" in shown_again, shown_again
        assert "kept-note" not in shown_again
    finally:
        JoinedLateShelf.landed.set()
        JoinedAsyncNest.landed.set()
        await communicator.disconnect()


class JoinedFoldingShelf(JoinedLateShelf):
    """``JoinedLateShelf`` that can fold the sprig away and back."""

    shown: bool = True

    async def toggle(self):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<p {% tag_header %}>"
            "{% if this.data.ok and this.shown %}{% live_component 'JoinedLateSprig' id='j-late-sprig' %}{% endif %}"
            "</p>"
        )


async def test_a_live_component_a_pass_draws_again_spends_what_its_parent_carried():
    # A sticky shelf crosses a boosted navigation with the entry its join
    # carried for the sprig, and the next page draws the sprig first. Once the
    # shelf's work lands its render draws the sprig the page has: the shelf's
    # entry is spent, and the sprig it folds away and back starts anew.
    JoinedLateShelf.landed = asyncio.Event()
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={JOINED_SINCE}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        shelf = sign_state(JoinedFoldingShelf(**meta, id="j-folding-shelf", data=AsyncResult.success("ok")))
        kept = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="kept-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {
                    "name": "JoinedFoldingShelf",
                    "state": shelf,
                    "children": {"j-late-sprig": kept},
                    "ref": 1,
                },
            }
        )
        assert "-note" not in str(await _until(communicator, "joined"))

        page = sign_state(JoinedSprigPage(**meta, id="j-sprig-page"))
        sprig = ["JoinedLateSprig", sign_state(JoinedLateSprig(**meta, id="j-late-sprig", note="page-note"))]
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedSprigPage", "state": page, "children": {"j-late-sprig": sprig}, "ref": 2},
            }
        )
        answered = await _until(communicator, "joined")
        while answered[-1]["payload"]["id"] != "j-sprig-page":
            answered += await _until(communicator, "joined")
        assert "page-note" in str(answered), answered

        JoinedLateShelf.landed.set()
        heard = await _until(communicator, "render")
        while heard[-1]["payload"]["id"] != "j-folding-shelf":
            heard += await _until(communicator, "render")

        await communicator.send_json_to(_event("j-folding-shelf", "toggle", 3))
        await _until(communicator, "render")
        await communicator.send_json_to(_event("j-folding-shelf", "toggle", 4))
        shown_again = str(await _until(communicator, "render"))

        assert "fresh-note" in shown_again, shown_again
        assert "kept-note" not in shown_again
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()
