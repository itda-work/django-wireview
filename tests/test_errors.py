"""Server code that raises costs its component, not the connection (#94).

A handler that raised used to escape the consumer and close the socket: the
client reconnected and joined every component on the page again, with no word
of what went wrong. Now the component that raised is discarded and the client
joins it again from the state its element still carries -- the state before
the event. Every other component, and the socket, carry on.

A join that fails is not retried (it would fail again); the client marks the
element. A message no client of ours sends is logged and dropped. A client too
old to know ``error`` gets what every client used to get.
"""

import json
import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import ERRORS_SINCE, PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

CALLS: list[tuple[str, str]] = []
_TEMPLATE = "{% load wireview %}<p {% tag_header %}>{{ count }}</p>"


class HaltWhenAsked:
    @staticmethod
    async def on_mount(component, params, session):
        return {"halt": True} if component.halt else {"cont": True}


class ErrorProbe(Component):
    class Meta:
        on_mount = [HaltWhenAsked]

    count: int = 0
    fail_to_join: bool = False
    fail_on_params: bool = False
    halt: bool = False

    async def joined(self):
        if self.fail_to_join:
            raise RuntimeError("joined went wrong")

    async def params_changed(self, params, uri):
        if self.fail_on_params:
            raise RuntimeError("params went wrong")

    async def bump(self, **_rest):
        self.count += 1

    async def bump_then_raise(self, **_rest):
        self.count += 100
        raise RuntimeError("handler went wrong")

    async def leaving(self):
        CALLS.append(("leaving", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(_TEMPLATE)


class ErrorProbeListener(ErrorProbe):
    class Meta:
        subscriptions = {"error-probe-topic"}

    async def notification(self, channel, **kwargs):
        if kwargs.get("fail") == self.id:
            raise RuntimeError("receiver went wrong")
        self.count += 1


class ErrorProbeParent(Component):
    async def leaving(self):
        CALLS.append(("leaving", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}></div>")


class ErrorProbeChild(LiveComponent):
    async def leaving(self):
        CALLS.append(("leaving", self.id))

    async def update(self, **assigns):
        raise RuntimeError("update went wrong")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% live_tag_header %}></div>")


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


def _state(id: str, **fields: t.Any) -> str:
    return sign_state(ErrorProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **fields))


async def _connect(vsn: int = PROTOCOL_VERSION) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={vsn}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _send(communicator: WebsocketCommunicator, _command: str, **payload: t.Any) -> None:
    await communicator.send_json_to({"command": _command, "payload": payload})


async def _join(communicator: WebsocketCommunicator, id: str, ref: int | None = None, **fields: t.Any) -> None:
    extra = {} if ref is None else {"ref": ref}
    await _send(communicator, "join", name="ErrorProbe", state=_state(id, **fields), children={}, **extra)


async def _event(communicator: WebsocketCommunicator, id: str, command: str, **extra: t.Any) -> None:
    await _send(communicator, "user_event", id=id, command=command, implicit_args={}, explicit_args={}, **extra)


async def _next(communicator: WebsocketCommunicator, *commands: str) -> dict[str, t.Any]:
    """The next message whose command is one of ``commands``, skipping bookkeeping."""
    for _ in range(10):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] in commands:
            return message
    raise AssertionError(f"no {commands} message")


async def test_a_handler_that_raises_is_joined_again_and_the_socket_stays():
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        await _next(communicator, "render")

        await _event(communicator, "e-1", "bump_then_raise", ref=4)
        error = await _next(communicator, "error", "render")

        # The client joins again with the state its element carries: from
        # before the event. The same socket answers it and the next event.
        await _join(communicator, "e-1")
        rejoined = await _next(communicator, "render")
        await _event(communicator, "e-1", "bump", ref=5)
        answer = await _next(communicator, "render")
    finally:
        await communicator.disconnect()

    assert error == {"command": "error", "payload": {"id": "e-1", "during": "event", "ref": 4}}
    assert ("leaving", "e-1") in CALLS
    assert rejoined["payload"]["id"] == "e-1"
    assert answer["payload"]["ref"] == 5
    assert answer["payload"]["diff"] is not None


async def test_the_other_components_on_the_connection_are_untouched():
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        await _next(communicator, "render")
        await _join(communicator, "e-2")
        await _next(communicator, "render")

        await _event(communicator, "e-1", "bump_then_raise")
        await _next(communicator, "error")
        await _event(communicator, "e-2", "bump", ref=9)
        answer = await _next(communicator, "render")
        calls = list(CALLS)
    finally:
        await communicator.disconnect()

    assert calls == [("leaving", "e-1")]
    assert answer["payload"]["id"] == "e-2" and answer["payload"]["diff"] is not None


async def test_a_client_older_than_error_gets_the_socket_closed_as_before():
    communicator = await _connect(vsn=ERRORS_SINCE - 1)
    try:
        await _join(communicator, "e-1")
        await _next(communicator, "render")
        await _event(communicator, "e-1", "bump_then_raise")
        closed = await communicator.receive_output(timeout=5)
    finally:
        await communicator.disconnect()

    assert closed == {"type": "websocket.close", "code": 1011}


async def test_a_join_that_raises_is_marked_not_retried():
    communicator = await _connect()
    try:
        await _join(communicator, "e-1", fail_to_join=True)
        error = await _next(communicator, "error", "remove", "render")
        # Nothing was left behind for an event to reach: no handler runs, and
        # the answer is an empty render, so the page's loading state ends.
        await _event(communicator, "e-1", "bump", ref=2)
        answer = await _next(communicator, "render", "error")
        await _join(communicator, "e-2")
        after = await _next(communicator, "render", "error")
        calls = list(CALLS)
    finally:
        await communicator.disconnect()

    assert error == {"command": "error", "payload": {"id": "e-1", "during": "join"}}
    assert answer == {"command": "render", "payload": {"id": "e-1", "diff": None, "ref": 2}}
    assert calls == [("leaving", "e-1")]
    assert after["payload"]["id"] == "e-2"


class ErrorProbeHolder(Component):
    """Draws an ErrorProbe that cannot join, and renders again on ``bump``."""

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{{ this.count }}"
            "{% component 'ErrorProbe' id='e-held' fail_to_join=True %}</div>"
        )


async def test_a_parents_render_draws_a_component_whose_join_failed_and_leaves_it_to_the_page():
    # The ``_join_failed`` contract on the server: one ``error`` for the join, the
    # instance gone, and no retry. The parent's next render draws the element
    # again, over an instance its template pass builds and nothing joins: no
    # joined(), no ``error`` again, nothing that would loop. Nothing reaches
    # that instance either (test_what_a_failed_join_left_gets_no_event_hook_or_upload).
    communicator = await _connect()
    try:
        holder = sign_state(ErrorProbeHolder(user=AnonymousUser(), wire=WireviewMeta(params={}), id="h-1"))
        await _send(communicator, "join", name="ErrorProbeHolder", state=holder, children={}, ref=1)
        await _next(communicator, "joined")
        await _join(communicator, "e-held", ref=2, fail_to_join=True)
        error = await _next(communicator, "error", "render")
        await _event(communicator, "h-1", "bump", ref=3)
        heard = [await communicator.receive_json_from(timeout=5)]
        # Only a check that nothing else follows the render: no message says so
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
    finally:
        await communicator.disconnect()

    assert error == {"command": "error", "payload": {"id": "e-held", "during": "join", "ref": 2}}
    assert [(m["command"], m["payload"]["id"]) for m in heard] == [("render", "h-1")]


class ErrorProbeNest(Component):
    """Fails to join while ``failing`` is set; holds ``e-own-leaf`` unless a slot fills it."""

    failing: t.ClassVar[bool] = True

    async def joined(self):
        if ErrorProbeNest.failing:
            raise RuntimeError("the nest's joined went wrong")

    async def poke(self, **_rest):
        CALLS.append(("poke", self.id))

    async def handle_hook_event(self, hook_id, event, payload):
        CALLS.append(("hook", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %} class='nest'>"
            "{% if slots.body %}{% render_slot 'body' %}"
            "{% else %}{% live_component 'ErrorProbeLeaf' id='e-own-leaf' %}{% endif %}</div>"
        )


class ErrorProbeLeaf(LiveComponent):
    async def poke(self, **_rest):
        CALLS.append(("poke", self.id))

    async def handle_hook_event(self, hook_id, event, payload):
        CALLS.append(("hook", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}></i>")


class ErrorProbeHost(Component):
    """Two nests: one holding its own LiveComponent, one with the host's in its slot."""

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{{ this.count }}"
            "{% component 'ErrorProbeNest' id='e-own-nest' %}"
            "{% component_block 'ErrorProbeNest' id='e-slot-nest' %}"
            "{% fill body %}{% live_component 'ErrorProbeLeaf' id='e-slot-leaf' %}{% endfill %}"
            "{% endcomponent %}</div>"
        )


@pytest.fixture
def failing_nests():
    ErrorProbeNest.failing = True
    yield
    ErrorProbeNest.failing = True


async def _host_with_failed_nests(communicator: WebsocketCommunicator) -> None:
    host = sign_state(ErrorProbeHost(user=AnonymousUser(), wire=WireviewMeta(params={}), id="e-host"))
    await _send(communicator, "join", name="ErrorProbeHost", state=host, children={}, ref=1)
    await _next(communicator, "joined")
    for ref, id in ((2, "e-own-nest"), (3, "e-slot-nest")):
        nest = sign_state(ErrorProbeNest(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))
        await _send(communicator, "join", name="ErrorProbeNest", state=nest, children={}, ref=ref)
        assert (await _next(communicator, "error", "render"))["command"] == "error"


async def _heard_until_host_answers(communicator: WebsocketCommunicator, ref: int) -> list[dict[str, t.Any]]:
    """What the server sent up to the host's answer to a ``bump`` sent last, which it handles after the rest."""
    await _event(communicator, "e-host", "bump", ref=ref)
    heard = [await communicator.receive_json_from(timeout=5)]
    while (heard[-1]["command"], heard[-1]["payload"].get("ref")) != ("render", ref):
        heard.append(await communicator.receive_json_from(timeout=5))
    return heard


async def test_what_a_failed_join_left_gets_no_event_hook_or_upload(failing_nests):
    # The parent's next render builds the nest again under the id, and its own
    # LiveComponent under it: instances whose joined() never ran. The page took
    # the element up again -- the server's HTML had taken its mark away -- and
    # a click or a hook reached them. Now the server refuses them, and answers
    # an event with an empty render so the page's loading state ends. The
    # LiveComponent in the other nest's slot is the host's, and is not refused.
    communicator = await _connect()
    try:
        await _host_with_failed_nests(communicator)
        # Before the host renders again: the ids the failure removed, the nest's
        # and its LiveComponent's
        await _event(communicator, "e-own-nest", "poke", ref=4)
        await _event(communicator, "e-own-leaf", "poke", ref=40)
        before = [await communicator.receive_json_from(timeout=5) for _ in range(2)]
        drawn = await _heard_until_host_answers(communicator, 5)
        for ref, id in ((6, "e-own-nest"), (7, "e-own-leaf"), (8, "e-slot-leaf")):
            await _event(communicator, id, "poke", ref=ref)
        await _send(communicator, "hook_event", component_id="e-own-nest", hook_id="h", event="e", payload={}, ref="r1")
        await _send(communicator, "hook_event", component_id="e-own-leaf", hook_id="h", event="e", payload={}, ref="r2")
        entry = {"ref": "u1", "name": "a.txt", "size": 1, "type": "text/plain"}
        await _send(communicator, "upload_register", id="e-own-nest", name="files", entries=[entry])
        after = await _heard_until_host_answers(communicator, 9)
    finally:
        await communicator.disconnect()

    assert before == [
        {"command": "render", "payload": {"id": "e-own-nest", "diff": None, "ref": 4}},
        {"command": "render", "payload": {"id": "e-own-leaf", "diff": None, "ref": 40}},
    ]
    # The host's HTML marks both nests, on top of their own class
    host = json.dumps(drawn[-1]["payload"]["diff"])
    assert host.count("wire-join-failed") == 2, host
    assert host.count("class='nest'") == 2, "the template's own class stays"
    answers = [(m["command"], m["payload"].get("id"), m["payload"].get("diff")) for m in after[:3]]
    assert answers[:2] == [("render", "e-own-nest", None), ("render", "e-own-leaf", None)]
    assert answers[2][:2] == ("render", "e-slot-leaf")
    assert [m["command"] for m in after[3:]] == ["render"], "nothing for the hooks or the upload"
    assert CALLS == [("poke", "e-slot-leaf")]


async def test_a_join_under_the_id_of_a_failed_one_tries_it_again(failing_nests):
    # New DOM under the id -- a boosted navigation's page -- joins again, and
    # the server takes it: what the failure kept out is the page's again.
    communicator = await _connect()
    try:
        await _host_with_failed_nests(communicator)
        drawn = await _heard_until_host_answers(communicator, 4)
        assert "wire-join-failed" in str(drawn[-1]["payload"]["diff"])

        ErrorProbeNest.failing = False
        nest = sign_state(ErrorProbeNest(user=AnonymousUser(), wire=WireviewMeta(params={}), id="e-own-nest"))
        await _send(communicator, "join", name="ErrorProbeNest", state=nest, children={}, ref=5)
        joined = await _next(communicator, "render", "error")
        await _event(communicator, "e-own-nest", "poke", ref=6)
        await _event(communicator, "e-own-leaf", "poke", ref=7)
        redrawn = await _heard_until_host_answers(communicator, 8)
    finally:
        await communicator.disconnect()

    assert (joined["command"], joined["payload"]["id"]) == ("render", "e-own-nest")
    assert "wire-join-failed" not in str(joined)
    assert CALLS == [("poke", "e-own-nest"), ("poke", "e-own-leaf")]
    # One failure left: the slot's nest
    assert str(redrawn[-1]["payload"]["diff"]).count("wire-join-failed") == 1


JOINED_LEAVES: list[int] = []


class ErrorPendingLeaf(LiveComponent):
    async def joined(self):
        JOINED_LEAVES.append(id(self))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}></i>")


class ErrorPendingNest(ErrorProbeNest):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{% live_component 'ErrorPendingLeaf' id='e-pend-leaf' %}</div>"
        )


class ErrorPendingHost(Component):
    shown: bool = True

    async def toggle(self, **_rest):
        self.shown = not self.shown

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>"
            "{% if this.shown %}{% component 'ErrorPendingNest' id='e-pend-nest' %}{% endif %}</div>"
        )


async def test_a_live_component_a_failed_join_took_away_owes_nothing_to_the_next_instance(failing_nests):
    # The host's pass built the nest and the LiveComponent in it, which waited
    # for the nest's render to run its joined(). The nest's join failed and both
    # went, but the LiveComponent still waited: once the page tried the id again
    # over the instances the host drew since, the nest's first render ran
    # joined() for the one that was gone as well as for its own.
    JOINED_LEAVES.clear()
    communicator = await _connect()
    meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
    try:
        host = sign_state(ErrorPendingHost(**meta, id="e-pend-host"))
        await _send(communicator, "join", name="ErrorPendingHost", state=host, children={}, ref=1)
        await _next(communicator, "joined")
        nest = sign_state(ErrorPendingNest(**meta, id="e-pend-nest"))
        await _send(communicator, "join", name="ErrorPendingNest", state=nest, children={}, ref=2)
        assert (await _next(communicator, "error", "render"))["command"] == "error"
        # The host hides it, the page lets it go, and the host draws it again
        await _event(communicator, "e-pend-host", "toggle", ref=3)
        await _next(communicator, "render")
        await _send(communicator, "leave", id="e-pend-nest")
        await _event(communicator, "e-pend-host", "toggle", ref=4)
        await _next(communicator, "render")

        ErrorProbeNest.failing = False
        await _send(communicator, "join", name="ErrorPendingNest", state=nest, children={}, ref=5)
        answer = await _next(communicator, "render", "error")
        await _next(communicator, "joined")
    finally:
        await communicator.disconnect()

    assert (answer["command"], answer["payload"]["id"]) == ("render", "e-pend-nest")
    assert len(JOINED_LEAVES) == 1, "joined() ran for an instance the repository no longer held"


class ErrorProbeHearingLeaf(LiveComponent):
    """Hears what its owner hears; its joined() is what a render of a refused owner would run."""

    class Meta:
        subscriptions = {"error-probe-refused"}

    async def joined(self):
        CALLS.append(("joined", self.id))

    async def notification(self, channel, **kwargs):
        CALLS.append(("notification", self.id))

    async def params_changed(self, params, uri):
        CALLS.append(("params_changed", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<i {% live_tag_header %}></i>")


class ErrorProbeHearingNest(ErrorProbeNest):
    class Meta:
        subscriptions = {"error-probe-refused"}

    async def notification(self, channel, **kwargs):
        CALLS.append(("notification", self.id))

    async def params_changed(self, params, uri):
        CALLS.append(("params_changed", self.id))

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>"
            "{% live_component 'ErrorProbeHearingLeaf' id='e-hearing-leaf' %}</div>"
        )


class ErrorProbeHearingHost(ErrorProbeHost):
    """Hears the same topic and the same params as its nest, and defers to it."""

    class Meta:
        subscriptions = {"error-probe-refused"}

    async def notification(self, channel, **kwargs):
        CALLS.append(("notification", self.id))

    async def params_changed(self, params, uri):
        CALLS.append(("params_changed", self.id))

    async def call(self, **_rest):
        await self.wire.defer("e-hearing-nest", ErrorProbeHearingNest.poke)

    @classmethod
    def _get_template(cls, template_name=None):
        return Template(
            "{% load wireview %}<div {% tag_header %}>{{ this.count }}"
            "{% component 'ErrorProbeHearingNest' id='e-hearing-nest' %}</div>"
        )


async def test_what_a_failed_join_left_hears_no_broadcast_params_or_deferred_call(failing_nests):
    # Events, hooks and uploads were refused, but every other path that runs a
    # component's code still found the instance the host's pass built: a
    # broadcast, params_changed (a message the page sends) and wire.defer. Each
    # ran its code and rendered it, and that render ran joined() for the
    # LiveComponent the instance owns. The host hears all three, so they came.
    from wireview import utils

    communicator = await _connect()
    try:
        host = sign_state(ErrorProbeHearingHost(user=AnonymousUser(), wire=WireviewMeta(params={}), id="e-host"))
        await _send(communicator, "join", name="ErrorProbeHearingHost", state=host, children={}, ref=1)
        await _next(communicator, "joined")
        meta = {"user": AnonymousUser(), "wire": WireviewMeta(params={})}
        nest = sign_state(ErrorProbeHearingNest(**meta, id="e-hearing-nest"))
        await _send(communicator, "join", name="ErrorProbeHearingNest", state=nest, children={}, ref=2)
        assert (await _next(communicator, "error", "render"))["command"] == "error"
        drawn = await _heard_until_host_answers(communicator, 3)
        assert "wire-join-failed" in json.dumps(drawn[-1]["payload"]["diff"])
        CALLS.clear()

        await utils.asend_to("error-probe-refused", "notification", kwargs={"n": 1})
        await _send(communicator, "params_changed", params={"q": "1"}, uri="?q=1")
        await _event(communicator, "e-host", "call", ref=4)
        heard = await _heard_until_host_answers(communicator, 5)
    finally:
        await communicator.disconnect()

    assert sorted(CALLS) == [("notification", "e-host"), ("params_changed", "e-host")]
    assert {m["payload"].get("id") for m in heard} == {"e-host"}, heard
    assert "joined" not in [m["command"] for m in heard]


async def test_a_parent_update_does_not_reach_a_live_component_of_a_failed_join():
    consumer, outbound = _consumer()
    consumer.repo.join_failed("root", [])
    consumer.repo.build("ErrorProbeParent", {"id": "root"})
    child = consumer.repo.build("ErrorProbeChild", {"id": "child"})
    child._parent_id = "root"  # type: ignore[union-attr]

    # ErrorProbeChild.update() raises: reaching it would cost the root an ``error``
    await consumer.component_update_live_component("root", "child", {"x": 1})

    assert outbound.commands == []
    assert consumer.repo.get("child") is child


async def test_a_failed_join_is_not_rendered_on_its_own_nor_its_live_components_joined():
    # Its parent's render draws it inline. A render of its own would run its
    # pass and then joined() for the LiveComponent the pass built.
    consumer, outbound = _consumer()
    consumer.repo.join_failed("e-hearing-nest", [])
    nest = consumer.repo.build("ErrorProbeHearingNest", {"id": "e-hearing-nest"})

    await consumer.send_render(nest, acknowledge=True)

    assert outbound.commands == []
    assert CALLS == []


async def test_a_failed_join_takes_no_upload_command_even_with_uploads_set_up():
    # The instance a parent's pass built under the id of a failed join, with an
    # upload set up and an entry in it -- what refusing the upload commands is
    # for. Without a registry, every upload command ends there anyway.
    from wireview.features.uploads import UploadEntry, UploadStatus

    consumer, outbound = _consumer()
    consumer.repo.join_failed("u-1", [])
    component = consumer.repo.build("ErrorProbe", {"id": "u-1"})
    component.allow_upload("files", max_entries=2)
    registry = component._upload_registry
    assert registry is not None
    entry = UploadEntry(ref="0", upload_name="files", client_name="a.txt", client_size=1, client_type="text/plain")
    registry.add_entry("files", entry)

    new = {"ref": "1", "name": "b.txt", "size": 1, "type": "text/plain"}
    await consumer.command_upload_register("u-1", "files", [new])
    await consumer.command_upload_complete("u-1", "files", "0")
    await consumer.command_upload_cancel("u-1", "files", "0")

    assert outbound.commands == []
    assert registry.get_entry("files", "1") is None
    assert entry.status is UploadStatus.PENDING


async def test_a_join_that_raises_after_its_first_render_is_answered_twice():
    # The URL's params reach a joining component after the render that answers
    # the join; if that raises, the join has failed too, and says so. The page
    # hears two things for one join, so nothing on it may count answers (#137).
    communicator = await _connect()
    try:
        await _send(communicator, "params_changed", params={"q": "x"}, uri="?q=x")
        await _join(communicator, "e-1", fail_on_params=True)
        first = await _next(communicator, "render", "error", "remove")
        second = await _next(communicator, "render", "error", "remove")
        await _join(communicator, "e-2")
        after = await _next(communicator, "render", "error")
    finally:
        await communicator.disconnect()

    assert first["command"] == "render"
    assert first["payload"]["id"] == "e-1"
    assert "vsn" in first["payload"]
    assert second == {"command": "error", "payload": {"id": "e-1", "during": "join"}}
    assert after["payload"]["id"] == "e-2"


async def test_both_answers_to_a_join_carry_its_ref():
    """#139: a page that sent a second join under the id tells the first one's answers by their ref.

    Without it the first join's ``error`` marked the element the second join was
    for, and dropped the component the second join's render was for.
    """
    communicator = await _connect()
    try:
        await _send(communicator, "params_changed", params={"q": "x"}, uri="?q=x")
        await _join(communicator, "e-1", ref=7, fail_on_params=True)
        first = await _next(communicator, "render", "error", "remove")
        second = await _next(communicator, "render", "error", "remove")
        await _join(communicator, "e-1", ref=8)
        again = await _next(communicator, "render", "error")
    finally:
        await communicator.disconnect()

    assert (first["command"], first["payload"]["ref"]) == ("render", 7)
    assert second == {"command": "error", "payload": {"id": "e-1", "during": "join", "ref": 7}}
    assert (again["command"], again["payload"]["ref"]) == ("render", 8)


async def test_a_join_that_cannot_mount_answers_with_its_ref():
    communicator = await _connect()
    try:
        await _join(communicator, "e-1", ref=3, fail_to_join=True)
        message = await _next(communicator, "error", "remove", "render")
    finally:
        await communicator.disconnect()

    assert message == {"command": "error", "payload": {"id": "e-1", "during": "join", "ref": 3}}


async def test_a_join_without_a_ref_is_answered_without_one():
    # What an older client sends, and a new one before the server announced itself
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        message = await _next(communicator, "render")
    finally:
        await communicator.disconnect()

    assert "ref" not in message["payload"]


async def test_every_other_answer_to_a_join_carries_its_ref():
    """#146: the ``joined`` that ends a join, and the ``remove`` of one that halted, name it too.

    A halted join's ``remove`` that reached the page after its next join under
    the id took the new element away; a replaced join's ``joined`` started the
    new element's infinite scroll.
    """
    communicator = await _connect()
    try:
        await _join(communicator, "e-1", ref=5)
        await _next(communicator, "render")
        joined = await _next(communicator, "joined")
        await _join(communicator, "e-2", ref=6, halt=True)
        removed = await _next(communicator, "remove", "render")
    finally:
        await communicator.disconnect()

    assert joined == {"command": "joined", "payload": {"id": "e-1", "ref": 5}}
    assert removed == {"command": "remove", "payload": {"id": "e-2", "ref": 6}}


async def test_a_join_refused_with_a_reload_answers_with_its_ref():
    communicator = await _connect()
    try:
        await _send(communicator, "join", name="ErrorProbe", state="not-signed", children={}, ref=9)
        message = await _next(communicator, "reload")
    finally:
        await communicator.disconnect()

    assert message["payload"]["ref"] == 9


async def test_the_other_answers_to_a_join_without_a_ref_carry_none():
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        joined = await _next(communicator, "joined")
        await _join(communicator, "e-2", halt=True)
        removed = await _next(communicator, "remove", "render")
        await _send(communicator, "join", name="ErrorProbe", state="not-signed", children={})
        reload = await _next(communicator, "reload")
    finally:
        await communicator.disconnect()

    assert joined["payload"] == {"id": "e-1"}
    assert removed["payload"] == {"id": "e-2"}
    assert "ref" not in reload["payload"]


async def test_a_join_that_raises_removes_the_element_for_an_older_client():
    communicator = await _connect(vsn=ERRORS_SINCE - 1)
    try:
        await _join(communicator, "e-1", fail_to_join=True)
        message = await _next(communicator, "error", "remove", "render")
    finally:
        await communicator.disconnect()

    assert message == {"command": "remove", "payload": {"id": "e-1"}}


@pytest.mark.parametrize(
    "message",
    [
        {"command": "no_such_command", "payload": {}},
        {"command": "user_event", "payload": {"id": "e-1"}},
        {"command": "user_event", "payload": {"id": "e-1", "command": "bump", "surprise": 1}},
        {"command": "join"},
        {"payload": {}},
        {"command": "../leave", "payload": {"id": "e-1"}},
    ],
)
async def test_a_message_no_client_sends_is_dropped_and_the_socket_stays(message):
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        await _next(communicator, "render")
        await communicator.send_json_to(message)
        await _event(communicator, "e-1", "bump", ref=3)
        answer = await _next(communicator, "render")
    finally:
        await communicator.disconnect()

    assert answer["payload"]["ref"] == 3 and answer["payload"]["diff"] is not None


@pytest.mark.parametrize("handler", ["_private", "no_such_handler", "model_dump", "joined"])
async def test_an_event_naming_no_handler_is_answered_with_nothing_changed(handler):
    communicator = await _connect()
    try:
        await _join(communicator, "e-1")
        await _next(communicator, "render")
        await _event(communicator, "e-1", handler, ref=6)
        answer = await _next(communicator, "render", "error")
    finally:
        await communicator.disconnect()

    # The loading state the event started is cleared and its ref settled.
    assert answer == {"command": "render", "payload": {"id": "e-1", "diff": None, "ref": 6}}


# The same recovery on every path that runs component code, without a socket.


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


def _consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


async def test_a_raising_receiver_costs_only_its_own_component():
    consumer, outbound = _consumer()
    consumer.repo.build("ErrorProbeListener", {"id": "n-1"})
    consumer.repo.build("ErrorProbeListener", {"id": "n-2"})

    await consumer.notification({"channel": "error-probe-topic", "kwargs": {"fail": "n-1"}})

    assert ("error", {"id": "n-1", "during": "event"}) in outbound.commands
    assert consumer.repo.get("n-1") is None
    assert consumer.repo.get("n-2") is not None
    assert any(command == "render" and payload["id"] == "n-2" for command, payload in outbound.commands)


async def test_a_live_component_that_raises_rejoins_its_root_with_the_whole_tree():
    consumer, outbound = _consumer()
    root = consumer.repo.build("ErrorProbeParent", {"id": "root"})
    child = consumer.repo.build("ErrorProbeChild", {"id": "child"})
    child._parent_id = "root"  # type: ignore[union-attr]
    # The page joined the root, and its render the child: both get leaving()
    root.wire.has_joined = child.wire.has_joined = True

    await consumer.component_update_live_component("root", "child", {"x": 1})

    assert outbound.commands == [("error", {"id": "root", "during": "event"})]
    assert consumer.repo.get("root") is None and consumer.repo.get("child") is None
    assert sorted(CALLS) == [("leaving", "child"), ("leaving", "root")]


async def test_params_changed_that_raises_spares_the_components_after_it():
    consumer, outbound = _consumer()
    consumer.repo.build("ErrorProbe", {"id": "p-1"})
    consumer.repo.build("ErrorProbe", {"id": "p-2"})
    first = consumer.repo.get("p-1")
    assert first is not None

    async def params_changed(params, uri):
        raise RuntimeError("params went wrong")

    object.__setattr__(first, "params_changed", params_changed)
    await consumer.command_params_changed({"q": "x"}, "?q=x")

    assert ("error", {"id": "p-1", "during": "event"}) in outbound.commands
    assert consumer.repo.get("p-2") is not None
