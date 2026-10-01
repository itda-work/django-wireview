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
from django.template import Template

from wireview import AsyncResult, Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import JOINED_SINCE
from wireview.core.state import sign_state

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


async def test_a_component_the_root_has_yet_to_draw_does_not_join_with_its_entries():
    # What a reconnect sends: the shelf's join, carrying the nest and the
    # sprig, and the nest's own join right behind it -- the page sends it once
    # the shelf is marked live, before the shelf's render, which does not draw
    # the nest, is patched in. That join built the nest from the shelf's entry
    # and the sprig from its own, and the page let the nest go a moment later:
    # once the work landed, both were drawn from their defaults.
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
        await communicator.send_json_to(
            {
                "command": "join",
                "payload": {"name": "JoinedLateNest", "state": nest, "children": {"j-late-sprig": sprig}, "ref": 2},
            }
        )
        await communicator.send_json_to({"command": "leave", "payload": {"id": "j-late-nest"}})
        # Answered once the server has handled what came before it
        poke = {"id": "j-late-nshelf", "command": "poke", "implicit_args": {}, "explicit_args": {}, "ref": 3}
        await communicator.send_json_to({"command": "user_event", "payload": poke})
        heard = await _until(communicator, "render")
        assert [m["payload"]["id"] for m in heard] == ["j-late-nshelf"], "nothing answers the nest's join"

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
        assert "fresh-note" not in adopted
    finally:
        JoinedLateShelf.landed.set()
        await communicator.disconnect()
