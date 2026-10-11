"""``Broadcast``: one patch, rendered once, written to every subscriber (#178).

docs/design/broadcast-patch.md §9-2 is the plan these follow: the builder,
byte-for-byte frames, the watched render, who receives one, what is never
reached, the order against a join's ``joined()`` operations, the dispatch trip,
mixed versions, the hold limit, ``wireview.testing`` and ``send()``.
"""

import asyncio
import json
import typing as t
from dataclasses import dataclass

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.template import Template, engines
from django.utils import timezone, translation
from testproj.waiting import eventually

from examples.todo.models import Item as TodoItem
from wireview import JS, Broadcast, Component, LiveComponent, ModelAction, abroadcast, mount, telemetry
from wireview.consumer import WireviewConsumer
from wireview.core import patches
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, UPDATE_ONLY_SINCE
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.core.transport import PATCH_TOPIC_MAX, PatchHub, set_broker, set_patch_hub
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TEMPLATES = {
    "bc/feed.html": '{% load wireview %}<ul {% tag_header %} wire-stream="items"></ul>',
    "bc/feed_item.html": '<li id="items-{{ item.pk }}">{{ item.text }}</li>',
    "bc/bind.html": '{% load wireview %}<li {% on "click" "remove" pk=item.pk %}>{{ item.text }}</li>',
    "bc/bind_missing.html": '{% load wireview %}<li {% on "click" "nope" %}>{{ item.text }}</li>',
    "bc/bind_myself.html": '{% load wireview %}<li {% on "click" "remove" myself=True %}>x</li>',
    "bc/this.html": "<li>{{ this.id }}</li>",
    "bc/user.html": "<li>{% if user.is_staff %}staff{% endif %}</li>",
    "bc/request.html": "<li>{{ request.path }}</li>",
    "bc/csrf.html": "<li>{% csrf_token %}</li>",
    "bc/querystring.html": '<li><a href="{% querystring page=2 %}">2</a></li>',
    "bc/perms.html": "<li>{% if perms.auth.add_user %}yes{% endif %}</li>",
    # {% if a and b %} takes an operand's exception for False: the read is caught after the render
    "bc/swallowed.html": "<li>{% if user.is_staff and item.text %}staff{% endif %}</li>",
    "bc/language.html": "{% load i18n tz %}{% get_current_language as L %}{% get_current_timezone as TZ %}"
    "<li>{{ L }} {{ TZ }}</li>",
    "bc/host.html": "{% load wireview %}<div {% tag_header %}>{% live_component 'BcLive' id='bc-live' %}</div>",
    "bc/live.html": '{% load wireview %}<ul {% live_tag_header %} wire-stream="items"></ul>',
    "bc/live_item.html": '<li id="items-{{ item.pk }}">{{ item.text }}</li>',
    "bc/updating-host.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% live_component 'BcUpdatedLive' id='bc-updated' revision=this.revision %}</div>"
    ),
    "bc/seed-host.html": (
        "{% load wireview %}<div {% tag_header %}>{% live_component 'BcSeedLive' id='bc-seed-live' %}</div>"
    ),
}


def _template(cls: type, template_name: str | None) -> t.Any:
    """The component's own template, or an item template as the loader hands one over (it takes a dict)."""
    if template_name is None:
        return Template(TEMPLATES[cls._meta.template_name])  # type: ignore[attr-defined]
    return engines["django"].from_string(TEMPLATES[template_name])


@dataclass
class Post:
    pk: int
    text: str


class BcFeed(Component):
    class Meta:
        template_name = "bc/feed.html"
        subscriptions = {"bc-feed"}

    async def remove(self, pk: int = 0, **_rest):
        await self.stream_delete("items", pk)

    async def edit(self, pk: int = 0, **_rest):
        await self.stream_insert("items", Post(pk, "edited"), update_only=True)
        await self.stream_delete("items", pk + 1)

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcFeedChild(BcFeed):
    """Draws the same list, so a Broadcast for BcFeed would fit it; it is not BcFeed."""


class BcOther(Component):
    class Meta:
        template_name = "bc/feed.html"
        subscriptions = {"bc-feed"}

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcDynamic(BcFeed):
    """Says its topics in get_subscriptions(): any topic may be its."""

    room: str = "room.42"

    def get_subscriptions(self) -> set[str]:
        return {self.room}


class BcQuiet(BcFeed):
    """Hears another topic: the sentinel that ends a test's reading."""

    class Meta:
        subscriptions = {"bc-quiet"}


class BcLive(LiveComponent):
    class Meta:
        template_name = "bc/live.html"
        subscriptions = {"bc-feed"}

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcHost(Component):
    class Meta:
        template_name = "bc/host.html"

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcSeeding(BcFeed):
    """Reads its list in joined(), and an item is committed after the read: the race of §4-3."""

    class Meta:
        subscriptions = {"bc-seed"}

    async def joined(self):
        await self.stream("items", [Post(1, "old")])
        await Broadcast(type(self), "bc-seed").stream_insert("items", Post(2, "new"), at=0).asend()


class BcSeedLive(LiveComponent):
    class Meta:
        template_name = "bc/live.html"
        subscriptions = {"bc-seed"}

    async def joined(self):
        await self.stream("items", [Post(1, "old")])
        await Broadcast(BcSeedLive, "bc-seed").stream_insert("items", Post(2, "new"), at=0).asend()

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcSeedHost(Component):
    class Meta:
        template_name = "bc/seed-host.html"

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcRefreshing(BcFeed):
    """Reads its list again in a handler, and an item is committed after the read: §4-3 outside joined()."""

    class Meta:
        subscriptions = {"bc-refresh"}

    async def refresh(self, **_rest):
        await self.stream("items", [Post(1, "old")])
        await Broadcast(BcRefreshing, "bc-refresh").stream_insert("items", Post(2, "new"), at=0).asend()

    async def refresh_while_reading(self, **_rest):
        async def posts():
            yield Post(1, "old")
            # Committed and published while the list is still being read
            await Broadcast(BcRefreshing, "bc-refresh").stream_insert("items", Post(2, "new"), at=0).asend()

        await self.stream("items", posts())


# What a test hands the server's handlers to wait on: a fresh asyncio.Event per test
WAIT: dict[str, asyncio.Event] = {}


class BcAppending(BcFeed):
    """Appends a page it reads, and an item of it is deleted or edited while it reads (#199).

    The insert goes through the connection's channel as a reset does: a patch
    written first used to be undone by it -- the delete found nothing and the
    insert brought the item back, the edit found nothing and the insert brought
    the item as it was.
    """

    class Meta:
        subscriptions = {"bc-append"}

    async def _page(self, change: str, wait: bool = False) -> t.AsyncIterator[Post]:
        yield Post(5, "five")
        # Another connection commits its change to 5 and publishes, while this page is read
        broadcast = Broadcast(BcAppending, "bc-append")
        if change == "delete":
            await broadcast.stream_delete("items", 5).asend()
        else:
            await broadcast.stream_insert("items", Post(5, "edited"), update_only=True).asend()
        if wait:
            # Until the page has the patch: nothing holds it
            await WAIT["patched"].wait()
        yield Post(6, "six")

    async def older(self, change: str = "delete", **_rest):
        await self.stream("items", self._page(change), reset=False)

    async def older_one_by_one(self, change: str = "delete", **_rest):
        async for post in self._page(change, wait=True):
            await self.stream_insert("items", post)

    async def edit_then_publish(self, **_rest):
        await self.stream_insert("items", Post(1, "edited"), update_only=True)
        await Broadcast(BcAppending, "bc-append").push_event("after").asend()
        # Behind the insert's mail
        await self.push_event("finished")


class BcLingering(BcFeed):
    """Streams, then goes on working while patches for it are published (#199).

    A stream() holds the component's patches until its operation is written,
    which the connection does once the handler returned: the cost of the hold.
    A stream_insert does not hold.
    """

    class Meta:
        subscriptions = {"bc-linger"}

    async def linger(self, how: str = "insert", published: int = 1, **_rest):
        if how == "insert":
            await self.stream_insert("items", Post(1, "one"))
        else:
            await self.stream("items", [Post(1, "one")], reset=how == "reset")
        for n in range(published):
            await Broadcast(BcLingering, "bc-linger").push_event("other", {"n": n}).asend()
        if how != "insert" and published <= patches.QUEUE_LIMIT:
            # With the connection, behind the stream operation
            await eventually(lambda: len(_holding(self)) == published)
            WAIT["held"].set()
        # A slow call, say, until the test lets the handler end
        await WAIT["end"].wait()
        await self.push_event("done")


class BcReplacing(BcFeed):
    """Replaces an operation whose stream() is on its way to a layer with a round trip (#199)."""

    class Meta:
        subscriptions = {"bc-replace"}

    async def poll(self, reset: bool = False, **_rest):
        await self.start_async("poll", self._append(reset))
        await WAIT["sending"].wait()
        await self.start_async("poll", self._idle())
        WAIT["sent"].set()
        await self.push_event("done")

    async def _append(self, reset: bool) -> None:
        await self.stream("items", [Post(1, "one")], reset=reset)

    async def _idle(self) -> None:
        return None


class BcRevocable(BcFeed):
    """Hears ``bc-revocable`` until a notification on ``bc-revoke`` says it may not any more.

    The topics come from state, so the connection keeps them only while the
    session reads get_subscriptions() again after the notification (#203).
    """

    _revoked: bool = False

    def get_subscriptions(self) -> set[str]:
        return {"bc-revoke"} if self._revoked else {"bc-revoke", "bc-revocable"}

    async def notification(self, channel: str, **kwargs):
        if channel == "bc-revoke":
            self._revoked = True
        await self.push_event("heard", {"channel": channel})
        self.skip_render()


def _holding(component: Component) -> list[str]:
    """The frames this component's connection holds for it now (white box: the session behind its gate)."""
    session = component.wire.patch_gate.hold.__self__  # type: ignore[union-attr]
    held = session._patch_held.get(id(component))
    return [] if held is None else held.frames


class BcTwice(BcFeed):
    """Resets its list twice in one handler, and an item is published while the second reads it."""

    class Meta:
        subscriptions = {"bc-twice"}

    async def refresh(self, **_rest):
        await self.stream("items", [Post(1, "old")])

        async def posts():
            yield Post(1, "old")
            await Broadcast(BcTwice, "bc-twice").stream_insert("items", Post(2, "new"), at=0).asend()
            # The patch is with the connection, held, while this reset still reads
            await eventually(lambda: _holding(self))

        await self.stream("items", posts())
        await self.push_event("finished")


class BcUpdatedLive(LiveComponent):
    """Resets its list in update(), then keeps working there past the hold's deadline."""

    revision: int = 0

    class Meta:
        template_name = "bc/live.html"
        subscriptions = {"bc-updated"}

    async def update(self, **assigns):
        await super().update(**assigns)
        if not self.revision:
            return
        await self.stream("items", [Post(1, "old")])
        await Broadcast(BcUpdatedLive, "bc-updated").stream_insert("items", Post(2, "new"), at=0).asend()
        await eventually(lambda: _holding(self))
        # Longer than HOLD_SECONDS, while the reset waits in the pending queue for the render
        await asyncio.sleep(patches.HOLD_SECONDS * 5)
        await self.push_event("finished")

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcUpdatingHost(Component):
    revision: int = 0

    class Meta:
        template_name = "bc/updating-host.html"

    async def refresh(self, **_rest):
        self.revision += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcSlowHandler(BcFeed):
    """Resets its list, then keeps working in the same handler past the hold's deadline."""

    class Meta:
        subscriptions = {"bc-slow"}

    async def refresh(self, **_rest):
        await self.stream("items", [Post(1, "old")])
        await Broadcast(BcSlowHandler, "bc-slow").stream_insert("items", Post(2, "new"), at=0).asend()
        await eventually(lambda: _holding(self))
        # The reset and its release wait in the channel until this returns
        await asyncio.sleep(patches.HOLD_SECONDS * 5)
        await self.push_event("finished")


class BcTwiceReading(BcFeed):
    """An item is published while the first of two resets reads its list."""

    class Meta:
        subscriptions = {"bc-twice-reading"}

    async def refresh(self, **_rest):
        async def posts():
            yield Post(1, "old")
            await Broadcast(BcTwiceReading, "bc-twice-reading").stream_insert("items", Post(2, "new"), at=0).asend()
            session = self.wire.patch_gate.hold.__self__  # type: ignore[union-attr]
            if isinstance(session, WireviewSession):
                await eventually(lambda: _holding(self))

        await self.stream("items", posts())
        await self.stream("items", [Post(1, "old")])
        await self.push_event("finished")


class BcBusy(BcFeed):
    """Resets its list in one handler; another keeps the connection busy until the test says."""

    class Meta:
        subscriptions = {"bc-busy"}

    async def refresh(self, **_rest):
        await self.stream("items", [Post(1, "old")])
        await Broadcast(BcBusy, "bc-busy").stream_insert("items", Post(2, "new"), at=0).asend()
        await eventually(lambda: _holding(self))
        await self.push_event("finished")

    async def busy(self, **_rest):
        await BUSY.wait()
        await self.push_event("idle")


BUSY = asyncio.Event()


class BcBusyAfterTwo(BcFeed):
    """Two resets; an item is published while the second reads. Then another handler keeps the connection busy."""

    busy_running: t.ClassVar[bool] = False

    class Meta:
        subscriptions = {"bc-busy-after"}

    async def refresh(self, **_rest):
        await self.stream("items", [Post(1, "old")])

        async def posts():
            yield Post(1, "old")
            await Broadcast(BcBusyAfterTwo, "bc-busy-after").stream_insert("items", Post(2, "new"), at=0).asend()
            await eventually(lambda: _holding(self))

        await self.stream("items", posts())
        await self.push_event("finished")

    async def busy(self, **_rest):
        type(self).busy_running = True
        await BUSY.wait()
        type(self).busy_running = False
        await self.push_event("idle")


class BcPublishingFirst(BcFeed):
    """joined() publishes an item before it resets its list."""

    class Meta:
        subscriptions = {"bc-first"}

    async def joined(self):
        await Broadcast(BcPublishingFirst, "bc-first").stream_insert("items", Post(2, "new"), at=0).asend()
        await self.stream("items", [Post(1, "old")])


class BcResetThenFail(BcFeed):
    async def joined(self):
        await self.stream("items", [])
        raise RuntimeError("joined went wrong after its reset")


class BcFailing(BcFeed):
    async def joined(self):
        raise RuntimeError("joined went wrong")


# --- helpers -----------------------------------------------------------------------------


def _state(cls: type[Component], id: str) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))


async def _connect(vsn: int = PROTOCOL_VERSION) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={vsn}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _join(communicator: WebsocketCommunicator, cls: type[Component], id: str) -> list[dict[str, t.Any]]:
    """Join and read up to its ``joined``: what the join wrote, in order."""
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": cls.__name__, "state": _state(cls, id), "children": {}}}
    )
    return await _until(communicator, lambda m: m["command"] == "joined" and m["payload"]["id"] == id)


async def _until(communicator: WebsocketCommunicator, last: t.Callable[[dict[str, t.Any]], bool]):
    heard = [await communicator.receive_json_from(timeout=5)]
    while not last(heard[-1]):
        heard.append(await communicator.receive_json_from(timeout=5))
    return heard


async def _patched_until_quiet(communicator: WebsocketCommunicator) -> list[dict[str, t.Any]]:
    """The frames written up to the sentinel: a push_event to the BcQuiet joined as ``quiet``.

    A connection writes the patches it takes in the order they came, so what a
    Broadcast sent before the sentinel wrote is all it wrote.
    """
    await Broadcast(BcQuiet, "bc-quiet").push_event("sentinel").asend()
    heard = await _until(communicator, lambda m: m["command"] == "push_event" and m["payload"]["event"] == "sentinel")
    return heard[:-1]


def _ids(frames: list[dict[str, t.Any]]) -> list[str]:
    return [frame["payload"].get("id", frame["payload"].get("component_id")) for frame in frames]


# --- the builder -------------------------------------------------------------------------


@pytest.mark.unit
class TestTheBuilder:
    def test_the_operations_go_in_the_order_they_were_added(self):
        frames = (
            Broadcast(BcFeed, "bc-feed")
            .push_event("first", {"n": 1})
            .stream_insert("items", Post(1, "a"))
            .stream_delete("items", 1)
            .js(JS().add_class("#x", "seen"))
            ._frames()
        )
        commands = [json.loads(patches.join(frame, "c"))["command"] for frame in frames]
        assert commands == ["push_event", "stream_op", "stream_op", "exec_js"]

    def test_the_target_is_a_component_class(self):
        with pytest.raises(TypeError):
            Broadcast(BcFeed(user=AnonymousUser(), wire=WireviewMeta(params={})), "bc-feed")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            Broadcast(Component, "bc-feed")

    @pytest.mark.parametrize("topic", ["", "x" * (PATCH_TOPIC_MAX + 1), "room:42", "방"])
    def test_a_topic_its_patch_group_cannot_be_named_for_raises(self, topic):
        with pytest.raises(ValueError):
            Broadcast(BcFeed, topic)

    def test_a_topic_the_target_never_subscribes_to_raises(self):
        with pytest.raises(ValueError, match="does not subscribe to 'bc-fed'"):
            Broadcast(BcFeed, "bc-fed")
        # Topics that get_subscriptions() decides are the instance's to say
        Broadcast(BcDynamic, "room.42")

    def test_the_longest_topic_names_a_group_every_layer_takes(self):
        topic = "x" * PATCH_TOPIC_MAX
        Broadcast(BcDynamic, topic)
        get_channel_layer().require_valid_group_name(f"wireview.patch.{topic}")

    def test_a_push_event_payload_that_is_not_json_raises_where_it_is_added(self):
        with pytest.raises(TypeError, match="JSON"):
            Broadcast(BcFeed, "bc-feed").push_event("e", {"when": object()})

    def test_the_message_names_the_handler_the_consumer_routes_it_to(self):
        assert patches.MESSAGE_TYPE == "wireview.patch"
        assert callable(WireviewSession.wireview_patch)


# --- byte for byte -----------------------------------------------------------------------


class _Recording:
    """An ``Outbound`` that keeps every frame as the consumer would write it."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.frames.append(await WireviewConsumer.encode_json({"command": command, "payload": payload}))

    async def send_text(self, text: str) -> None:
        self.frames.append(text)

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


async def _as_the_session_writes(sent: dict[str, t.Any]) -> str:
    """The frame a session writes for a component's own operation, as ``mount()`` recorded it."""
    session = WireviewSession(_Recording())  # type: ignore[arg-type]
    await session.start(session=SessionView.wrap(None), vsn=PROTOCOL_VERSION)
    kwargs = {key: value for key, value in sent.items() if key != "type"}
    await getattr(session, f"component_{sent['type']}")(**kwargs)
    return session.outbound.frames[0]  # type: ignore[attr-defined]


ODD_ID = 'bc-"odd"-\\-한'


@pytest.mark.parametrize(
    ("own", "broadcast"),
    [
        (
            lambda c: c.stream_insert("items", Post(7, '"따옴표" & <b>')),
            lambda b: b.stream_insert("items", Post(7, '"따옴표" & <b>')),
        ),
        (
            lambda c: c.stream_insert("items", Post(7, "x"), at=0, limit=50),
            lambda b: b.stream_insert("items", Post(7, "x"), at=0, limit=50),
        ),
        (
            lambda c: c.stream_insert("items", Post(7, "x"), update_only=True),
            lambda b: b.stream_insert("items", Post(7, "x"), update_only=True),
        ),
        (
            lambda c: c.stream_insert("items", Post(7, "x"), dom_id=lambda i: f"row-{i.pk}", template="bc/bind.html"),
            lambda b: b.stream_insert("items", Post(7, "x"), dom_id=lambda i: f"row-{i.pk}", template="bc/bind.html"),
        ),
        (lambda c: c.stream_delete("items", 7), lambda b: b.stream_delete("items", 7)),
        (lambda c: c.stream_delete("items", "row-7"), lambda b: b.stream_delete("items", "row-7")),
        (
            lambda c: c.push_event("posted", {"pk": 7, "text": "한글"}),
            lambda b: b.push_event("posted", {"pk": 7, "text": "한글"}),
        ),
        (lambda c: c.push_event("posted", hook_id="h1"), lambda b: b.push_event("posted", hook_id="h1")),
        (
            lambda c: c.push_js(JS().add_class("#items-7", "new").focus("#q")),
            lambda b: b.js(JS().add_class("#items-7", "new").focus("#q")),
        ),
    ],
)
@pytest.mark.parametrize("component_id", ["feed", ODD_ID])
@pytest.mark.asyncio
async def test_a_frame_is_the_one_the_session_would_have_written(own, broadcast, component_id):
    view = await mount(BcFeed, id=component_id)
    view.clear_messages()
    await own(view.component)
    expected = await _as_the_session_writes(view.sent_messages[0])

    (frame,) = await patches_frames(broadcast(Broadcast(BcFeed, "bc-feed")))

    assert patches.join(frame, component_id) == expected


async def patches_frames(builder: Broadcast) -> list[list[str]]:
    from wireview.utils import db

    return await db(builder._frames)()


# --- the watched render ------------------------------------------------------------------


@pytest.mark.unit
class TestTheItemReadsNothingOfTheViewer:
    @pytest.mark.parametrize(
        ("template", "name"),
        [
            ("bc/this.html", "this"),
            ("bc/user.html", "user"),
            ("bc/request.html", "request"),
            ("bc/csrf.html", "csrf_token"),
            # The page's query, which Django's tag reads at context.request (#210)
            ("bc/querystring.html", "request"),
            ("bc/perms.html", "perms"),
            ("bc/bind_myself.html", "this"),
            ("bc/swallowed.html", "user"),
        ],
    )
    def test_reading_the_viewer_raises_and_names_what_was_read(self, template, name):
        with pytest.raises(ImproperlyConfigured, match=f"read '{name}'"):
            Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), template=template)._frames()

    def test_an_update_only_item_is_watched_the_same(self):
        """The item #199's ``update_only`` replaces is drawn once for every viewer too: no one's CSRF token (#206)."""
        builder = Broadcast(BcFeed, "bc-feed").stream_insert(
            "items", Post(1, "x"), template="bc/csrf.html", update_only=True
        )
        with pytest.raises(ImproperlyConfigured, match="read 'csrf_token'"):
            builder._frames()

    def test_the_item_alone_renders(self):
        (frame,) = Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"))._frames()
        assert json.loads(patches.join(frame, "f"))["payload"]["items"][0]["html"] == '<li id="items-1">x</li>'

    def test_a_binding_is_checked_against_the_target_class(self):
        (frame,) = Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), template="bc/bind.html")._frames()
        assert "wire-on-click" in json.loads(patches.join(frame, "f"))["payload"]["items"][0]["html"]
        with pytest.raises(AssertionError, match="BcFeed.nope"):
            Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), template="bc/bind_missing.html")._frames()

    def test_it_renders_in_the_projects_language_and_time_zone_not_the_publishers(self):
        builder = Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), template="bc/language.html")
        with translation.override("ko"), timezone.override("Asia/Seoul"):
            (frame,) = builder._frames()
        assert json.loads(patches.join(frame, "f"))["payload"]["items"][0]["html"] == "<li>en-us UTC</li>"


# --- who receives one --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_the_target_class_on_its_topic_receives_it():
    communicator = await _connect()
    try:
        await _join(communicator, BcQuiet, "quiet")
        for cls, id in ((BcFeed, "feed-a"), (BcFeed, "feed-b"), (BcFeedChild, "child"), (BcOther, "other")):
            await _join(communicator, cls, id)
        await Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), at=0).asend()
        await Broadcast(BcDynamic, "bc-other-topic").push_event("unheard").asend()
        heard = await _patched_until_quiet(communicator)
    finally:
        await communicator.disconnect()

    assert [frame["command"] for frame in heard] == ["stream_op", "stream_op"]
    assert sorted(_ids(heard)) == ["feed-a", "feed-b"]


@pytest.mark.asyncio
async def test_a_live_component_is_a_target_too():
    communicator = await _connect()
    try:
        await _join(communicator, BcQuiet, "quiet")
        await _join(communicator, BcHost, "host")
        await Broadcast(BcLive, "bc-feed").push_event("posted").asend()
        heard = await _patched_until_quiet(communicator)
    finally:
        await communicator.disconnect()

    assert _ids(heard) == ["bc-live"]


@pytest.mark.asyncio
async def test_a_failed_join_and_a_component_that_left_receive_nothing():
    communicator = await _connect()
    try:
        await _join(communicator, BcQuiet, "quiet")
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "BcFailing", "state": _state(BcFailing, "failing"), "children": {}}}
        )
        await _until(communicator, lambda m: m["command"] == "error")
        await _join(communicator, BcFeed, "gone")
        await communicator.send_json_to({"command": "leave", "payload": {"id": "gone"}})
        await _join(communicator, BcFeed, "here")
        await Broadcast(BcFailing, "bc-feed").push_event("posted").asend()
        await Broadcast(BcFeed, "bc-feed").push_event("posted").asend()
        heard = await _patched_until_quiet(communicator)
    finally:
        await communicator.disconnect()

    assert _ids(heard) == ["here"]


@pytest.mark.asyncio
async def test_a_topic_given_up_in_a_notification_is_heard_no_more():
    """The session reads get_subscriptions() again after it delivered a notification (#203).

    Nothing else on the connection wants the topic, so the connection leaves its
    group: neither a notification nor a Broadcast on it reaches the page after.
    """
    communicator = await _connect()
    try:
        await _join(communicator, BcQuiet, "quiet")
        await _join(communicator, BcRevocable, "revocable")
        await Broadcast(BcRevocable, "bc-revocable").push_event("before").asend()
        before = await _patched_until_quiet(communicator)

        await abroadcast("bc-revoke")
        await _until(communicator, lambda m: m["command"] == "push_event" and m["payload"]["event"] == "heard")
        await abroadcast("bc-revocable")
        await Broadcast(BcRevocable, "bc-revocable").push_event("after").asend()
        after = await _patched_until_quiet(communicator)
    finally:
        await communicator.disconnect()

    assert [frame["payload"]["event"] for frame in before] == ["before"]
    assert after == []


@pytest.mark.asyncio
async def test_a_stale_book_writes_nothing_to_what_is_no_longer_reachable():
    session = WireviewSession(_Recording())  # type: ignore[arg-type]
    await session.start(session=SessionView.wrap(None))
    feed = await session.repo.join("BcFeed", {"id": "feed"})
    await session.after_mutation_chores()
    # Gone without the book settling again, as a failed join leaves it
    session.repo.remove("feed")
    assert session._patch_book[(BcFeed._fqn, "bc-feed")] == [feed]

    (frame,) = Broadcast(BcFeed, "bc-feed").push_event("posted")._frames()
    await session.wireview_patch(
        {"type": "wireview.patch", "target": BcFeed._fqn, "topic": "bc-feed", "frames": [frame]}
    )

    assert session.outbound.frames == []  # type: ignore[attr-defined]


# --- what an older client is not sent ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("vsn", "sent"), [(UPDATE_ONLY_SINCE - 1, False), (UPDATE_ONLY_SINCE, True)])
async def test_an_update_only_patch_reaches_only_a_client_that_honours_it(vsn, sent):
    """An older client would insert the item where the page does not show it (#199).

    The frames are the same bytes for every subscriber: the one that carries
    ``update_only`` names the version it needs, and the delete after it, which
    every client reads alike, goes to both.
    """
    communicator = await _connect(vsn)
    try:
        await _join(communicator, BcQuiet, "quiet")
        await _join(communicator, BcFeed, "feed")
        await (
            Broadcast(BcFeed, "bc-feed")
            .stream_insert("items", Post(7, "x"), update_only=True)
            .stream_delete("items", 8)
            .asend()
        )
        heard = await _patched_until_quiet(communicator)
    finally:
        await communicator.disconnect()

    expected = [("insert", ["items-7"]), ("delete", ["items-8"])] if sent else [("delete", ["items-8"])]
    assert _stream_ops(heard) == expected
    assert all("update_only" in m["payload"] for m in heard if m["payload"]["op"] == "insert")


@pytest.mark.asyncio
@pytest.mark.parametrize(("vsn", "sent"), [(UPDATE_ONLY_SINCE - 1, False), (UPDATE_ONLY_SINCE, True)])
async def test_a_components_own_update_only_insert_reaches_only_a_client_that_honours_it(vsn, sent):
    communicator = await _connect(vsn)
    try:
        await _join(communicator, BcFeed, "feed")
        await _event(communicator, "feed", "edit")
        heard = await _until(communicator, lambda m: m["command"] == "stream_op" and m["payload"]["op"] == "delete")
    finally:
        await communicator.disconnect()

    expected = [("insert", ["items-0"]), ("delete", ["items-1"])] if sent else [("delete", ["items-1"])]
    assert _stream_ops(heard) == expected


def test_a_frame_names_the_version_it_needs_only_when_it_needs_one():
    insert, edit, delete, event = (
        Broadcast(BcFeed, "bc-feed")
        .stream_insert("items", Post(1, "a"))
        .stream_insert("items", Post(1, "b"), update_only=True)
        .stream_delete("items", 1)
        .push_event("posted")
        ._frames()
    )
    assert [len(frame) for frame in (insert, edit, delete, event)] == [2, 3, 2, 2]
    assert edit[2] == UPDATE_ONLY_SINCE
    assert [patches.reaches(edit, vsn) for vsn in (0, UPDATE_ONLY_SINCE - 1, UPDATE_ONLY_SINCE)] == [False, False, True]
    assert patches.reaches(insert, 0)


# --- the order against joined() ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_patch_published_during_joined_is_written_after_its_stream_reset():
    """joined() read its list, and an item committed after the read was published: it must not be wiped."""
    communicator = await _connect()
    try:
        heard = await _join(communicator, BcSeeding, "seed")
    finally:
        await communicator.disconnect()

    ops = [
        (m["payload"]["op"], [item["id"] for item in m["payload"]["items"]])
        for m in heard
        if m["command"] == "stream_op"
    ]
    assert ops == [("reset", ["items-1"]), ("insert", ["items-2"])]


@pytest.mark.asyncio
async def test_so_is_one_published_during_a_live_components_joined():
    communicator = await _connect()
    try:
        # The LiveComponent's operations, its patches and its joined go out before its host's joined
        heard = await _join(communicator, BcSeedHost, "seed-host")
    finally:
        await communicator.disconnect()

    ops = [(m["payload"]["op"], m["payload"]["id"]) for m in heard if m["command"] == "stream_op"]
    assert ops == [("reset", "bc-seed-live"), ("insert", "bc-seed-live")]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["refresh", "refresh_while_reading"])
async def test_a_patch_published_after_a_handlers_read_is_written_after_its_stream_reset(handler):
    """The reset goes through the connection's channel; the patch used to be written first and wiped by it."""
    communicator = await _connect()
    try:
        await _join(communicator, BcRefreshing, "refresh")
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {"id": "refresh", "command": handler, "implicit_args": {}, "explicit_args": {}, "ref": 1},
            }
        )
        ops: list[tuple[str, list[str]]] = []

        def two_ops(message: dict[str, t.Any]) -> bool:
            if message["command"] == "stream_op":
                ops.append((message["payload"]["op"], [item["id"] for item in message["payload"]["items"]]))
            return len(ops) == 2

        await _until(communicator, two_ops)
    finally:
        await communicator.disconnect()

    assert ops == [("reset", ["items-1"]), ("insert", ["items-2"])]


def _shown(ops: t.Iterable[dict[str, t.Any]]) -> dict[str, str]:
    """What a page that showed ``items-1`` shows after ``ops``, applied in order: id to HTML."""
    shown = {"items-1": "<li>one</li>"}
    for op in ops:
        items = {item["id"]: item["html"] for item in op["items"]}
        if op["op"] == "reset":
            shown = items
        elif op["op"] == "delete":
            shown = {key: html for key, html in shown.items() if key not in items}
        else:
            shown |= {key: html for key, html in items.items() if key in shown or not op.get("update_only")}
    return shown


def _assert_change_kept(shown: dict[str, str], change: str) -> None:
    if change == "delete":
        assert "items-5" not in shown, shown
    else:
        assert "edited" in shown["items-5"], shown
    assert "items-6" in shown


async def _appended(
    communicator: WebsocketCommunicator, handler: str, change: str, count: int
) -> list[dict[str, t.Any]]:
    """Call ``handler`` of the BcAppending joined as ``append``: its first ``count`` stream operations."""
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {
                "id": "append",
                "command": handler,
                "implicit_args": {},
                "explicit_args": {"change": change},
                "ref": 1,
            },
        }
    )
    ops: list[dict[str, t.Any]] = []

    def all_ops(message: dict[str, t.Any]) -> bool:
        if message["command"] == "stream_op":
            ops.append(message["payload"])
            if handler == "older_one_by_one" and len(ops) == 1:
                WAIT["patched"].set()
        return len(ops) == count

    await _until(communicator, all_ops)
    return ops


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "edit"])
async def test_a_patch_published_during_an_appended_read_is_written_after_the_insert(change):
    communicator = await _connect()
    try:
        await _join(communicator, BcAppending, "append")
        # The page in one frame, and the patch
        ops = await _appended(communicator, "older", change, 2)
    finally:
        await communicator.disconnect()

    # The patch last, behind what the page read
    assert ops[-1]["items"][0]["id"] == "items-5"
    assert ops[-1]["op"] == ("delete" if change == "delete" else "insert")
    _assert_change_kept(_shown(ops), change)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "edit"])
async def test_a_page_inserted_one_by_one_does_not_keep_a_change_made_while_it_was_read(change):
    """Why a page read and appended goes to stream(queryset, reset=False) (#199).

    stream_insert does not hold the patches: the change is written while the
    handler still reads, and the inserts the connection writes after the handler
    bring the item back as the page read it.
    """
    WAIT["patched"] = asyncio.Event()
    communicator = await _connect()
    try:
        await _join(communicator, BcAppending, "append")
        # The patch, then one insert each
        ops = await _appended(communicator, "older_one_by_one", change, 3)
    finally:
        await communicator.disconnect()

    assert [op["items"][0]["id"] for op in ops] == ["items-5", "items-5", "items-6"]
    shown = _shown(ops)
    assert shown["items-5"] == '<li id="items-5">five</li>', shown


async def _lingering(communicator: WebsocketCommunicator, how: str, published: int = 1) -> None:
    await _join(communicator, BcLingering, "linger")
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {
                "id": "linger",
                "command": "linger",
                "implicit_args": {},
                "explicit_args": {"how": how, "published": published},
                "ref": 1,
            },
        }
    )


def _written(heard: list[dict[str, t.Any]]) -> list[str]:
    return [
        m["command"] if m["command"] == "stream_op" else m["payload"]["event"]
        for m in heard
        if m["command"] in ("stream_op", "push_event")
    ]


@pytest.mark.asyncio
async def test_a_patch_published_while_a_handler_goes_on_after_its_stream_insert_is_written_meanwhile():
    """An insert does not hold the component's patches: they are not late behind the rest of the handler (#199)."""
    WAIT["end"] = asyncio.Event()
    communicator = await _connect()
    try:
        await _lingering(communicator, "insert")
        # The handler waits for the test, which waits for the patch
        meanwhile = await _until(communicator, lambda m: m["command"] == "push_event")
        WAIT["end"].set()
        after = await _until(communicator, lambda m: m["command"] == "push_event")
    finally:
        WAIT["end"].set()
        await communicator.disconnect()

    assert _written(meanwhile) == ["other"]
    # The insert goes out with the handler's other operations, once it returned
    assert _written(after) == ["stream_op", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["append", "reset"])
async def test_a_patch_published_while_a_handler_goes_on_after_its_stream_waits_for_the_handler(how):
    """The cost of the hold: the patch is written behind the stream operation, once the handler returned (#199)."""
    WAIT["held"], WAIT["end"] = asyncio.Event(), asyncio.Event()
    communicator = await _connect()
    try:
        await _lingering(communicator, how)
        await asyncio.wait_for(WAIT["held"].wait(), timeout=5)
        # Held with the connection while the handler goes on: nothing is written
        assert await communicator.receive_nothing(timeout=0.1)
        WAIT["end"].set()
        heard = await _until(communicator, lambda m: m["command"] == "push_event" and m["payload"]["event"] == "done")
    finally:
        WAIT["end"].set()
        await communicator.disconnect()

    assert _written(heard) == ["stream_op", "other", "done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["append", "reset"])
async def test_too_many_patches_held_while_a_handler_goes_on_after_its_stream_close_the_connection(how, monkeypatch):
    """Past QUEUE_LIMIT the connection closes and the page joins again (#199)."""
    monkeypatch.setattr(patches, "QUEUE_LIMIT", 3)
    WAIT["end"] = asyncio.Event()
    communicator = await _connect()
    try:
        await _lingering(communicator, how, published=patches.QUEUE_LIMIT + 1)
        while (out := await communicator.receive_output(timeout=5))["type"] != "websocket.close":
            assert json.loads(out["text"])["command"] != "push_event", out
    finally:
        WAIT["end"].set()
        await communicator.disconnect()

    assert out["code"] == 1013


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", [False, True])
async def test_a_stream_cancelled_on_its_way_to_the_channel_still_lets_its_hold_go(reset, monkeypatch):
    """start_async replaces the operation while its stream() waits for a layer's round trip (#199).

    Cancelled there, the mail never reached the channel: its token stayed and
    the connection closed once HOLD_SECONDS ran out. The send now goes on.
    """
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.05)
    WAIT["sending"], WAIT["sent"] = asyncio.Event(), asyncio.Event()
    real = WireviewMeta.send_to

    async def round_trip(self, channel, command, **kwargs):
        if command == "stream_op":
            # A layer with a network round trip (redis, nats): cancelled meanwhile
            WAIT["sending"].set()
            await WAIT["sent"].wait()
        return await real(self, channel, command, **kwargs)

    monkeypatch.setattr(WireviewMeta, "send_to", round_trip)
    communicator = await _connect()
    try:
        await _join(communicator, BcReplacing, "replace")
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {
                    "id": "replace",
                    "command": "poll",
                    "implicit_args": {},
                    "explicit_args": {"reset": reset},
                    "ref": 1,
                },
            }
        )
        await _until(communicator, lambda m: m["command"] == "push_event")
        # Well past the hold's deadline, the connection is open and writes what comes
        await asyncio.sleep(patches.HOLD_SECONDS * 6)
        await Broadcast(BcReplacing, "bc-replace").push_event("after").asend()
        heard = await _until(communicator, lambda m: m["command"] == "push_event" and m["payload"]["event"] == "after")
    finally:
        await communicator.disconnect()

    assert _written(heard)[-1] == "after"


@pytest.mark.asyncio
@pytest.mark.parametrize("vsn", [UPDATE_ONLY_SINCE - 1, UPDATE_ONLY_SINCE])
async def test_an_update_only_insert_not_sent_to_an_older_client_holds_nothing_back(vsn):
    """Sent or not, it keeps nothing behind it: what is published after it goes out while the handler runs."""
    communicator = await _connect(vsn)
    try:
        await _join(communicator, BcAppending, "append")
        await _event(communicator, "append", "edit_then_publish")
        heard = await _until(communicator, _finished)
    finally:
        await communicator.disconnect()

    sent = vsn >= UPDATE_ONLY_SINCE
    assert _written(heard) == (["after", "stream_op", "finished"] if sent else ["after", "finished"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "edit"])
async def test_a_mounted_component_writes_it_after_the_insert_too(change):
    view = await mount(BcAppending, id="append")
    view.clear_messages()

    await view.call("older", change=change)

    ops = view.stream_ops("items")
    assert ops[-1]["items"][0]["id"] == "items-5"
    _assert_change_kept(_shown(ops), change)


async def _event(communicator: WebsocketCommunicator, id: str, handler: str) -> None:
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {"id": id, "command": handler, "implicit_args": {}, "explicit_args": {}, "ref": 1},
        }
    )


def _stream_ops(heard: list[dict[str, t.Any]]) -> list[tuple[str, list[str]]]:
    return [
        (m["payload"]["op"], [item["id"] for item in m["payload"]["items"]])
        for m in heard
        if m["command"] == "stream_op"
    ]


def _posted(session: WireviewSession) -> int:
    """How many patch frames (``_message``'s push_event) the session wrote."""
    return sum(json.loads(frame)["command"] == "push_event" for frame in session.outbound.frames)  # type: ignore[attr-defined]


def _finished(message: dict[str, t.Any]) -> bool:
    return message["command"] == "push_event" and message["payload"]["event"] == "finished"


@pytest.mark.asyncio
async def test_a_patch_held_across_two_resets_is_written_after_the_second():
    """The first reset's release used to let out what came while the second was on its way."""
    communicator = await _connect()
    try:
        await _join(communicator, BcTwice, "twice")
        await _event(communicator, "twice", "refresh")
        heard = await _until(communicator, _finished)
    finally:
        await communicator.disconnect()

    assert _stream_ops(heard) == [("reset", ["items-1"]), ("reset", ["items-1"]), ("insert", ["items-2"])]


@pytest.mark.asyncio
async def test_the_deadline_starts_when_a_pending_reset_is_sent_not_when_it_is_queued(monkeypatch):
    """update() queues its reset until the render: a deadline counted from the queue let the patch out first."""
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.02)
    communicator = await _connect()
    try:
        await _join(communicator, BcUpdatingHost, "updating")
        await _event(communicator, "updating", "refresh")
        # The release is queued behind the reset and ahead of ``finished``
        heard = await _until(communicator, _finished)
    finally:
        await communicator.disconnect()

    assert _stream_ops(heard) == [("reset", ["items-1"]), ("insert", ["items-2"])]


@pytest.mark.asyncio
async def test_a_handler_that_runs_past_the_deadline_does_not_let_its_patch_out_first(monkeypatch):
    """The release waits behind the handler in the channel: it is late, not lost."""
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.02)
    communicator = await _connect()
    try:
        await _join(communicator, BcSlowHandler, "slow")
        await _event(communicator, "slow", "refresh")
        heard = await _until(communicator, _finished)
    finally:
        await communicator.disconnect()

    assert _stream_ops(heard) == [("reset", ["items-1"]), ("insert", ["items-2"])]


@pytest.mark.asyncio
async def test_a_mounted_component_lets_a_patch_out_after_its_handler_as_a_connection_does():
    communicator = await _connect()
    try:
        await _join(communicator, BcTwiceReading, "twice")
        await _event(communicator, "twice", "refresh")
        connection = _stream_ops(await _until(communicator, _finished))
    finally:
        await communicator.disconnect()
    view = await mount(BcTwiceReading, id="twice")
    view.clear_messages()

    await view.call("refresh")

    assert connection == [("reset", ["items-1"]), ("reset", ["items-1"]), ("insert", ["items-2"])]
    assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("items")] == connection


@pytest.mark.asyncio
async def test_a_reset_late_behind_the_next_handler_is_waited_for(monkeypatch):
    """The handler that sent the reset is done, the next one runs past the deadline: the reset is late, not lost."""
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.02)
    BUSY.clear()
    communicator = await _connect()
    try:
        await _join(communicator, BcBusyAfterTwo, "late")
        await _event(communicator, "late", "refresh")
        # Taken before refresh's mail: it runs between refresh and its second reset
        await _event(communicator, "late", "busy")
        await eventually(lambda: BcBusyAfterTwo.busy_running)
        await asyncio.sleep(patches.HOLD_SECONDS * 5)
        BUSY.set()
        events: set[str] = set()

        def both(message: dict[str, t.Any]) -> bool:
            if message["command"] == "push_event":
                events.add(message["payload"]["event"])
            return events >= {"idle", "finished"}

        heard = await _until(communicator, both)
    finally:
        BUSY.set()
        await communicator.disconnect()

    assert _stream_ops(heard) == [("reset", ["items-1"]), ("reset", ["items-1"]), ("insert", ["items-2"])]


@pytest.mark.asyncio
async def test_a_mounted_component_holds_from_before_its_joined_as_a_connection_does():
    communicator = await _connect()
    try:
        connection = _stream_ops(await _join(communicator, BcPublishingFirst, "first"))
    finally:
        await communicator.disconnect()

    view = await mount(BcPublishingFirst, id="first")

    assert connection == [("reset", ["items-1"]), ("insert", ["items-2"])]
    assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("items")] == connection


class _Layer:
    """The part of a channel layer a ``PatchHub`` uses, counting what it is asked."""

    def __init__(self) -> None:
        self.added: list[str] = []
        self.discarded: list[str] = []
        self.inbox: asyncio.Queue[dict[str, t.Any]] = asyncio.Queue()

    async def new_channel(self) -> str:
        return "hub-channel"

    async def group_add(self, group: str, channel: str) -> None:
        self.added.append(group)

    async def group_discard(self, group: str, channel: str) -> None:
        self.discarded.append(group)

    async def receive(self, channel: str) -> dict[str, t.Any]:
        return await self.inbox.get()


@pytest.fixture
def hub():
    hub = PatchHub(_Layer())  # type: ignore[arg-type]
    set_patch_hub(hub)
    yield hub
    set_patch_hub(None)
    hub._stop()


async def _joined_session(outbound: t.Any, id: str = "feed") -> WireviewSession:
    session = WireviewSession(outbound)
    await session.start(session=SessionView.wrap(None))
    feed = await session.repo.join("BcFeed", {"id": id}, before_joined=session._hold_patches)
    await session.after_mutation_chores()
    # Out of pending mode, as command_join leaves it
    await feed.wire.flush_pending()
    await session._release_patches(feed)
    return session


def _message(cls: type[Component] = BcFeed, topic: str = "bc-feed") -> dict[str, t.Any]:
    (frame,) = Broadcast(cls, topic).push_event("posted")._frames()
    return {"type": "wireview.patch", "target": cls._fqn, "topic": topic, "frames": [frame]}


@pytest.mark.asyncio
async def test_a_process_takes_a_patch_once_and_hands_it_to_every_session(hub):
    layer = hub._layer
    first = await _joined_session(_Recording(), "first")
    second = await _joined_session(_Recording(), "second")
    assert layer.added == ["wireview.patch.bc-feed"], "one group_add for the process, not one per connection"

    layer.inbox.put_nowait(_message())
    written = await eventually(lambda: first.outbound.frames and second.outbound.frames)  # type: ignore[attr-defined]

    assert written
    assert [json.loads(f)["payload"]["component_id"] for f in first.outbound.frames] == ["first"]  # type: ignore[attr-defined]
    assert [json.loads(f)["payload"]["component_id"] for f in second.outbound.frames] == ["second"]  # type: ignore[attr-defined]

    await first.stop(1000)
    assert layer.discarded == [], "the other session still hears the topic"
    await second.stop(1000)
    assert layer.discarded == ["wireview.patch.bc-feed"]
    assert hub.topics == set() and hub._tasks == []


@pytest.mark.asyncio
async def test_the_process_renews_its_membership_before_the_layer_expires_it(hub):
    await _joined_session(_Recording())
    await hub.renew()
    assert hub._layer.added == ["wireview.patch.bc-feed", "wireview.patch.bc-feed"]


@pytest.mark.asyncio
async def test_a_renewal_that_fails_leaves_the_next_one_to_run(hub, caplog):
    """One error from the layer used to end the renewing task: a day later the process heard nothing."""
    layer = hub._layer
    failures = [ConnectionError("the layer went away")]

    async def group_add(group: str, channel: str) -> None:
        if failures:
            raise failures.pop()
        layer.added.append(group)

    hub.RENEW_SECONDS = 0.01
    await hub.join("a", "key", lambda message: None)
    await hub.join("b", "key", lambda message: None)
    layer.added.clear()
    layer.group_add = group_add

    # The first renewal fails for one topic and renews the other; the ones after renew both
    renewing = hub._tasks[1]
    await eventually(
        lambda: layer.added.count("wireview.patch.a") >= 2 and layer.added.count("wireview.patch.b") >= 2,
        task=renewing,
    )

    assert not renewing.done()
    assert "Could not renew" in caplog.text


@pytest.mark.asyncio
async def test_a_topic_no_one_hears_keeps_no_lock(hub):
    for room in range(50):
        await hub.join(f"room.{room}", "key", lambda message: None)
    await asyncio.gather(*(hub.leave(f"room.{room}", "key") for room in range(50)))

    assert hub._receivers == {} and hub._locks == {}


@pytest.mark.asyncio
async def test_a_slow_socket_holds_up_only_its_own_frames_and_is_closed_past_the_limit(hub):
    closed: list[int | None] = []
    events: list[dict[str, t.Any]] = []
    never = asyncio.Event()

    class Stuck(_Recording):
        async def send_text(self, text: str) -> None:
            await never.wait()

        async def close(self, code=None):
            closed.append(code)

    def overflowed(sender, **kwargs):
        events.append(kwargs)

    await _joined_session(Stuck(), "stuck")
    quick = await _joined_session(_Recording(), "quick")
    message = _message()

    telemetry.broadcast_overflowed.connect(overflowed)
    telemetry.enable()
    try:
        hub.dispatch(message)
        await eventually(lambda: quick.outbound.frames)  # type: ignore[attr-defined]
        # The first frame is with the stuck socket; the queue behind it holds the rest
        for _ in range(patches.QUEUE_LIMIT):
            hub.dispatch(message)
            await asyncio.sleep(0)  # the hub's loop takes one message at a time: writers run between
        assert closed == []
        hub.dispatch(message)
        await eventually(lambda: closed)
        hub.dispatch(message)
    finally:
        telemetry.disable()
        telemetry.broadcast_overflowed.disconnect(overflowed)
        never.set()

    assert closed == [1013]
    # Every frame reached the other socket meanwhile
    await eventually(lambda: len(quick.outbound.frames) == patches.QUEUE_LIMIT + 3)  # type: ignore[attr-defined]
    assert [(e["component_id"], e["topic"], e["limit"]) for e in events] == [("stuck", "bc-feed", patches.QUEUE_LIMIT)]


@pytest.mark.asyncio
async def test_a_component_that_falls_behind_while_held_closes_its_connection(hub):
    closed: list[int | None] = []

    class Closing(_Recording):
        async def close(self, code=None):
            closed.append(code)

    session = WireviewSession(Closing())  # type: ignore[arg-type]
    await session.start(session=SessionView.wrap(None))
    feed = session.repo.build("BcFeed", {"id": "feed"})
    await session._hold_patches(feed)
    message = _message()

    for _ in range(patches.QUEUE_LIMIT):
        hub.dispatch(message)
    assert closed == []
    hub.dispatch(message)
    await eventually(lambda: closed)

    assert closed == [1013]
    assert session.outbound.frames == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_what_was_held_for_a_component_that_is_gone_is_not_written(hub):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    assert await session._hold_patches(feed)
    hub.dispatch(_message())
    session.repo.remove("feed")

    await session._release_patches(feed)

    assert session.outbound.frames == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_connection_whose_release_never_comes_closes_rather_than_write_out_of_order(hub, monkeypatch, caplog):
    """A full channel may drop the mail: written then, the frames could go ahead of a reset that is only late."""
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.02)
    closed: list[int | None] = []

    class Closing(_Recording):
        async def close(self, code=None):
            closed.append(code)

    session = await _joined_session(Closing())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)
    await session._hold_patches(feed)
    hub.dispatch(_message())
    await session._let_patches_through(feed)
    assert [command for command, _ in mail] == ["release_patches"]

    # Handling a message, it waits
    with session.handling_message():
        await asyncio.sleep(patches.HOLD_SECONDS * 3)
        assert closed == []
    await eventually(lambda: closed)

    assert closed == [1013]
    assert session.outbound.frames == []  # type: ignore[attr-defined]
    assert "did not come in" in caplog.text


@pytest.mark.asyncio
async def test_a_connection_its_login_ended_on_writes_no_more_patches(hub):
    closed: list[int | None] = []

    class Closing(_Recording):
        async def close(self, code=None):
            closed.append(code)

    session = await _joined_session(Closing())
    await session.session_invalidated({"reason": "logged out"})
    hub.dispatch(_message())
    await session._patches_written()

    assert closed == [4001]
    assert session.outbound.frames == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_socket_that_refuses_a_write_ends_its_patches_without_raising(hub):
    """A socket closed before its disconnect reached the session: the error came up in whatever awaited next."""
    tried: list[str] = []

    class Gone(_Recording):
        async def send_text(self, text: str) -> None:
            tried.append(text)
            raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")

    session = await _joined_session(Gone())
    hub.dispatch(_message())
    hub.dispatch(_message())
    await session._patches_written()
    hub.dispatch(_message())
    await session._patches_written()

    assert len(tried) == 1


async def _mailing(session: WireviewSession, component: Component, monkeypatch) -> list[tuple[str, dict[str, t.Any]]]:
    """Keep the session mail ``component`` sends, for the test to deliver when it says."""
    mail: list[tuple[str, dict[str, t.Any]]] = []

    async def send(command: str, **kwargs: t.Any) -> None:
        mail.append((command, kwargs))

    component.wire.channel_name = "the-sessions-channel"
    monkeypatch.setattr(component.wire, "_do_send", send)
    return mail


async def _deliver(session: WireviewSession, mail: list[tuple[str, dict[str, t.Any]]]) -> None:
    for command, kwargs in mail:
        await getattr(session, f"component_{command}")(**kwargs)


@pytest.mark.asyncio
async def test_a_reset_lets_go_of_its_own_token_only(hub, monkeypatch):
    """A reset carries its token: written again, or late, it lets out nothing of a later hold."""
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)

    # A reset queued in pending mode, flushed, and the join's release after it (send_render does both)
    feed.wire.enter_pending_mode()
    await feed.stream("items", [])
    await feed.wire.flush_pending()
    await session._let_patches_through(feed)
    first = list(mail)
    assert [command for command, _ in first] == ["stream_op"]
    await _deliver(session, first)
    assert session._patch_held == {}

    await feed.stream("items", [])
    later = session._patch_held[id(feed)]
    hub.dispatch(_message())
    await _deliver(session, first)

    assert session._patch_held[id(feed)] is later
    assert _posted(session) == 0


@pytest.mark.asyncio
async def test_a_hold_that_could_not_subscribe_is_given_back(hub, monkeypatch):
    """The reset raised before it took its release: a retry inherited a hold nothing would let go."""
    session = WireviewSession(_Recording())  # type: ignore[arg-type]
    await session.start(session=SessionView.wrap(None))
    feed = await session.repo.join("BcDynamic", {"id": "feed"}, before_joined=session._hold_patches)
    await session.after_mutation_chores()
    await feed.wire.flush_pending()
    await session._release_patches(feed)
    mail = await _mailing(session, feed, monkeypatch)
    layer = hub._layer
    group_add = layer.group_add

    async def unavailable_once(group: str, channel: str) -> None:
        if group == "wireview.patch.room.new" and not layer.added.count("failed"):
            layer.added.append("failed")
            raise ConnectionError("the layer went away")
        await group_add(group, channel)

    layer.group_add = unavailable_once
    feed.room = "room.new"
    with pytest.raises(ConnectionError):
        await feed.stream("items", [])
    await feed.stream("items", [])
    await _deliver(session, mail)
    hub.dispatch(_message(BcDynamic, "room.new"))
    await session._patches_written()

    assert session._patch_held == {}
    assert _posted(session) == 1


@pytest.mark.asyncio
async def test_a_component_that_left_holds_nothing_and_hears_no_topic_again(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)
    session.repo.remove("feed")
    await session.after_mutation_chores()
    assert hub.topics == set()

    # A reference kept to it, a task that ran late
    await feed.stream("items", [])
    await _deliver(session, mail)

    assert hub.topics == set() and session._patch_held == {}


@pytest.mark.asyncio
async def test_a_pending_reset_starts_its_deadline_once_it_was_sent(hub, monkeypatch):
    monkeypatch.setattr(patches, "HOLD_SECONDS", 0.02)
    closed: list[int | None] = []

    class Closing(_Recording):
        async def close(self, code=None):
            closed.append(code)

    session = await _joined_session(Closing())
    feed = session.repo.get("feed")
    await _mailing(session, feed, monkeypatch)
    feed.wire.enter_pending_mode()
    await feed.stream("items", [])
    hub.dispatch(_message())
    await asyncio.sleep(patches.HOLD_SECONDS * 3)
    assert session._patch_held[id(feed)].expiries == {} and closed == []

    # Sent, and never delivered
    await feed.wire.flush_pending()

    await eventually(lambda: closed)
    assert _posted(session) == 0


@pytest.mark.asyncio
async def test_a_reset_whose_read_raised_lets_its_hold_go(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)

    async def posts():
        yield Post(1, "old")
        raise ValueError("the read failed")

    hub.dispatch(_message())
    await session._patches_written()
    assert _posted(session) == 1
    feed.wire.channel_name = "the-sessions-channel"
    with pytest.raises(ValueError):
        await feed.stream("items", posts())
    hub.dispatch(_message())
    await session._patches_written()

    assert mail == [] and session._patch_held == {}
    assert _posted(session) == 2


@pytest.mark.asyncio
async def test_a_reset_whose_send_raised_after_queueing_keeps_its_hold(hub, monkeypatch):
    """channels_redis adds the message, then sets its expiry: the send can raise with the reset in the channel."""
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)
    queued = feed.wire._do_send

    async def queued_then_raised(command: str, **kwargs: t.Any) -> None:
        await queued(command, **kwargs)
        raise ConnectionError("the expiry could not be set")

    monkeypatch.setattr(feed.wire, "_do_send", queued_then_raised)
    with pytest.raises(ConnectionError):
        await feed.stream("items", [Post(1, "old")])
    hub.dispatch(_message())
    await session._patches_written()
    assert _posted(session) == 0

    await _deliver(session, mail)

    assert [json.loads(frame)["command"] for frame in session.outbound.frames] == ["stream_op", "push_event"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_subscription_that_ends_after_its_connection_stopped_leaves_no_receiver(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    await _mailing(session, feed, monkeypatch)
    adding, proceed = asyncio.Event(), asyncio.Event()
    group_add = hub._layer.group_add

    async def slow_group_add(group: str, channel: str) -> None:
        if group == "wireview.patch.bc-late":
            adding.set()
            await proceed.wait()
        await group_add(group, channel)

    hub._layer.group_add = slow_group_add
    monkeypatch.setattr(BcFeed, "get_subscriptions", lambda self: {"bc-late"})
    resetting = asyncio.ensure_future(feed.stream("items", []))
    await adding.wait()
    await session.stop(1000)
    proceed.set()
    await resetting

    assert session._patch_topics == set() and hub._receivers == {}


@pytest.mark.asyncio
async def test_a_stopped_connection_holds_nothing_for_a_component_kept_after_it(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    await session.stop(1000)

    await feed.stream("items", [])

    assert session._patch_held == {} and hub.topics == set() and hub._receivers == {}


@pytest.mark.asyncio
async def test_a_subscription_that_was_cancelled_leaves_no_receiver(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    adding = asyncio.Event()

    async def group_add(group: str, channel: str) -> None:
        adding.set()
        await asyncio.Event().wait()

    hub._layer.group_add = group_add
    monkeypatch.setattr(BcFeed, "get_subscriptions", lambda self: {"bc-new"})
    resetting = asyncio.ensure_future(feed.stream("items", []))
    await adding.wait()
    resetting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await resetting
    await session.stop(1000)

    assert session._patch_held == {}
    assert hub._receivers == {}


@pytest.mark.asyncio
async def test_a_reset_cancelled_while_another_subscribes_leaves_the_subscription(hub, monkeypatch):
    """Two resets of one connection hold at once; undoing the cancelled one undid the other's join."""
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    mail = await _mailing(session, feed, monkeypatch)
    adding, proceed = asyncio.Event(), asyncio.Event()
    group_add = hub._layer.group_add

    async def slow_group_add(group: str, channel: str) -> None:
        if group == "wireview.patch.bc-new":
            adding.set()
            await proceed.wait()
        await group_add(group, channel)

    hub._layer.group_add = slow_group_add
    monkeypatch.setattr(BcFeed, "get_subscriptions", lambda self: {"bc-new"})
    first = asyncio.ensure_future(feed.stream("items", []))
    await adding.wait()
    second = asyncio.ensure_future(feed.stream("items", []))
    await eventually(lambda: session._patch_settling._waiters)  # type: ignore[attr-defined]
    second.cancel()
    proceed.set()
    await first
    with pytest.raises(asyncio.CancelledError):
        await second
    await _deliver(session, mail)
    hub.dispatch(_message(BcFeed, "bc-new"))
    await session._patches_written()

    assert "bc-new" in session._patch_topics
    assert _posted(session) == 1


@pytest.mark.asyncio
async def test_a_join_that_fails_gives_back_the_receiver_it_replaced(hub):
    heard: list[str] = []
    await hub.join("t", "key", lambda message: heard.append("first"))
    layer = hub._layer

    async def unavailable(group: str, channel: str) -> None:
        raise ConnectionError("the layer went away")

    await hub.leave("t", "key")
    await hub.join("t", "key", lambda message: heard.append("first"))
    hub._joined.discard("t")  # as if the group expired: the next join adds it again
    layer.group_add = unavailable
    with pytest.raises(ConnectionError):
        await hub.join("t", "key", lambda message: heard.append("second"))
    hub.dispatch({"topic": "t"})

    assert heard == ["first"]


@pytest.mark.asyncio
async def test_stopping_cancels_the_deadlines_of_what_it_held(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    await _mailing(session, feed, monkeypatch)
    await feed.stream("items", [])
    deadlines = list(session._patch_held[id(feed)].expiries.values())

    await session.stop(1000)

    assert deadlines and all(deadline.cancelled() for deadline in deadlines)


@pytest.mark.asyncio
async def test_a_join_that_failed_after_its_reset_holds_nothing(hub):
    session = WireviewSession(_Recording())  # type: ignore[arg-type]
    session.channel_name = "the-sessions-channel"
    await session.start(session=SessionView.wrap(None), vsn=PROTOCOL_VERSION)

    await session.command_join("BcResetThenFail", _state(BcResetThenFail, "failed"), {})

    assert session.repo.get("failed") is None
    assert session._patch_held == {}


@pytest.mark.asyncio
async def test_a_closed_connection_holds_nothing_and_its_deadlines_are_cancelled(hub, monkeypatch):
    session = await _joined_session(_Recording())
    feed = session.repo.get("feed")
    await _mailing(session, feed, monkeypatch)
    await feed.stream("items", [])
    deadlines = list(session._patch_held[id(feed)].expiries.values())
    hub.dispatch(_message())

    session._close_patches()
    await feed.stream("items", [])
    await session._patches_written()

    assert deadlines and all(deadline.cancelled() for deadline in deadlines)
    assert session._patch_held == {} and session.outbound.frames == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_end_of_a_connection_leaves_no_patch_group_behind(hub):
    session = await _joined_session(_Recording())
    assert hub.topics == {"bc-feed"}

    await session.stop(1000)

    assert hub.topics == set()
    assert session._patch_book == {} and session._patch_held == {}


@pytest.mark.asyncio
async def test_a_topic_too_long_for_a_patch_group_still_subscribes_its_notifications(hub):
    class Long(BcFeed):
        class Meta:
            subscriptions = {"x" * (PATCH_TOPIC_MAX + 1)}

    class Topics(_Recording):
        topics: list[str]

        async def subscribe(self, topic):
            get_channel_layer().require_valid_group_name(topic)
            self.topics = [*getattr(self, "topics", []), topic]

    session = WireviewSession(Topics())  # type: ignore[arg-type]
    await session.start(session=SessionView.wrap(None))
    await session.repo.join("Long", {"id": "long"}, before_joined=session._hold_patches)
    await session.after_mutation_chores()

    assert session.outbound.topics == ["x" * (PATCH_TOPIC_MAX + 1)]  # type: ignore[attr-defined]
    assert hub.topics == set()


# --- the channel layer -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_consumer_in_the_topics_notification_group_hears_no_patch():
    """A process from before patches has its consumers in the topic's group, with no handler for a patch.

    Channels raises on a message type a consumer has no handler for, which would
    close its socket; the patch goes to a group of its own instead (§7-2).
    """
    layer = get_channel_layer()
    old = await layer.new_channel()
    new = await layer.new_channel()
    await layer.group_add("bc-mixed", old)
    await layer.group_add("wireview.patch.bc-mixed", new)
    try:
        await Broadcast(BcDynamic, "bc-mixed").push_event("posted").asend()
        await layer.group_send("bc-mixed", {"type": "notification", "channel": "bc-mixed", "kwargs": {}})

        assert (await layer.receive(new))["type"] == "wireview.patch"
        assert (await layer.receive(old))["type"] == "notification"
    finally:
        await layer.group_discard("bc-mixed", old)
        await layer.group_discard("wireview.patch.bc-mixed", new)


@pytest.mark.asyncio
async def test_publishing_is_a_broadcast_published_span_of_kind_patch():
    spans: list[dict[str, t.Any]] = []

    def published(sender, **kwargs):
        spans.append(kwargs)

    telemetry.broadcast_published.connect(published)
    telemetry.enable()
    try:
        await Broadcast(BcFeed, "bc-feed").push_event("posted").asend()
    finally:
        telemetry.disable()
        telemetry.broadcast_published.disconnect(published)

    (span,) = spans
    assert (span["topic"], span["kind"], span["error"]) == ("bc-feed", "patch", None)
    assert span["payload_size"] > 0


@pytest.mark.asyncio
async def test_nothing_to_send_publishes_nothing():
    published: list[t.Any] = []

    class Broker:
        async def publish_patch(self, topic, message):
            published.append(message)

    set_broker(Broker())  # type: ignore[arg-type]
    try:
        await Broadcast(BcFeed, "bc-feed").asend()
    finally:
        set_broker(None)

    assert published == []


# --- wireview.testing --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_mounted_component_hears_a_broadcast_for_its_class_and_topic():
    view = await mount(BcFeed, id="feed")
    child = await mount(BcFeedChild, id="child")
    view.clear_messages()
    child.clear_messages()

    await Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(3, "new"), at=0).asend()
    await Broadcast(BcDynamic, "bc-feed").push_event("unheard").asend()

    assert view.stream_html("items") == '<li id="items-3">new</li>'
    assert [op["id"] for op in view.stream_ops("items")] == ["feed"]
    assert child.sent_messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("vsn", "sent"), [(UPDATE_ONLY_SINCE - 1, False), (UPDATE_ONLY_SINCE, True)])
async def test_a_mounted_component_leaves_out_a_frame_its_client_would_misread(vsn, sent):
    """As a session does (``patches.reaches``). mount() talks to this release's
    client, so the test lowers its repository's version (white box)."""
    view = await mount(BcFeed, id="feed")
    view._repo.vsn = vsn
    view.clear_messages()

    broadcast = Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "edited"), update_only=True)
    await broadcast.stream_delete("items", 2).asend()

    assert [op["op"] for op in view.stream_ops("items")] == (["insert", "delete"] if sent else ["delete"])


@pytest.mark.asyncio
async def test_a_mounted_component_writes_a_patch_after_the_reset_it_came_during():
    """As a connection does: the order a test sees is the browser's."""
    view = await mount(BcRefreshing, id="refresh")
    view.clear_messages()

    await view.component.refresh_while_reading()

    assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("items")] == [
        ("reset", ["items-1"]),
        ("insert", ["items-2"]),
    ]


# --- the process's channel, the connection's queue and its limit (#185) ------------------


class _Opening(_Layer):
    """A layer whose new_channel() the test controls: fails once, or waits until let go."""

    def __init__(self, fail: int = 0) -> None:
        super().__init__()
        self.opened = 0
        self.fail = fail
        self.entered = asyncio.Event()
        self.go = asyncio.Event()
        self.go.set()

    async def new_channel(self) -> str:
        self.opened += 1
        self.entered.set()
        await self.go.wait()
        if self.fail:
            self.fail -= 1
            raise OSError("the layer could not open a channel")
        return f"hub-channel-{self.opened}"


@pytest.mark.asyncio
async def test_a_channel_that_could_not_be_opened_is_opened_again_by_the_next_join():
    """One failed new_channel() used to stay the answer to every join of the process."""
    layer = _Opening(fail=1)
    hub = PatchHub(layer)  # type: ignore[arg-type]
    try:
        with pytest.raises(OSError):
            await hub.join("a", "first", lambda message: None)
        assert hub._receivers == {} and hub.topics == set()

        heard: list[dict[str, t.Any]] = []
        await hub.join("a", "second", heard.append)
        layer.inbox.put_nowait({"topic": "a"})
        await eventually(lambda: heard)

        assert layer.opened == 2
        assert hub.topics == {"a"} and layer.added == ["wireview.patch.a"]
    finally:
        hub._stop()


@pytest.mark.asyncio
async def test_a_join_cancelled_while_the_channel_opens_leaves_the_others_joining():
    """The opening is the process's: one join giving up must not cancel it for the rest."""
    layer = _Opening()
    layer.go.clear()
    hub = PatchHub(layer)  # type: ignore[arg-type]
    try:
        first = asyncio.ensure_future(hub.join("a", "first", lambda message: None))
        await layer.entered.wait()
        heard: list[dict[str, t.Any]] = []
        second = asyncio.ensure_future(hub.join("b", "second", heard.append))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        layer.go.set()
        await second
        layer.inbox.put_nowait({"topic": "b"})
        await eventually(lambda: heard)

        assert layer.opened == 1
        assert hub.topics == {"b"} and set(hub._receivers) == {"b"}
    finally:
        hub._stop()


@pytest.mark.asyncio
async def test_a_channel_opened_for_a_join_that_gave_up_starts_nothing():
    layer = _Opening()
    layer.go.clear()
    hub = PatchHub(layer)  # type: ignore[arg-type]
    try:
        first = asyncio.ensure_future(hub.join("a", "first", lambda message: None))
        await layer.entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        layer.go.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert hub._tasks == [] and hub._receivers == {} and hub.topics == set()
        # And the next join uses it rather than open another
        await hub.join("a", "again", lambda message: None)
        assert layer.opened == 1 and hub.topics == {"a"}
    finally:
        hub._stop()


@pytest.mark.asyncio
async def test_a_queued_frame_is_not_written_for_a_component_that_left_before_it(hub):
    """Asked again right before each write, as the documentation says: queued is not written."""
    session = await _joined_session(_Recording())
    session._take_patch(_message())
    # Before the writer took it: the component leaves
    session.repo.remove("feed")
    await session._patches_written()

    assert _posted(session) == 0


@pytest.mark.asyncio
async def test_a_queued_frame_is_not_written_for_another_instance_under_the_same_id(hub):
    session = await _joined_session(_Recording())
    old = session.repo.get("feed")
    session._take_patch(_message())
    session.repo.remove("feed")
    new = session.repo.build("BcFeed", {"id": "feed"})
    assert session.repo.reachable("feed") is new and new is not old
    await session._patches_written()

    assert _posted(session) == 0


@pytest.mark.asyncio
async def test_the_frames_of_components_still_there_go_out_in_order(hub):
    session = await _joined_session(_Recording(), "first")
    await session.repo.join("BcFeed", {"id": "second"}, before_joined=session._hold_patches)
    second = session.repo.get("second")
    await second.wire.flush_pending()
    await session.after_mutation_chores()
    await session._release_patches(second)
    for event in ("one", "two"):
        (frame,) = Broadcast(BcFeed, "bc-feed").push_event(event)._frames()
        session._take_patch({"type": "wireview.patch", "target": BcFeed._fqn, "topic": "bc-feed", "frames": [frame]})
    session.repo.remove("first")
    await session._patches_written()

    written = [json.loads(frame)["payload"] for frame in session.outbound.frames]  # type: ignore[attr-defined]
    assert [(p["component_id"], p["event"]) for p in written] == [("second", "one"), ("second", "two")]


def _stuck() -> tuple[t.Any, list[int | None]]:
    """An Outbound whose socket takes nothing, and the close codes it got."""
    closed: list[int | None] = []
    never = asyncio.Event()

    class Stuck(_Recording):
        async def send_text(self, text: str) -> None:
            await never.wait()

        async def close(self, code=None):
            closed.append(code)

    return Stuck(), closed


async def _held_feeds(session: WireviewSession, count: int) -> list[Component]:
    feeds = []
    for index in range(count):
        feed = session.repo.build("BcFeed", {"id": f"held-{index}"})
        assert await session._hold_patches(feed) is not None
        feeds.append(feed)
    return feeds


@pytest.mark.asyncio
async def test_the_limit_counts_every_frame_held_on_the_connection(hub):
    """Two components holding QUEUE_LIMIT each used to keep twice the limit waiting."""
    outbound, closed = _stuck()
    session = WireviewSession(outbound)
    await session.start(session=SessionView.wrap(None))
    await _held_feeds(session, 2)
    events: list[dict[str, t.Any]] = []

    def overflowed(sender, **kwargs):
        events.append(kwargs)

    telemetry.broadcast_overflowed.connect(overflowed)
    telemetry.enable()
    try:
        # Each message puts a frame in each hold: 2 per message
        for _ in range(patches.QUEUE_LIMIT // 2):
            session._take_patch(_message())
        assert closed == [] and not session._patches_closed
        session._take_patch(_message())
        await eventually(lambda: closed)
    finally:
        telemetry.disable()
        telemetry.broadcast_overflowed.disconnect(overflowed)

    assert closed == [1013]
    assert len(events) == 1 and events[0]["limit"] == patches.QUEUE_LIMIT


@pytest.mark.asyncio
async def test_the_limit_counts_held_and_queued_frames_together(hub):
    """Frames held for one component count when another's are queued: the limit is the connection's."""
    outbound, closed = _stuck()
    session = await _joined_session(outbound, "queued")
    quiet = session.repo.build("BcQuiet", {"id": "quiet"})
    assert await session._hold_patches(quiet) is not None
    for _ in range(patches.QUEUE_LIMIT // 2):
        session._take_patch(_message(BcQuiet, "bc-quiet"))
    # Now only the queue grows; the socket is stuck with the first frame
    for _ in range(patches.QUEUE_LIMIT // 2):
        session._take_patch(_message())
        await asyncio.sleep(0)
    assert closed == []
    session._take_patch(_message())
    session._take_patch(_message())
    await eventually(lambda: closed)

    assert closed == [1013]


@pytest.mark.asyncio
async def test_frames_moved_from_a_hold_to_the_queue_are_counted_once(hub):
    outbound, closed = _stuck()
    session = WireviewSession(outbound)
    await session.start(session=SessionView.wrap(None))
    (feed,) = await _held_feeds(session, 1)
    for _ in range(patches.QUEUE_LIMIT):
        session._take_patch(_message())
    assert closed == [] and not session._patches_closed

    # At the limit, held: let through, they are the same frames waiting elsewhere
    session._unhold(feed)
    await asyncio.sleep(0)
    assert closed == [] and not session._patches_closed
    session._take_patch(_message())
    session._take_patch(_message())
    await eventually(lambda: closed)

    assert closed == [1013]


@pytest.mark.asyncio
async def test_frames_dropped_with_a_component_that_left_free_the_limit(hub):
    outbound, closed = _stuck()
    session = WireviewSession(outbound)
    await session.start(session=SessionView.wrap(None))
    gone, kept = await _held_feeds(session, 2)
    for _ in range(patches.QUEUE_LIMIT // 2):
        session._take_patch(_message())
    session.repo.remove(gone.id)
    await session.after_mutation_chores()
    # What the one that left held is gone; the other's hold has room again
    for _ in range(patches.QUEUE_LIMIT // 2 - 1):
        session._take_patch(_message())
    await asyncio.sleep(0)

    assert closed == [] and not session._patches_closed
    assert id(kept) in session._patch_held


# --- send(): after the commit ------------------------------------------------------------


RENDERED: list[int] = []


class CountedPost(Post):
    @property
    def counted(self) -> str:
        RENDERED.append(self.pk)
        return self.text


TEMPLATES["bc/counted.html"] = "<li>{{ item.counted }}</li>"


@pytest.mark.django_db(transaction=True)
def test_send_renders_and_publishes_once_the_transaction_commits():
    published: list[dict[str, t.Any]] = []

    class Broker:
        async def publish_patch(self, topic, message):
            published.append(message)

    RENDERED.clear()
    set_broker(Broker())  # type: ignore[arg-type]
    try:
        with transaction.atomic():
            Broadcast(BcFeed, "bc-feed").stream_insert(
                "items", CountedPost(1, "kept"), template="bc/counted.html"
            ).send()
            assert (published, RENDERED) == ([], [])
        assert RENDERED == [1] and len(published) == 1

        try:
            with transaction.atomic():
                Broadcast(BcFeed, "bc-feed").stream_insert(
                    "items", CountedPost(2, "rolled back"), template="bc/counted.html"
                ).send()
                raise RuntimeError("roll back")
        except RuntimeError:
            pass
    finally:
        set_broker(None)

    assert RENDERED == [1] and len(published) == 1


@pytest.mark.django_db(transaction=True)
def test_send_outside_a_transaction_sends_at_once():
    published: list[t.Any] = []

    class Broker:
        async def publish_patch(self, topic, message):
            published.append(message)

    set_broker(Broker())  # type: ignore[arg-type]
    try:
        Broadcast(BcFeed, "bc-feed").push_event("posted").send()
    finally:
        set_broker(None)

    assert len(published) == 1


# --- wireview.testing: mail behind the handler (#205), notifications (#201) ----------------


async def _publish_and_write(component: Component, broadcast: Broadcast) -> None:
    """Publish; on a connection, wait until the patch is on the socket: it is written during the handler."""
    session = component.wire.patch_gate.hold.__self__  # type: ignore[union-attr]
    writer = getattr(session, "_patch_writer", None)
    await broadcast.asend()
    if isinstance(session, WireviewSession):
        await eventually(lambda: session._patch_writer is not writer and session._patch_writer.done())  # type: ignore[union-attr]


class BcInserting(BcFeed):
    """Inserts the rows it read one by one; a delete of the first is published between the two."""

    class Meta:
        subscriptions = {"bc-inserting"}

    async def add(self, **_rest):
        await self.stream_insert("items", Post(5, "five"))
        await _publish_and_write(self, Broadcast(BcInserting, "bc-inserting").stream_delete("items", 5))
        await self.stream_insert("items", Post(6, "six"))
        await self.push_event("finished")


@pytest.mark.asyncio
async def test_a_mounted_component_writes_its_handlers_mail_after_a_patch_that_came_during_it():
    """The patch is written as it comes, the handler's mail once the handler is done: the item is not kept (#205)."""
    communicator = await _connect()
    try:
        await _join(communicator, BcInserting, "inserting")
        await _event(communicator, "inserting", "add")
        connection = _stream_ops(await _until(communicator, _finished))
    finally:
        await communicator.disconnect()
    view = await mount(BcInserting, id="inserting")
    view.clear_messages()

    await view.call("add")

    assert connection == [("delete", ["items-5"]), ("insert", ["items-5"]), ("insert", ["items-6"])]
    assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("items")] == connection


TEMPLATES["bc/listener.html"] = "{% load wireview %}<p {% tag_header %}>{{ room }}: {{ heard|join:',' }}</p>"


class BcListener(Component):
    """Hears its room; a notification can move it to another one, and mails what it heard."""

    room: str = "a"
    heard: list[str] = []

    class Meta:
        template_name = "bc/listener.html"

    def get_subscriptions(self) -> set[str]:
        return {f"bc-room-{self.room}"}

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.heard = [*self.heard, kwargs["text"]]
        if move := kwargs.get("move"):
            self.room = move
        await self.push_event("heard", {"text": kwargs["text"]})

    async def shout(self, **_rest):
        """Mails, publishes to its own room, mails again: the notification waits between the two."""
        await self.push_event("before")
        await abroadcast(f"bc-room-{self.room}", text="own")
        await self.push_event("after")

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


def _heard(message: dict[str, t.Any]) -> tuple[str, ...]:
    """A frame as a test compares it: a render by its id, a push_event by its event and text."""
    if message["command"] == "render":
        return ("render", message["payload"]["id"])
    payload = message["payload"]
    return ("push_event", payload["event"], *([payload["payload"]["text"]] if payload["payload"] else []))


def _recorded(view: t.Any) -> list[tuple[str, ...]]:
    return [
        ("render", m["id"]) if m["type"] == "render" else ("push_event", m["event"], *m["payload"].values())
        for m in view.sent_messages
    ]


def _event_answer(message: dict[str, t.Any]) -> bool:
    """The render that answers a user_event: ``call()`` returns the handler's result instead."""
    return message["command"] == "render" and "ref" in message["payload"]


@pytest.mark.asyncio
async def test_a_mounted_component_hears_a_notification_as_a_connection_does():
    """Receiver, render, subscriptions settled again: the move to room b is heard there, not in room a (#201)."""
    communicator = await _connect()
    try:
        await _join(communicator, BcListener, "listener")
        await abroadcast("bc-room-a", text="one", move="b")
        heard = await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "one"))
        await abroadcast("bc-room-a", text="two")
        await abroadcast("bc-room-b", text="three")
        heard += await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "three"))
    finally:
        await communicator.disconnect()
    connection = [_heard(m) for m in heard]
    view = await mount(BcListener, id="listener")

    await abroadcast("bc-room-a", text="one", move="b")
    await abroadcast("bc-room-a", text="two")
    await abroadcast("bc-room-b", text="three")

    assert connection == [
        ("render", "listener"),
        ("push_event", "heard", "one"),
        ("render", "listener"),
        ("push_event", "heard", "three"),
    ]
    assert _recorded(view) == connection
    assert view.component.heard == ["one", "three"]
    assert "b: one,three" in view.render()


@pytest.mark.asyncio
async def test_a_notification_published_during_a_handler_waits_behind_the_mail_sent_before_it():
    """In the channel the handler's mail and the notification are in the order they were sent (#201, #205)."""
    communicator = await _connect()
    try:
        await _join(communicator, BcListener, "listener")
        await _event(communicator, "listener", "shout")
        heard = await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "own"))
    finally:
        await communicator.disconnect()
    connection = [_heard(m) for m in heard if not _event_answer(m)]
    view = await mount(BcListener, id="listener")

    await view.call("shout")

    assert connection == [
        ("push_event", "before"),
        ("render", "listener"),
        ("push_event", "after"),
        ("push_event", "heard", "own"),
    ]
    assert _recorded(view) == connection


@pytest.mark.asyncio
async def test_a_mounted_component_hears_another_mounted_components_broadcast():
    speaker = await mount(BcListener, id="speaker")
    listener = await mount(BcListener, id="listener", room="b")

    await speaker.component.broadcast("bc-room-b", text="hi")

    assert speaker.component.heard == []
    assert listener.component.heard == ["hi"]
    assert [b["channel"] for b in speaker.broadcasts] == ["bc-room-b"]


class BcFailingListener(BcListener):
    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        raise RuntimeError("receiver failed")


@pytest.mark.asyncio
async def test_a_receiver_that_raises_raises_where_the_notification_was_published():
    """A session would recover the component alone; a test is told."""
    await mount(BcFailingListener, id="failing")

    with pytest.raises(RuntimeError, match="receiver failed"):
        await abroadcast("bc-room-a", text="one")


def test_a_component_mounted_by_an_earlier_test_hears_nothing():
    """Its loop is gone: what a later test publishes is not its (#201)."""
    left_behind = asyncio.run(mount(BcListener, id="left"))

    asyncio.run(abroadcast("bc-room-a", text="later"))

    assert left_behind.component.heard == []


# --- the session asks each component in its turn (#201 review) -----------------------------

TEMPLATES["bc/moving-host.html"] = (
    "{% load wireview %}<div {% tag_header %}>{{ room }}"
    "{% live_component 'BcMovingChild' id='bc-moving-child' room=this.room %}</div>"
)
TEMPLATES["bc/moving-child.html"] = "{% load wireview %}<p {% live_tag_header %}>{{ room }}</p>"


class BcMovingHost(Component):
    """Hears room 1; a notification moves it, and its render moves its LiveComponent with it."""

    room: str = "1"

    class Meta:
        template_name = "bc/moving-host.html"
        subscriptions = {"bc-moving-1"}

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.room = kwargs["move"]

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


class BcMovingChild(LiveComponent):
    room: str = "1"

    class Meta:
        template_name = "bc/moving-child.html"

    def get_subscriptions(self) -> set[str]:
        return {f"bc-moving-{self.room}"}

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        await self.push_event("child-heard", {"text": channel})

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


@pytest.mark.asyncio
async def test_a_component_moved_off_a_channel_earlier_in_the_loop_does_not_hear_it():
    """The parent's render hands the child room 2 before the child's turn: it no longer hears room 1."""
    communicator = await _connect()
    try:
        await _join(communicator, BcMovingHost, "moving-host")
        await abroadcast("bc-moving-1", move="2")
        # Room 2 is the child's once the connection settled its subscriptions again
        layer = get_channel_layer()
        await eventually(lambda: "bc-moving-2" in layer.groups)  # type: ignore[union-attr]
        await abroadcast("bc-moving-2", move="2")
        heard = await _until(communicator, lambda m: _heard(m) == ("push_event", "child-heard", "bc-moving-2"))
    finally:
        await communicator.disconnect()

    assert [_heard(m) for m in heard if m["command"] == "push_event"] == [("push_event", "child-heard", "bc-moving-2")]


# --- wireview.testing as a connection: the review of #201/#205 ------------------------------


class BcJoiner(BcListener):
    """Tells its own room it joined, from joined()."""

    async def joined(self):
        await self.broadcast(f"bc-room-{self.room}", text="hello")


@pytest.mark.asyncio
async def test_a_mounted_component_hears_what_its_joined_broadcast():
    """joined()'s broadcast goes out once the subscriptions are settled, so the component hears its own."""
    communicator = await _connect()
    try:
        joined = await _join(communicator, BcJoiner, "joiner")
        heard = joined + await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "hello"))
    finally:
        await communicator.disconnect()
    # The join's own render is the page the mounted component starts from
    connection = [_heard(m) for m in heard[1:] if m["command"] in ("render", "push_event")]
    view = await mount(BcJoiner, id="joiner")

    assert connection == [("render", "joiner"), ("push_event", "heard", "hello")]
    assert _recorded(view) == connection
    assert view.component.heard == ["hello"]


class BcIdle(BcListener):
    """Hears without changing anything: nothing to render."""

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        await self.push_event("idle")


@pytest.mark.asyncio
async def test_a_notification_that_changes_nothing_renders_nothing_on_a_mounted_component():
    """The join drew the page: a notification's render is a diff against it, empty here."""
    communicator = await _connect()
    try:
        await _join(communicator, BcIdle, "idle")
        await abroadcast("bc-room-a", text="one")
        heard = await _until(communicator, lambda m: _heard(m) == ("push_event", "idle"))
    finally:
        await communicator.disconnect()
    connection = [_heard(m) for m in heard]
    view = await mount(BcIdle, id="idle")

    await abroadcast("bc-room-a", text="one")

    assert connection == [("push_event", "idle")]
    assert _recorded(view) == connection


class BcJoinInserting(BcFeed):
    """Inserts in joined(); a delete of that item is published while joined() runs."""

    class Meta:
        subscriptions = {"bc-join-inserting"}

    async def joined(self):
        await self.stream_insert("items", Post(5, "five"))
        await Broadcast(BcJoinInserting, "bc-join-inserting").stream_delete("items", 5).asend()


@pytest.mark.asyncio
async def test_a_mounted_component_writes_a_patch_from_its_join_after_joineds_mail():
    """The join holds the patches until its release, which follows joined()'s mail: the delete lands last."""
    communicator = await _connect()
    try:
        connection = _stream_ops(await _join(communicator, BcJoinInserting, "join-inserting"))
    finally:
        await communicator.disconnect()
    view = await mount(BcJoinInserting, id="join-inserting")

    assert connection == [("insert", ["items-5"]), ("delete", ["items-5"])]
    assert [(op["op"], [item["id"] for item in op["items"]]) for op in view.stream_ops("items")] == connection


class BcHooked(BcFeed):
    """A handle_event hook mails; the handler publishes a patch to its class and mails after it."""

    class Meta:
        subscriptions = {"bc-hooked"}

    async def joined(self):
        self.attach_hook("mail", "handle_event", self._mail)

    async def _mail(self, event: str, payload: dict[str, t.Any]) -> None:
        await self.push_event("hooked")

    async def post(self, **_rest):
        await _publish_and_write(self, Broadcast(BcHooked, "bc-hooked").push_event("posted"))
        await self.push_event("finished")


@pytest.mark.asyncio
async def test_a_mounted_components_handle_event_hook_mails_inside_the_event():
    """The hook runs in the event's message: its mail waits behind a patch written during the handler."""
    communicator = await _connect()
    try:
        await _join(communicator, BcHooked, "hooked")
        await _event(communicator, "hooked", "post")
        heard = await _until(communicator, lambda m: _heard(m) == ("push_event", "finished"))
    finally:
        await communicator.disconnect()
    connection = [_heard(m) for m in heard if m["command"] == "push_event"]
    view = await mount(BcHooked, id="hooked")
    view.clear_messages()

    await view.call("post")

    assert connection == [("push_event", "posted"), ("push_event", "hooked"), ("push_event", "finished")]
    assert [("push_event", m["event"]) for m in view.sent_messages if m["type"] == "push_event"] == connection


class BcTeller(BcListener):
    async def tell(self, room: str = "b", text: str = "", **_rest):
        await self.broadcast(f"bc-room-{room}", text=text)


@pytest.mark.asyncio
async def test_a_mounted_components_broadcast_reaches_a_socket_of_its_process():
    """self.broadcast() from a mount is published as a page's is, and still recorded (#201)."""
    communicator = await _connect()
    try:
        await _join(communicator, BcListener, "socket-listener")
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {
                    "id": "socket-listener",
                    "command": "shout",
                    "implicit_args": {},
                    "explicit_args": {},
                    "ref": 1,
                },
            }
        )
        await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "own"))
        view = await mount(BcTeller, id="teller")

        await view.call("tell", room="a", text="from a mount")

        heard = await _until(communicator, lambda m: m["command"] == "push_event")
    finally:
        await communicator.disconnect()

    assert _heard(heard[-1]) == ("push_event", "heard", "from a mount")
    assert [(b["channel"], b["kwargs"]) for b in view.broadcasts] == [("bc-room-a", {"text": "from a mount"})]
    assert view.component.heard == ["from a mount"], "and the mount hears its own room, as the socket does"


@pytest.mark.asyncio
async def test_a_component_whose_page_a_push_left_hears_nothing_more():
    """follow_push() to another path mounts the destination; the page it left is gone, as after a boosted move."""
    view = await mount(BcListener, id="leaving", path="/here/")
    await view.component.wire.push_to("/elsewhere/")
    await view.follow_push(BcIdle, live_session=None, id="arrived")

    await abroadcast("bc-room-a", text="after leaving")

    assert view.component.heard == []
    assert view.is_frozen


class BcStickyListener(BcListener):
    class Meta:
        sticky = True


@pytest.mark.asyncio
async def test_a_sticky_component_hears_on_after_a_push_to_another_page():
    """A sticky component survives a boosted move within its live_session."""
    view = await mount(BcStickyListener, id="sticky", path="/here/")
    await view.component.wire.push_to("/elsewhere/")
    await view.follow_push(BcIdle, live_session=None, id="arrived")

    await abroadcast("bc-room-a", text="after the move")

    assert view.component.heard == ["after the move"]


# --- an event call() handled before a notification (#201 review) --------------------------


class BcQuietListener(BcListener):
    async def quiet(self, **_rest):
        self.skip_render()


class BcBumping(BcIdle):
    async def bump(self, **_rest):
        self.heard = ["bumped"]


class BcBumpingListener(BcListener):
    async def bump(self, **_rest):
        self.heard = ["bumped"]


def _drawn(diff: dict[str, t.Any]) -> dict[str, t.Any]:
    """A render's diff but the signed state, which differs between any two renders."""
    return {k: v for k, v in diff.items() if k != "0"}


def _rendered(message: dict[str, t.Any]) -> tuple[t.Any, ...]:
    """A frame with what it drew: a render by its diff, a push_event by its event."""
    if message["command"] == "render":
        return ("render", _drawn(message["payload"]["diff"]))
    return ("push_event", message["payload"]["event"])


def _recorded_renders(view: t.Any) -> list[tuple[t.Any, ...]]:
    return [
        ("render", _drawn(m["diff"])) if m["type"] == "render" else ("push_event", m["event"])
        for m in view.sent_messages
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cls", "handler", "last"),
    [
        (BcQuietListener, "quiet", ("push_event", "heard")),
        (BcBumping, "bump", ("push_event", "idle")),
        (BcBumpingListener, "bump", ("push_event", "heard")),
    ],
    ids=["skipped", "changed", "both-changed"],
)
async def test_an_events_render_is_not_a_notifications_on_a_mounted_component(cls, handler, last):
    """A session renders after each event; call() does not. With the page taken, a notification renders the
    event's own first: what it changed is not the notification's, and a skip_render() is not either."""
    communicator = await _connect()
    try:
        await _join(communicator, cls, "evented")
        await _event(communicator, "evented", handler)
        await abroadcast("bc-room-a", text="one")
        heard = await _until(communicator, lambda m: m["command"] == "push_event" and _rendered(m) == last)
    finally:
        await communicator.disconnect()
    # A render with no diff is the event's answer to the page; a mount records none
    connection = [_rendered(m) for m in heard if not (m["command"] == "render" and m["payload"]["diff"] is None)]
    view = await mount(cls, id="evented")
    await view.render_diff()

    await view.call(handler)
    await abroadcast("bc-room-a", text="one")

    assert (
        connection
        == {
            BcQuietListener: [("render", {"2": "one"}), ("push_event", "heard")],
            BcBumping: [("render", {"2": "bumped"}), ("push_event", "idle")],
            BcBumpingListener: [("render", {"2": "bumped"}), ("render", {"2": "bumped,one"}), ("push_event", "heard")],
        }[cls]
    )
    assert _recorded_renders(view) == connection


class BcJoinTalker(BcListener):
    """joined() publishes a patch to its class, then broadcasts to its own room."""

    async def joined(self):
        await Broadcast(BcJoinTalker, "bc-room-a").push_event("patch").asend()
        await self.broadcast("bc-room-a", text="self")

    def get_subscriptions(self) -> set[str]:
        return {"bc-room-a"}


@pytest.mark.asyncio
async def test_a_mounted_component_hears_joineds_broadcast_before_the_join_lets_its_patches_go():
    """The join's release follows the notification joined() brought: its render comes ahead of the held patch."""
    communicator = await _connect()
    try:
        joined = await _join(communicator, BcJoinTalker, "join-talker")
        heard = joined + await _until(communicator, lambda m: _heard(m) == ("push_event", "heard", "self"))
    finally:
        await communicator.disconnect()
    # The join's own render is the page the mounted component starts from
    connection = [_heard(m) for m in heard[1:] if m["command"] in ("render", "push_event")]
    view = await mount(BcJoinTalker, id="join-talker")

    assert connection == [("render", "join-talker"), ("push_event", "patch"), ("push_event", "heard", "self")]
    assert _recorded(view) == connection


# --- tutorial 09 "복잡한 상태": the broadcast fills the list, once (#201 review) ------------

TEMPLATES["bc/tutorial-todo.html"] = (
    "{% load wireview %}<ul {% tag_header %}>{% for item in items %}<li>{{ item.text }}</li>{% endfor %}</ul>"
)


class BcTutorialTodo(Component):
    """Tutorial 03's list: add_item() saves the row, mutation() puts it in."""

    items: list[TodoItem] = []

    class Meta:
        template_name = "bc/tutorial-todo.html"
        subscriptions = {"todo.item"}

    async def joined(self):
        self.items = [item async for item in TodoItem.objects.all()]

    async def add_item(self, text: str):
        await TodoItem.objects.acreate(text=text)

    async def mutation(self, channel: str, action: ModelAction, instance: TodoItem):
        if action == ModelAction.CREATED:
            self.items.insert(0, instance)

    @classmethod
    def _get_template(cls, template_name=None):
        return _template(cls, template_name)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_tutorials_list_is_filled_by_the_model_broadcast_once():
    """docs/tutorials/09: the test does not call mutation() itself -- the broadcast did."""
    view = await mount(BcTutorialTodo, id="tutorial-todo")

    await view.call("add_item", text="New item")

    assert [item.text for item in view.component.items] == ["New item"]
    assert "<li>New item</li>" in view.render()
