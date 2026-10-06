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

from wireview import JS, Broadcast, Component, LiveComponent, mount, telemetry
from wireview.consumer import WireviewConsumer
from wireview.core import patches
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
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
    "bc/perms.html": "<li>{% if perms.auth.add_user %}yes{% endif %}</li>",
    "bc/language.html": "{% load i18n tz %}{% get_current_language as L %}{% get_current_timezone as TZ %}"
    "<li>{{ L }} {{ TZ }}</li>",
    "bc/host.html": "{% load wireview %}<div {% tag_header %}>{% live_component 'BcLive' id='bc-live' %}</div>",
    "bc/live.html": '{% load wireview %}<ul {% live_tag_header %} wire-stream="items"></ul>',
    "bc/live_item.html": '<li id="items-{{ item.pk }}">{{ item.text }}</li>',
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


class BcFailing(BcFeed):
    async def joined(self):
        raise RuntimeError("joined went wrong")


# --- helpers -----------------------------------------------------------------------------


def _state(cls: type[Component], id: str) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))


async def _connect() -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
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
            ("bc/perms.html", "perms"),
            ("bc/bind_myself.html", "this"),
        ],
    )
    def test_reading_the_viewer_raises_and_names_what_was_read(self, template, name):
        with pytest.raises(ImproperlyConfigured, match=f"read '{name}'"):
            Broadcast(BcFeed, "bc-feed").stream_insert("items", Post(1, "x"), template=template)._frames()

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
