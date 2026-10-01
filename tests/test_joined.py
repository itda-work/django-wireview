"""``joined``: the server says a join has landed, everything joined() queued included (#112).

The render that answers a join goes out first; what joined() queued (a stream's
first page, a title) follows through the session's queue. A client that judged
the page on the render alone judged it too early -- infinite scroll saw an
empty list and asked for a second page. ``joined`` comes through the same
queue, after all of it, to a client that says it understands it.

A LiveComponent hears its own: a render that brings one in runs its joined(),
and the page has to know when that one's list is there too.
"""

import typing as t

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component, LiveComponent
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
