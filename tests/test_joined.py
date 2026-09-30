"""``joined``: the server says a join has landed, everything joined() queued included (#112).

The render that answers a join goes out first; what joined() queued (a stream's
first page, a title) follows through the session's queue. A client that judged
the page on the render alone judged it too early -- infinite scroll saw an
empty list and asked for a second page. ``joined`` comes through the same
queue, after all of it, to a client that says it understands it.
"""

import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component
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


async def _session(vsn: int, last: str) -> list[str]:
    """The commands a client hears for one join, in order: up to ``last``, then until nothing more comes.

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
        state = sign_state(JoinedProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="j-1"))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "JoinedProbe", "state": state, "children": {}}}
        )
        heard: list[dict[str, t.Any]] = []
        while not heard or heard[-1]["command"] != last:
            heard.append(await communicator.receive_json_from(timeout=5))
        while not await communicator.receive_nothing(timeout=0.3):
            heard.append(await communicator.receive_json_from())
        return [m["command"] + (f":{m['payload']['id']}" if m["command"] == "joined" else "") for m in heard]
    finally:
        await communicator.disconnect()


async def test_joined_comes_after_everything_the_join_sent():
    assert await _session(JOINED_SINCE, last="joined") == ["render", "title", "joined:j-1"]


async def test_a_client_that_does_not_know_it_does_not_get_it():
    # No end signal for this client -- that is the point. The title is the last
    # thing joined() queues; the quiet window after it is where joined would be.
    assert await _session(JOINED_SINCE - 1, last="title") == ["render", "title"]
