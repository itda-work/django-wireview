"""A hook's ``pushEvent`` answer comes behind the render its event caused (#223).

The page patches a render on the next frame and runs the callback when the
reply arrives, so a reply sent first ran the callback over the old DOM: a
spinner the callback hid went away before the new number came in. The server
sends the render first and the reply after it -- Phoenix's order -- and the
page holds the reply in its component's line until that render's patch has run
(``tests/test_hooks_e2e.py`` sees the callback read the new DOM).
"""

from __future__ import annotations

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component, LiveComponent
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db, pytest.mark.asyncio]

TEMPLATES = {
    "hook_reply/counter.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ this.count }}</b>"
        "{% live_component 'ReplyLeaf' id='reply-leaf' %}</div>"
    ),
    "hook_reply/leaf.html": "{% load wireview %}<p {% live_tag_header %}>{{ this.count }}</p>",
    "hook_reply/tally.html": (
        "{% load wireview %}<div {% tag_header %}>{% live_component 'ReplyTallyLeaf' id='tally-leaf' n=this.n %}</div>"
    ),
    "hook_reply/tally_leaf.html": "{% load wireview %}<p {% live_tag_header %}>{{ this.n }}</p>",
}

# Not a field: the tally's own render stays the same, only its LiveComponent's changes
TALLY = {"n": 0}


class _Counting:
    count: int = 0

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict) -> t.Any:
        if event == "same":
            return {"count": self.count}
        self.count += 1
        return {"count": self.count}


class ReplyCounter(_Counting, Component):
    class Meta:
        template_name = "hook_reply/counter.html"


class ReplyLeaf(_Counting, LiveComponent):
    class Meta:
        template_name = "hook_reply/leaf.html"


class ReplyTally(Component):
    class Meta:
        template_name = "hook_reply/tally.html"

    @property
    def n(self) -> int:
        return TALLY["n"]

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict) -> t.Any:
        TALLY["n"] += 1
        return {"n": TALLY["n"]}


class ReplyTallyLeaf(LiveComponent):
    class Meta:
        template_name = "hook_reply/tally_leaf.html"

    n: int = 0


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


async def joined(cls: type[Component] = ReplyCounter, id: str = "counter") -> tuple[WireviewSession, RecordingOutbound]:
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="reply-1")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    state = sign_state(cls(id=id, user=AnonymousUser(), wire=WireviewMeta(params={})))
    await session.handle_message({"command": "join", "payload": {"name": cls.__name__, "state": state}})
    outbound.commands.clear()
    return session, outbound


async def hook_event(session: WireviewSession, component_id: str, event: str, ref: str | None) -> None:
    payload = {"component_id": component_id, "hook_id": "h", "event": event, "payload": {}, "ref": ref}
    await session.handle_message({"command": "hook_event", "payload": payload})


async def test_the_reply_follows_the_render_the_event_caused():
    session, outbound = await joined()

    await hook_event(session, "counter", "bump", "hook-1")

    assert [command for command, _ in outbound.commands] == ["render", "hook_reply"]
    render, reply = (payload for _, payload in outbound.commands)
    assert render["id"] == "counter" and "1" in render["diff"].values()
    assert reply == {"ref": "hook-1", "response": {"count": 1}}


async def test_a_live_components_reply_follows_its_render():
    session, outbound = await joined()

    await hook_event(session, "reply-leaf", "bump", "hook-2")

    assert [command for command, _ in outbound.commands] == ["render", "hook_reply"]
    assert outbound.commands[0][1]["id"] == "reply-leaf"
    assert outbound.commands[1][1] == {"ref": "hook-2", "response": {"count": 1}}


async def test_the_reply_follows_a_render_that_changed_only_a_live_component():
    """The page waits for the child's patch here, not the parent's (``tests/js/targets.test.mjs``)."""
    session, outbound = await joined(ReplyTally, "tally")

    await hook_event(session, "tally", "bump", "hook-4")

    assert [command for command, _ in outbound.commands] == ["render", "hook_reply"]
    render = outbound.commands[0][1]
    assert render["id"] == "tally" and render["diff"] is None and "tally-leaf" in render["children"]
    assert outbound.commands[1][1] == {"ref": "hook-4", "response": {"n": TALLY["n"]}}


async def test_an_event_that_changes_nothing_is_answered_alone():
    session, outbound = await joined()

    await hook_event(session, "counter", "same", "hook-3")

    assert outbound.commands == [("hook_reply", {"ref": "hook-3", "response": {"count": 0}})]


async def test_an_event_without_a_callback_gets_no_reply():
    session, outbound = await joined()

    await hook_event(session, "counter", "bump", None)

    assert [command for command, _ in outbound.commands] == ["render"]
