"""A template changed under the dev server: the open pages join their components again (#180).

Django's autoreloader answers a template change by emptying the loaders, not by
restarting, so an open page used to learn nothing of it -- and a component whose
join failed on a broken template stayed refused on its connection after the file
was fixed, until the page was reloaded. In development the same ``file_changed``
now has every open connection of the process send ``rejoin``, and the joins that
answer it render from the new file with the state the page carries.

Neither end may get ahead of a message still being answered: the session writes
``rejoin`` in its turn, behind the handler or join it is handling, and the page
asks ``sync`` before it joins, so what it joins with has every render it was
owed. A join ahead of an event's render used to put back what the event did.

The signal is sent from another thread, as the reloader sends it. The template
is the real loader's, in a directory of the test's own (``testproj.reloadprobe.shadow``).
"""

import asyncio
import json
import typing as t

import pytest
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from django.template.autoreload import reset_loaders
from django.test import override_settings
from django.utils.autoreload import file_changed
from testproj.outbound import RecordingOutbound
from testproj.reloadprobe.live import ReloadBox
from testproj.reloadprobe.shadow import shadowed

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core import template_reload
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, REJOIN_SINCE
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.core.transport import ChannelsBroker, set_broker
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]


@pytest.fixture
def shadow(tmp_path):
    with shadowed(tmp_path) as shadow:
        yield shadow


@pytest.fixture
def debug():
    # pytest-django runs with DEBUG off; the dev server runs with it on
    with override_settings(DEBUG=True):
        yield


def _state(id: str, **fields: t.Any) -> str:
    return sign_state(ReloadBox(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **fields))


async def _connect(vsn: int = PROTOCOL_VERSION) -> WebsocketCommunicator:
    """A connection whose session has started: one join answered.

    The socket is accepted before ``start()`` runs, so the accept alone does
    not say the session registered.
    """
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={vsn}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await _join(communicator, "box")
    await _next(communicator, "render")
    # And what follows the join (joined, the query string), so a test hears only what comes next
    while not await communicator.receive_nothing(timeout=0.1):
        await communicator.receive_json_from()
    return communicator


async def _send(communicator: WebsocketCommunicator, _command: str, **payload: t.Any) -> None:
    await communicator.send_json_to({"command": _command, "payload": payload})


async def _join(communicator: WebsocketCommunicator, id: str, **fields: t.Any) -> None:
    await _send(communicator, "join", name="ReloadBox", state=_state(id, **fields), children={})


async def _bump(communicator: WebsocketCommunicator, id: str, ref: int) -> None:
    await _send(communicator, "user_event", id=id, command="bump", implicit_args={}, explicit_args={}, ref=ref)


async def _next(communicator: WebsocketCommunicator, *commands: str) -> dict[str, t.Any]:
    """The next message whose command is one of ``commands``, skipping bookkeeping."""
    for _ in range(10):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] in commands:
            return message
    raise AssertionError(f"no {commands} message")


async def _rejoined(communicator: WebsocketCommunicator, ref: int) -> None:
    """What the page does on ``rejoin``: ask ``sync``, and join once it is answered."""
    await _next(communicator, "rejoin")
    await _send(communicator, "sync", ref=ref)
    assert await _next(communicator, "synced") == {"command": "synced", "payload": {"ref": ref}}


async def _changed(path) -> list[tuple[t.Any, t.Any]]:
    """Send ``file_changed`` for ``path`` from another thread, as the autoreloader does."""
    return await asyncio.to_thread(file_changed.send, sender=None, file_path=path)


async def test_a_template_change_asks_the_page_to_join_again(debug, shadow):
    communicator = await _connect()
    try:
        results = await _changed(shadow.write("v2"))
        rejoin = await _next(communicator, "rejoin")
    finally:
        await communicator.disconnect()

    assert rejoin == {"command": "rejoin", "payload": {}}
    # Not a "handled": that is Django's receiver's to say, and it said it
    ours = [result for receiver, result in results if receiver is template_reload.templates_changed]
    assert ours == [None]
    assert any(result is True for _, result in results)


async def test_a_component_refused_on_a_broken_template_comes_back_once_it_is_fixed(debug, shadow):
    communicator = await _connect()
    try:
        await _bump(communicator, "box", ref=1)
        bumped = await _next(communicator, "render")

        # Saved broken: the page joins again with the latest state, and fails
        await _changed(shadow.break_it())
        await _rejoined(communicator, ref=10)
        await _join(communicator, "box", count=1)
        failed = await _next(communicator, "error", "render")
        # Refused on this connection: an event runs nothing
        await _bump(communicator, "box", ref=2)
        refused = await _next(communicator, "render", "error")

        # Fixed: the page joins again, and the join is tried
        await _changed(shadow.write("v3"))
        await _rejoined(communicator, ref=11)
        await _join(communicator, "box", count=1)
        back = await _next(communicator, "render", "error")
        await _bump(communicator, "box", ref=3)
        after = await _next(communicator, "render", "error")
    finally:
        await communicator.disconnect()

    assert bumped["payload"]["diff"]["1"] == "1"
    assert failed == {"command": "error", "payload": {"id": "box", "during": "join"}}
    assert refused == {"command": "render", "payload": {"id": "box", "diff": None, "ref": 2}}
    # The new file, with the count the page carried
    assert back["command"] == "render"
    assert '<span data-testid="box-version">v3</span>' in "".join(back["payload"]["diff"]["s"])
    assert back["payload"]["diff"]["d"][1] == "1"
    assert after["command"] == "render"
    assert after["payload"]["diff"]["1"] == "2"


async def test_every_open_connection_of_the_process_hears_it(debug, shadow):
    first, second = await _connect(), await _connect()
    try:
        await _changed(shadow.write("v2"))
        heard = [await _next(first, "rejoin"), await _next(second, "rejoin")]
    finally:
        await first.disconnect()
        await second.disconnect()

    assert [message["command"] for message in heard] == ["rejoin", "rejoin"]


@pytest.mark.parametrize("kind", ["python", "outside"])
async def test_a_file_that_is_not_a_template_asks_nothing(debug, shadow, tmp_path_factory, kind):
    if kind == "python":
        # In a template directory, but Django restarts for it
        path = shadow.root / "reloadprobe" / "helpers.py"
    else:
        path = tmp_path_factory.mktemp("elsewhere") / "box.html"
    path.write_text("")
    communicator = await _connect()
    try:
        await _changed(path)
        assert await communicator.receive_nothing(timeout=0.3)
    finally:
        await communicator.disconnect()


@pytest.mark.parametrize(
    ("debug_on", "setting"),
    [(False, None), (True, False)],
    ids=["DEBUG off", "turned off under DEBUG"],
)
async def test_off_a_connection_does_not_register_and_hears_nothing(shadow, debug_on, setting):
    with override_settings(DEBUG=debug_on, WIREVIEW={"REJOIN_ON_TEMPLATE_CHANGE": setting}):
        before = len(template_reload._sessions)
        communicator = await _connect()
        try:
            registered = len(template_reload._sessions) - before
            await _changed(shadow.write("v2"))
            assert await communicator.receive_nothing(timeout=0.3)
        finally:
            await communicator.disconnect()

    assert registered == 0


async def test_turned_on_without_debug_it_works(shadow):
    with override_settings(DEBUG=False, WIREVIEW={"REJOIN_ON_TEMPLATE_CHANGE": True}):
        communicator = await _connect()
        try:
            await _changed(shadow.write("v2"))
            rejoin = await _next(communicator, "rejoin")
        finally:
            await communicator.disconnect()

    assert rejoin["command"] == "rejoin"


async def test_a_closed_connection_is_let_go(debug, shadow):
    before = len(template_reload._sessions)
    communicator = await _connect()
    assert len(template_reload._sessions) == before + 1
    await communicator.disconnect()

    assert len(template_reload._sessions) == before


async def test_a_page_older_than_rejoin_hears_nothing(debug, shadow):
    communicator = await _connect(vsn=REJOIN_SINCE - 1)
    try:
        await _changed(shadow.write("v2"))
        assert await communicator.receive_nothing(timeout=0.3)
    finally:
        await communicator.disconnect()


async def test_a_restarted_process_joins_what_failed_before_as_any_reconnect(debug, shadow):
    """``uvicorn --reload`` restarts the process: the page reconnects and joins everything again.

    A new connection remembers no failed join, so the fixed template is all it
    takes. A new process starts with empty loaders; ``reset_loaders`` is that here.
    """
    before = await _connect()
    try:
        shadow.break_it()
        reset_loaders()
        await _join(before, "box", count=4)
        failed = await _next(before, "error", "render")
    finally:
        await before.disconnect()

    shadow.write("v5")
    reset_loaders()
    after = await _connect()
    try:
        await _join(after, "box", count=4)
        back = await _next(after, "render", "error")
    finally:
        await after.disconnect()

    assert failed["payload"]["during"] == "join"
    assert back["command"] == "render"
    assert '<span data-testid="box-version">v5</span>' in "".join(back["payload"]["diff"]["s"])
    assert back["payload"]["diff"]["d"][1] == "4"


# What the page has sent and the server has yet to answer: a handler or a join
# halfway when the file changes. Each stops at a gate the test opens.
GATES: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}


async def _gate(name: str) -> None:
    entered, release = GATES[name]
    entered.set()
    await release.wait()


@pytest.fixture
def gates():
    GATES.clear()
    for name in ("bump", "joined", "kid"):
        GATES[name] = (asyncio.Event(), asyncio.Event())
    yield GATES
    for _, release in GATES.values():
        release.set()
    GATES.clear()


class GatedBox(ReloadBox):
    """``ReloadBox`` whose ``bump`` -- and ``joined()``, when asked -- stops halfway at a gate."""

    gate_joined: bool = False

    async def joined(self):
        if self.gate_joined:
            await _gate("joined")

    async def bump(self):
        self.count += 1
        await _gate("bump")


class GatedKid(LiveComponent):
    count: int = 0

    async def bump(self):
        self.count += 1
        await _gate("kid")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<span {% live_tag_header %}>{{ count }}</span>")


class GatedNest(Component):
    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}>{% live_component 'GatedKid' id='kid' %}</div>")


def _signed(cls: type, id: str, **fields: t.Any) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id, **fields))


async def _open(name: str, state: str) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await _send(communicator, "join", name=name, state=state, children={}, ref=1)
    await _next(communicator, "joined")
    return communicator


async def _event(communicator: WebsocketCommunicator, id: str, ref: int) -> None:
    await _send(communicator, "user_event", id=id, command="bump", implicit_args={}, explicit_args={}, ref=ref)


async def test_a_handler_halfway_is_answered_before_the_rejoin(debug, shadow, gates):
    """The review's case: ``rejoin`` ahead of the render put the count back to 0."""
    communicator = await _open("GatedBox", _signed(GatedBox, "box"))
    try:
        await _event(communicator, "box", ref=2)
        await asyncio.wait_for(gates["bump"][0].wait(), 5)
        await _changed(shadow.write("v2"))
        # Its turn has not come: the handler is still being handled
        quiet = await communicator.receive_nothing(timeout=0.3)
        gates["bump"][1].set()
        answered = await _next(communicator, "render", "rejoin")
        await _rejoined(communicator, ref=3)
        # The page joins with the state that render brought
        await _send(communicator, "join", name="GatedBox", state=_signed(GatedBox, "box", count=1), children={}, ref=4)
        back = await _next(communicator, "render", "error")
    finally:
        await communicator.disconnect()

    assert quiet
    assert answered["command"] == "render" and answered["payload"]["ref"] == 2
    # A full render: the loaders were emptied, so the statics are the new file's
    assert answered["payload"]["diff"]["d"][1] == "1"
    assert '<span data-testid="box-version">v2</span>' in "".join(back["payload"]["diff"]["s"])
    assert back["payload"]["diff"]["d"][1] == "1"


async def test_a_sync_is_answered_behind_what_the_page_sent_before_it(debug, shadow, gates):
    """An event the page sent before it heard ``rejoin`` is answered before ``synced``."""
    communicator = await _open("GatedBox", _signed(GatedBox, "box"))
    try:
        await _changed(shadow.write("v2"))
        await _next(communicator, "rejoin")
        # Sent before the page heard it, still being handled when it asks
        await _event(communicator, "box", ref=2)
        await asyncio.wait_for(gates["bump"][0].wait(), 5)
        await _send(communicator, "sync", ref=3)
        quiet = await communicator.receive_nothing(timeout=0.3)
        gates["bump"][1].set()
        first = await _next(communicator, "render", "synced")
        second = await _next(communicator, "render", "synced")
    finally:
        await communicator.disconnect()

    assert quiet
    assert first["command"] == "render" and first["payload"]["ref"] == 2
    assert second == {"command": "synced", "payload": {"ref": 3}}


async def test_a_join_halfway_is_answered_before_the_rejoin(debug, shadow, gates):
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    await communicator.connect()
    try:
        state = _signed(GatedBox, "box", count=3, gate_joined=True)
        await _send(communicator, "join", name="GatedBox", state=state, children={}, ref=1)
        await asyncio.wait_for(gates["joined"][0].wait(), 5)
        await _changed(shadow.write("v2"))
        quiet = await communicator.receive_nothing(timeout=0.3)
        gates["joined"][1].set()
        answered = await _next(communicator, "render", "rejoin")
        await _rejoined(communicator, ref=2)
    finally:
        await communicator.disconnect()

    assert quiet
    # The join's render, with the state the page joins again from. Its
    # ``joined`` is mail queued behind the change's, and carries no state.
    assert answered["command"] == "render" and answered["payload"]["ref"] == 1
    assert answered["payload"]["diff"]["d"][1] == "3"


async def test_a_live_components_handler_halfway_is_answered_before_the_rejoin(debug, shadow, gates):
    communicator = await _open("GatedNest", _signed(GatedNest, "nest"))
    try:
        await _event(communicator, "kid", ref=2)
        await asyncio.wait_for(gates["kid"][0].wait(), 5)
        await _changed(shadow.write("v2"))
        quiet = await communicator.receive_nothing(timeout=0.3)
        gates["kid"][1].set()
        answered = await _next(communicator, "render", "rejoin")
        await _rejoined(communicator, ref=3)
    finally:
        await communicator.disconnect()

    assert quiet
    assert answered["command"] == "render" and answered["payload"]["ref"] == 2
    assert "1" in json.dumps(answered["payload"])


async def test_saves_before_the_rejoin_is_written_make_one(debug, shadow, gates):
    communicator = await _open("GatedBox", _signed(GatedBox, "box"))
    try:
        await _event(communicator, "box", ref=2)
        await asyncio.wait_for(gates["bump"][0].wait(), 5)
        for version in ("v2", "v3", "v4"):
            await _changed(shadow.write(version))
        gates["bump"][1].set()
        await _next(communicator, "render")
        await _next(communicator, "rejoin")
        again = await communicator.receive_nothing(timeout=0.3)
        # Handled: the next save asks again
        await _changed(shadow.write("v5"))
        later = await _next(communicator, "rejoin")
    finally:
        await communicator.disconnect()

    assert again
    assert later["command"] == "rejoin"


async def test_a_session_stopped_after_the_signal_sends_nothing(debug, shadow):
    """The receiver took its snapshot; the session stopped before its loop got to it."""
    outbound = RecordingOutbound()
    session = WireviewSession(outbound)
    await session.start(session=SessionView({}), vsn=PROTOCOL_VERSION)
    assert template_reload.registered(session)

    # On the loop's thread, so nothing of it runs before stop() unregisters
    template_reload.templates_changed(None, file_path=shadow.write("v2"))
    await session.stop()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await session.templates_changed()

    assert not template_reload.registered(session)
    assert outbound.commands == []


async def test_a_bare_session_writes_its_rejoin(debug, shadow):
    outbound = RecordingOutbound()
    session = WireviewSession(outbound)
    await session.start(session=SessionView({}), vsn=PROTOCOL_VERSION)
    try:
        await session.templates_changed()
    finally:
        await session.stop()

    assert outbound.commands == [("rejoin", {})]


async def test_a_sync_whose_ref_is_not_an_integer_is_dropped():
    outbound = RecordingOutbound()
    session = WireviewSession(outbound)
    await session.start(session=SessionView({}), vsn=PROTOCOL_VERSION)
    try:
        await session.handle_message({"command": "sync", "payload": {"ref": "3"}})
        await session.handle_message({"command": "sync", "payload": {"ref": 4}})
    finally:
        await session.stop()

    assert outbound.commands == [("synced", {"ref": 4})]


async def test_a_change_the_full_channel_dropped_does_not_stop_the_next_save(debug):
    """The broker drops a mail to a full channel without raising (#124): the session hears it was not sent.

    It coalesced saves until its mail was handled, and a mail that was never
    sent never is: every later save was ignored until the page reconnected.
    """
    layer = InMemoryChannelLayer(capacity=1)
    set_broker(ChannelsBroker(layer))
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, channel_name="reload-full")
    await session.start(session=SessionView({}), vsn=PROTOCOL_VERSION)
    try:
        await layer.send("reload-full", {"type": "occupied"})
        await session.templates_changed()
        # The channel drains; the next save is mailed
        assert await layer.receive("reload-full") == {"type": "occupied"}
        await session.templates_changed()
        mail = await asyncio.wait_for(layer.receive("reload-full"), 1)
        await session.component_template_changed()
    finally:
        await session.stop()
        set_broker(None)

    assert mail == {"type": "message_from_component", "command": "template_changed", "kwargs": {}}
    assert outbound.commands == [("rejoin", {})]
