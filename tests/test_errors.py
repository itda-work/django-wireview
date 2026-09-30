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


class ErrorProbe(Component):
    count: int = 0
    fail_to_join: bool = False
    fail_on_params: bool = False

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
        # Nothing was left behind for an event to reach: it is not answered,
        # and the next thing on the socket is another component's join.
        await _event(communicator, "e-1", "bump", ref=2)
        await _join(communicator, "e-2")
        after = await _next(communicator, "render", "error")
        calls = list(CALLS)
    finally:
        await communicator.disconnect()

    assert error == {"command": "error", "payload": {"id": "e-1", "during": "join"}}
    assert calls == [("leaving", "e-1")]
    assert after["payload"]["id"] == "e-2"


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
    consumer.repo.build("ErrorProbeParent", {"id": "root"})
    child = consumer.repo.build("ErrorProbeChild", {"id": "child"})
    child._parent_id = "root"  # type: ignore[union-attr]

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
