"""A handler's async ORM never runs on the connection the previous message left open (#176).

Channels closes old database connections on a worker trip before every handler
(``AsyncConsumer.dispatch`` -> ``aclose_old_connections``). The fan-out profile
counts that trip at about 18 µs a message, and the render's own trip closes them
again on either side of the render, so skipping it looked free (B6 in
docs/design/broadcast-fanout.md). It is not: what runs after the render's trip
-- an ``after_render`` hook, a LiveComponent's ``joined()`` -- may open a
connection, and the next message's handler runs before any render. Without the
dispatch trip that handler queries on a connection opened by the message before,
past ``CONN_MAX_AGE`` and unchecked: the one a database or a pooler may have
dropped in between.

Each connection is numbered as it is created (``connection_created``): the
handler's is the hook's only when nothing closed it in between. No test that
talks to the consumer through Channels' ``WebsocketCommunicator`` can see this:
it swaps ``close_old_connections`` for a no-op while it does.
"""

import itertools
import json
import typing as t

import channels.consumer
import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.db import connection as db_connection
from django.db.backends.signals import connection_created
from django.template import Template

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

#: ``(where, the number of the connection it queried on)``
SEEN: list[tuple[str, int]] = []
CREATED = itertools.count(1)


def _number(sender: t.Any, connection: t.Any, **_kwargs: t.Any) -> None:
    connection._dc_number = next(CREATED)


async def _query(where: str) -> None:
    await User.objects.acount()
    SEEN.append((where, await sync_to_async(lambda: db_connection._dc_number)()))


class DcProbe(Component):
    async def joined(self):
        async def after_render() -> None:
            await _query("after_render")  # a connection opened after the render's trip closed its own

        self.attach_hook("dc-open", "after_render", after_render)

    async def look(self, **_rest):
        await _query("handler")

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}></p>")


@pytest.fixture(autouse=True)
def _count_connections():
    SEEN.clear()
    connection_created.connect(_number)
    yield
    connection_created.disconnect(_number)


async def _look_after_a_render() -> tuple[int, int]:
    """Join, then send an event; the numbers of the connections the hook and then the handler queried on.

    Through asgiref's communicator: Channels' own swaps ``close_old_connections``
    for a no-op while it talks to the consumer, the very call this is about.
    """
    scope = {
        "type": "websocket",
        "path": "/__wireview__",
        "query_string": f"vsn={PROTOCOL_VERSION}".encode(),
        "headers": [],
        "subprotocols": [],
        "user": AnonymousUser(),
    }
    communicator = ApplicationCommunicator(WireviewConsumer.as_asgi(), scope)

    async def send(_command: str, **payload: t.Any) -> None:
        text = json.dumps({"command": _command, "payload": payload})
        await communicator.send_input({"type": "websocket.receive", "text": text})

    async def until(command: str) -> None:
        while json.loads((await communicator.receive_output(5))["text"])["command"] != command:
            pass

    await communicator.send_input({"type": "websocket.connect"})
    assert (await communicator.receive_output(5))["type"] == "websocket.accept"
    try:
        state = sign_state(DcProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="dc"))
        await send("join", name="DcProbe", state=state, children={})
        await until("joined")
        await send("user_event", id="dc", command="look", implicit_args={}, explicit_args={})
        await until("render")
    finally:
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait(5)
    (hook, opened), (handler, used) = SEEN[:2]
    assert (hook, handler) == ("after_render", "handler")
    return opened, used


async def test_a_handler_queries_on_a_connection_of_its_own():
    opened, used = await _look_after_a_render()
    assert used != opened


async def test_without_channels_dispatch_trip_it_would_not(monkeypatch):
    """What B6 would do: the handler reuses the connection the hook opened during the join."""

    async def skipped() -> None:
        pass

    monkeypatch.setattr(channels.consumer, "aclose_old_connections", skipped)

    opened, used = await _look_after_a_render()
    assert used == opened
