"""A user event and the render that answers it are paired by ``ref`` (#92).

The client needs to know which render answers which of its events: only the
answer to a committing action (a submit, Enter) may reset the fields it came
from, and #91's first version let whatever render arrived first take that
right. ``ref`` travels client → server, so the server first says it can take
one: the render answering a join carries its protocol version.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, REFS_SINCE
from wireview.core.state import sign_state

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

_template: Template | None = None


class RefProbe(Component):
    _template_name = "ref_probe.html"

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1

    async def nothing(self, **_rest):
        pass

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template("{% load wireview %}<p {% tag_header %}>{{ count }}</p>")
        return _template


async def _joined() -> tuple[WebsocketCommunicator, dict]:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    probe = RefProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="ref-1")
    await communicator.send_json_to(
        {"command": "join", "payload": {"name": "RefProbe", "state": sign_state(probe), "children": {}}}
    )
    first = await communicator.receive_json_from(timeout=5)
    assert first["command"] == "render"
    return communicator, first["payload"]


async def _event(communicator, command: str, **extra) -> dict:
    await communicator.send_json_to(
        {
            "command": "user_event",
            "payload": {"id": "ref-1", "command": command, "implicit_args": {}, "explicit_args": {}, **extra},
        }
    )
    for _ in range(5):
        message = await communicator.receive_json_from(timeout=5)
        if message["command"] == "render":
            return message["payload"]
    raise AssertionError("no render after the event")


async def test_the_render_answering_a_join_announces_the_servers_version():
    communicator, first = await _joined()
    await communicator.disconnect()

    assert first["vsn"] == PROTOCOL_VERSION >= REFS_SINCE


@pytest.mark.parametrize("command", ["bump", "nothing"])
async def test_the_answer_carries_the_events_ref_whether_or_not_it_changed_anything(command):
    communicator, _ = await _joined()
    try:
        answer = await _event(communicator, command, ref=7)
    finally:
        await communicator.disconnect()

    assert answer["ref"] == 7
    assert (answer["diff"] is None) == (command == "nothing")


@pytest.mark.parametrize("ref", ["7", True, 1.5, None])
async def test_only_an_integer_ref_is_echoed(ref):
    communicator, _ = await _joined()
    try:
        answer = await _event(communicator, "bump", ref=ref)
    finally:
        await communicator.disconnect()

    assert "ref" not in answer


async def test_an_event_without_a_ref_is_answered_as_before():
    communicator, _ = await _joined()
    try:
        answer = await _event(communicator, "bump")
    finally:
        await communicator.disconnect()

    assert "ref" not in answer and "vsn" not in answer
