"""The session runs with nothing but an ``Outbound`` (GAP-027, #60).

``WireviewConsumer`` used to be both the Channels WebSocket and everything a
session does. The session logic is ``WireviewSession`` now and the consumer is
its adapter, so these drive a session with no socket, no scope and no channel
layer: join, an event, a render, and the way out.
"""

import ast
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TEMPLATES = {"sess/count.html": "{% load wireview %}<p {% tag_header %}>count={{ this.count }}</p>"}


class SessCounter(Component):
    class Meta:
        template_name = "sess/count.html"

    count: int = 0

    async def bump(self, by: int = 1):
        self.count += by

    async def leaving(self):
        LEFT.append(self.id)


LEFT: list[str] = []


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


async def started() -> tuple[WireviewSession, RecordingOutbound]:
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="sess-1")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    return session, outbound


def signed(count: int = 0) -> str:
    return sign_state(SessCounter(id="c", count=count, user=AnonymousUser(), wire=WireviewMeta(params={})))


@pytest.mark.asyncio
async def test_a_session_joins_handles_an_event_and_renders_with_only_an_outbound():
    session, outbound = await started()

    await session.handle_message({"command": "join", "payload": {"name": "SessCounter", "state": signed(2)}})
    (joined,) = outbound.renders()
    assert joined["id"] == "c" and joined["vsn"] == PROTOCOL_VERSION
    assert "2" in joined["diff"]["d"], "the full render carries the restored count"

    await session.handle_message(
        {
            "command": "user_event",
            "payload": {"id": "c", "command": "bump", "implicit_args": {}, "explicit_args": {"by": 3}},
        }
    )
    assert session.repo.get("c").count == 5
    assert "5" in outbound.renders()[-1]["diff"].values(), "a partial diff with the new count"


@pytest.mark.asyncio
async def test_a_message_no_client_sends_is_dropped_not_raised():
    session, outbound = await started()

    await session.handle_message({"command": "no_such_thing", "payload": {}})
    await session.handle_message("not even a dict")

    assert outbound.commands == []


@pytest.mark.asyncio
async def test_stop_lets_every_component_leave_and_every_topic_go():
    session, outbound = await started()
    await session.handle_message({"command": "join", "payload": {"name": "SessCounter", "state": signed()}})
    outbound.topics.add("some.topic")
    session.subscriptions.add("some.topic")

    LEFT.clear()
    await session.stop()

    assert LEFT == ["c"]
    assert outbound.topics == set()


@pytest.mark.asyncio
async def test_the_session_closes_through_its_outbound():
    session, outbound = await started()

    await session.session_invalidated({"reason": "logged out"})

    assert outbound.closed == [4001]


def test_the_session_module_knows_nothing_of_channels():
    """The seam is the point: a second adapter must not need Channels to host a session."""
    tree = ast.parse((Path(__file__).resolve().parent.parent / "wireview" / "session.py").read_text())
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not {name for name in imported if name.split(".")[0] == "channels"}


def test_the_consumer_is_only_the_adapter():
    """Every handler lives on the session; the consumer adds the socket's own four and no more.

    And ``dispatch``: where one message ends is the adapter's to know, and it
    only tells the session (``handling_message``, #178).
    """
    own = {name for name, value in vars(WireviewConsumer).items() if callable(value) and not name.startswith("__")}
    assert own <= {"websocket_connect", "connect", "disconnect", "receive_json", "dispatch"}
    assert issubclass(WireviewConsumer, WireviewSession)
