"""A session ends once, however its connection ends (#211).

A disconnect ends it. So does the ASGI app's end without one: a server cancels
the app when a handler holds up the disconnect past its close timeout (Daphne)
or on its way down (Uvicorn), and an app may raise. Channels runs
``disconnect()`` in none of those, and what ``stop()`` lets go stayed: the
process's ``PatchHub`` kept the session (the auth topic's entry, strong), the
template reloader, the channel groups, ``start_async`` tasks, a presence
producer's typing timeout and its place on other tabs' lists, a Broadcast
hold's timer. The adapter ends the session in the app's ``finally`` too, and a
second ending waits on the first.

The application's ``leaving()`` hooks are bounded (``LEAVING_SECONDS``) and
apart from that cleanup: a hook that raises, hangs or forgets
``presence_leave()`` does not keep the hub entry, the tasks or the presence.
"""

from __future__ import annotations

import asyncio
import gc
import typing as t
import weakref

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template
from testproj.waiting import eventually

from wireview import Component, PresenceConfig, PresenceMixin, telemetry
from wireview import session as session_module
from wireview.consumer import WireviewConsumer
from wireview.core import template_reload, transport
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.features import presence as presence_module

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

#: What the component's leaving() does, set by each test
LEAVING: dict[str, str] = {"how": "leave"}
#: What happened, in order
CALLS: list[str] = []
#: The handler that keeps the connection's turn, so its disconnect waits
HELD: dict[str, asyncio.Event] = {}
#: The session a "restop" hook stops again from within its ending
ENDING: dict[str, WireviewConsumer] = {}


class SeProducer(PresenceMixin, Component):
    """A presence producer with a typing timeout and a task that never end on their own."""

    class Meta:
        template_name = "se/producer.html"
        subscriptions = {"se-room"}
        presence = PresenceConfig(typing_timeout=3600)

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}></div>")

    def _presence_topic(self) -> str:
        return "se"

    def _presence_user_id(self) -> str:
        return "se-user"

    def _presence_username(self) -> str:
        return "se"

    async def joined(self):
        await self.presence_join()
        await self.presence_set_typing(True)
        await self.start_async("forever", self._forever())

    async def _forever(self):
        await asyncio.Event().wait()

    async def leaving(self):
        CALLS.append("leaving")
        how = LEAVING.get(self.id, LEAVING["how"])
        if how == "raise":
            raise RuntimeError("leaving() went wrong")
        if how == "hang":
            await asyncio.Event().wait()
        if how == "slow":
            await asyncio.sleep(0.1)
            CALLS.append("left")
            await self.presence_leave()
        if how == "restop":
            await ENDING["session"].stop()
            CALLS.append("restopped")
            await self.presence_leave()
        if how == "leave":
            await self.presence_leave()
        # "forget": the application never calls presence_leave()

    async def hold(self, **_rest):
        await HELD["until"].wait()


@pytest.fixture(autouse=True)
def ending(settings, monkeypatch):
    """A layer of this test's own, a reloader that registers, and the presence leaves published."""
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.WIREVIEW = {**getattr(settings, "WIREVIEW", {}), "REJOIN_ON_TEMPLATE_CHANGE": True}
    LEAVING.clear()
    LEAVING["how"] = "leave"
    CALLS.clear()
    ENDING.clear()
    HELD["until"] = asyncio.Event()
    publish = transport.ChannelsBroker.publish

    async def recording(self, topic: str, message: dict[str, t.Any]) -> None:
        if (message.get("kwargs") or {}).get("action") == "presence_leave":
            CALLS.append(f"presence_leave {topic}")
        await publish(self, topic, message)

    monkeypatch.setattr(transport.ChannelsBroker, "publish", recording)
    yield
    HELD["until"].set()


class Connection(t.NamedTuple):
    communicator: WebsocketCommunicator
    consumer: WireviewConsumer
    component: SeProducer
    tasks: list[asyncio.Future[t.Any]]
    hold_timer: asyncio.TimerHandle


async def _connected() -> Connection:
    """Joined, typing, with a task running, the auth topic heard and a Broadcast hold armed."""
    consumer = WireviewConsumer()
    communicator = WebsocketCommunicator(consumer, f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    state = sign_state(SeProducer(user=AnonymousUser(), wire=WireviewMeta(params={}), id="p"))
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": "SeProducer", "state": state, "children": {}}}
    )
    while (await communicator.receive_json_from(timeout=3))["command"] != "joined":
        pass
    await consumer._subscribe_auth_topic()
    component = t.cast(SeProducer, consumer.repo.components["p"])
    typing = component._presence_typing_task
    assert typing is not None
    token = await consumer._hold_for_reset(component)
    assert token is not None
    consumer._arm_expiry(component, token)
    timer = consumer._patch_held[id(component)].expiries[token]
    return Connection(communicator, consumer, component, [component._async_tasks["forever"], typing], timer)


async def _join_another(connection: Connection, id: str) -> SeProducer:
    """A second producer on the same connection, joined and typing."""
    state = sign_state(SeProducer(user=AnonymousUser(), wire=WireviewMeta(params={}), id=id))
    await connection.communicator.send_json_to(
        {"command": "join", "payload": {"name": "SeProducer", "state": state, "children": {}}}
    )
    while (await connection.communicator.receive_json_from(timeout=3))["command"] != "joined":
        pass
    return t.cast(SeProducer, connection.consumer.repo.components[id])


async def _holding(connection: Connection) -> None:
    """A handler keeps the connection's turn: a disconnect would wait behind it."""
    await connection.communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {"id": "p", "command": "hold", "implicit_args": {}, "explicit_args": {}, "ref": 1},
        }
    )
    await eventually(lambda: connection.consumer.handling)


async def _app_ends(connection: Connection) -> None:
    """The app's task ends -- however it ends -- and with it whatever it left running."""
    await asyncio.gather(connection.communicator.future, return_exceptions=True)
    stopping = connection.consumer._stopping
    assert stopping is not None
    await asyncio.gather(stopping, return_exceptions=True)


def _hears_nothing(consumer: WireviewConsumer) -> bool:
    hub = transport.get_patch_hub()
    return not any(consumer in receivers for receivers in hub._receivers.values())


def _ended(connection: Connection) -> None:
    consumer = connection.consumer
    assert _hears_nothing(consumer)
    assert not template_reload.registered(consumer)
    assert all(task.done() for task in connection.tasks)
    assert connection.hold_timer.cancelled()
    assert consumer._mailbox is not None and consumer._mailbox.closed
    assert consumer.subscriptions == set() and consumer._auth_topic == ""


async def test_a_session_whose_app_the_server_cancelled_ends_all_the_same():
    """Daphne cancels the app a held-up disconnect never reached; nothing of the session stays behind."""
    connection = await _connected()
    await _holding(connection)

    connection.communicator.future.cancel()
    await _app_ends(connection)

    _ended(connection)
    assert CALLS == ["leaving", "presence_leave presence.se"]


async def test_a_session_whose_app_raised_ends_all_the_same(monkeypatch):
    """An exception out of the app ends it without a disconnect too."""
    connection = await _connected()

    async def broken(content, **kwargs):
        raise RuntimeError("the adapter went wrong")

    monkeypatch.setattr(connection.consumer, "receive_json", broken)
    await connection.communicator.send_json_to({"command": "sync", "payload": {"ref": 1}})
    await _app_ends(connection)

    assert isinstance(connection.communicator.future.exception(), RuntimeError)
    _ended(connection)
    assert CALLS == ["leaving", "presence_leave presence.se"]


async def test_a_session_whose_app_was_cancelled_is_collected():
    """The hub's entry for the auth topic held the whole session -- its repository, its login -- for good."""
    connection = await _connected()
    ref = weakref.ref(connection.consumer)
    connection.communicator.future.cancel()
    await _app_ends(connection)
    del connection
    await asyncio.sleep(0)
    gc.collect()

    assert ref() is None


async def test_a_disconnect_ends_the_session_once():
    """The app's end after a disconnect waits on that ending: no second leaving(), leave or report."""
    closed: list[dict[str, t.Any]] = []

    def receive(signal, **kwargs):
        closed.append(kwargs)

    telemetry.connection_closed.connect(receive, weak=False)
    telemetry.enable()
    try:
        connection = await _connected()
        await connection.communicator.disconnect()
        await _app_ends(connection)
    finally:
        telemetry.disable()
        telemetry.connection_closed.disconnect(receive)

    _ended(connection)
    assert CALLS == ["leaving", "presence_leave presence.se"]
    assert [each["code"] for each in closed] == [1000]


@pytest.mark.parametrize("how", ["raise", "hang", "forget"])
async def test_a_leaving_hook_that_fails_holds_up_nothing_else(how, monkeypatch):
    """Raised, out of time or without presence_leave(): the framework leaves presence and lets the rest go."""
    monkeypatch.setattr(session_module, "LEAVING_SECONDS", 0.2)
    LEAVING["how"] = how
    connection = await _connected()

    await connection.communicator.disconnect()
    await _app_ends(connection)

    _ended(connection)
    assert CALLS == ["leaving", "presence_leave presence.se"]


async def test_the_hooks_after_the_time_ran_out_are_not_called_and_their_producers_leave(monkeypatch):
    """One hook used the connection's time up: the next is not called, and each producer still leaves."""
    monkeypatch.setattr(session_module, "LEAVING_SECONDS", 0.2)
    LEAVING.clear()
    LEAVING.update(how="leave", p="hang")
    connection = await _connected()
    typing = (await _join_another(connection, "q"))._presence_typing_task
    assert typing is not None

    await connection.communicator.disconnect()
    await _app_ends(connection)

    _ended(connection)
    assert typing.done()
    assert CALLS == ["leaving", "presence_leave presence.se", "presence_leave presence.se"]


async def test_a_leave_the_time_ran_out_in_while_it_published_is_published_after_all(monkeypatch):
    """The hooks' time ends inside presence_leave()'s publish: that leave is still owed, and each producer's arrives."""
    monkeypatch.setattr(session_module, "LEAVING_SECONDS", 0.05)
    connection = await _connected()
    await _join_another(connection, "q")
    publish = transport.ChannelsBroker.publish

    async def slow(self, topic: str, message: dict[str, t.Any]) -> None:
        kwargs = message.get("kwargs") or {}
        if kwargs.get("action") == "presence_leave":
            # Longer than the hooks' time: p's own leave is cut short in here
            await asyncio.sleep(0.1)
            CALLS.append(f"delivered {kwargs['presence_ref']}")
        await publish(self, topic, message)

    monkeypatch.setattr(transport.ChannelsBroker, "publish", slow)

    await connection.communicator.disconnect()
    await _app_ends(connection)

    _ended(connection)
    producers = list(connection.consumer.repo.components.values())
    refs = {presence_module._presence_ref(producer) for producer in producers}
    assert len(refs) == 2
    assert {call.removeprefix("delivered ") for call in CALLS if call.startswith("delivered ")} == refs
    assert CALLS.count("leaving") == 1  # q's hook had no time left
    assert not any(producer._presence_joined or producer._presence_leave_owed for producer in producers)


async def test_a_leaving_hook_that_stops_its_session_again_does_not_wait_on_itself(monkeypatch):
    """stop() from within the ending returns at once: the hook goes on, and the next one runs in time."""
    monkeypatch.setattr(session_module, "LEAVING_SECONDS", 2.0)
    LEAVING.clear()
    LEAVING.update(how="leave", p="restop")
    connection = await _connected()
    await _join_another(connection, "q")
    ENDING["session"] = connection.consumer
    loop = asyncio.get_running_loop()
    began = loop.time()

    await connection.communicator.disconnect()
    await _app_ends(connection)

    _ended(connection)
    assert loop.time() - began < 1.0
    assert CALLS == ["leaving", "restopped", "presence_leave presence.se", "leaving", "presence_leave presence.se"]


async def test_a_session_whose_app_was_cancelled_while_it_ended_ends_all_the_same(monkeypatch):
    """The disconnect's ending is still in leaving() when the server gives up on the app: it goes on, bounded."""
    monkeypatch.setattr(session_module, "LEAVING_SECONDS", 0.2)
    LEAVING["how"] = "hang"
    connection = await _connected()

    await connection.communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await eventually(lambda: CALLS == ["leaving"])
    connection.communicator.future.cancel()
    await _app_ends(connection)

    _ended(connection)
    assert CALLS == ["leaving", "presence_leave presence.se"]


async def test_the_servers_cancellation_does_not_cut_a_leaving_hook_short():
    """The server gives up on the app, not on the application's hook: it runs to its end, within the bound."""
    LEAVING["how"] = "slow"
    connection = await _connected()

    await connection.communicator.send_input({"type": "websocket.disconnect", "code": 1000})
    await eventually(lambda: CALLS == ["leaving"])
    connection.communicator.future.cancel()
    await _app_ends(connection)

    _ended(connection)
    assert CALLS == ["leaving", "left", "presence_leave presence.se"]


async def test_a_connection_refused_before_it_started_ends_with_nothing_to_let_go(settings):
    """A page on another site gets no socket (#96): its ending finds no session to stop."""
    settings.ALLOWED_HOSTS = ["wireview.test"]
    consumer = WireviewConsumer()
    communicator = WebsocketCommunicator(consumer, "/__wireview__", headers=[(b"origin", b"https://elsewhere.example")])
    connected, _ = await communicator.connect()
    assert not connected
    await communicator.disconnect()
    await asyncio.gather(communicator.future, return_exceptions=True)

    assert consumer._stopping is not None and consumer._stopping.done() and consumer._stopping.exception() is None
    assert not hasattr(consumer, "repo") and _hears_nothing(consumer)


async def test_a_session_whose_start_failed_closes_its_mailbox(monkeypatch):
    """Reading the Django session raised in connect(): the mailbox it opened is closed, nothing else was begun."""
    consumer = WireviewConsumer()

    async def broken(session):
        raise RuntimeError("the session store is down")

    monkeypatch.setattr("wireview.consumer.load_session", broken)
    communicator = WebsocketCommunicator(consumer, f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    # Accepted before the session started: the app raises after the accept
    await communicator.connect()
    await asyncio.gather(communicator.future, return_exceptions=True)

    assert isinstance(communicator.future.exception(), RuntimeError)

    assert consumer._mailbox is not None and consumer._mailbox.closed
    assert not template_reload.registered(consumer) and _hears_nothing(consumer)
