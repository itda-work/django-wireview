"""``wire.submit_form()``: the server has the page submit a form as the browser does (#246, GAP-050).

The request rides on the component's next render -- the one the handler ends
with -- as ``submit``, so the page submits once it has painted that render, and
keeps the form locked from the answer on. A page whose script predates
``SUBMIT_SINCE`` would drop the field and stay put: it is not sent one, and the
server says why in its log.

What the page does with it -- the submitter, the origin, once, the lock -- is
held by tests/test_submit_form_e2e.py in a browser.
"""

from __future__ import annotations

import logging
import re
import typing as t
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import PUBLIC_MEMBERS, WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, SUBMIT_SINCE
from wireview.repository import ComponentRepository

STATIC = Path(__file__).resolve().parent.parent / "wireview" / "static" / "wireview"

TEMPLATES = {
    "sf/page.html": (
        "{% load wireview %}<form {% tag_header %} id='{{ this.id }}'>"
        "<input name='who' value='{{ this.who }}'>{% live_component 'SfChild' id='sf-child' %}</form>"
    ),
    "sf/child.html": "{% load wireview %}<div {% live_tag_header %}><form id='inner'></form></div>",
}


class SfPage(Component):
    who: str = ""

    class Meta:
        template_name = "sf/page.html"

    async def login(self, **data):
        self.who = "ada"
        await self.wire.submit_form("form")

    async def quietly(self, **data):
        # Nothing to draw: the answer still carries the request
        await self.wire.submit_form("#p")

    async def twice(self, **data):
        await self.wire.submit_form("form")
        await self.wire.submit_form("#other")

    async def nothing(self, **data):
        pass


class SfChild(LiveComponent):
    class Meta:
        template_name = "sf/child.html"

    async def go(self, **data):
        await self.wire.submit_form("#inner")


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


async def _session(vsn: int = PROTOCOL_VERSION, *events: dict[str, t.Any]) -> list[tuple[str, dict[str, t.Any]]]:
    """Join an SfPage on a client of protocol ``vsn`` and send ``events``: the frames after the join."""
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="sf-channel", vsn=vsn)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "sf-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        component = await consumer.repo.join("SfPage", {"id": "p"})
        await consumer.send_render(component)
        outbound.commands.clear()
        for event in events:
            payload = {"implicit_args": {}, "explicit_args": {}, **event}
            await consumer.receive_json({"command": "user_event", "payload": payload})
    return outbound.commands


def _renders(frames: list[tuple[str, dict[str, t.Any]]]) -> list[dict[str, t.Any]]:
    return [payload for command, payload in frames if command == "render"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_request_goes_with_the_render_that_answers_the_handler():
    frames = await _session(PROTOCOL_VERSION, {"id": "p", "command": "login", "ref": 7})
    (render,) = _renders(frames)
    # The same frame: the page paints the render, then submits what it drew
    assert render["ref"] == 7
    assert render["submit"] == [{"id": "p", "selector": "form"}]
    assert "ada" in str(render["diff"])
    # No message of its own
    assert [command for command, _ in frames] == ["render"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_handler_that_draws_nothing_still_sends_it():
    (render,) = _renders(await _session(PROTOCOL_VERSION, {"id": "p", "command": "quietly", "ref": 1}))
    assert render["diff"] is None
    assert render["submit"] == [{"id": "p", "selector": "#p"}]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_each_request_goes_once():
    frames = await _session(
        PROTOCOL_VERSION,
        {"id": "p", "command": "twice", "ref": 1},
        {"id": "p", "command": "nothing", "ref": 2},
    )
    first, second = _renders(frames)
    assert first["submit"] == [{"id": "p", "selector": "form"}, {"id": "p", "selector": "#other"}]
    assert "submit" not in second


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_live_components_request_names_it():
    (render,) = _renders(await _session(PROTOCOL_VERSION, {"id": "sf-child", "command": "go", "ref": 3}))
    assert render["id"] == "sf-child"
    assert render["submit"] == [{"id": "sf-child", "selector": "#inner"}]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_an_older_page_is_not_asked_and_the_log_says_why(caplog):
    with caplog.at_level(logging.WARNING, logger="wireview"):
        frames = await _session(SUBMIT_SINCE - 1, {"id": "p", "command": "login", "ref": 4})
    (render,) = _renders(frames)
    assert "submit" not in render
    assert any(
        "Not asking the page to submit 'form' of p" in record.getMessage() and "reload" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_selector_is_required():
    wire = WireviewMeta(params={})
    with pytest.raises(ValueError, match="CSS selector"):
        await wire.submit_form("  ")
    with pytest.raises(ValueError):
        await wire.submit_form(None)  # type: ignore[arg-type]
    assert wire.take_submits() == []


@pytest.mark.unit
def test_it_is_public_on_wire():
    assert "submit_form" in PUBLIC_MEMBERS


@pytest.mark.unit
def test_the_client_reads_the_field():
    source = (STATIC / "rendered.mjs").read_text(encoding="utf-8")
    match = re.search(r"export const PROTOCOL_VERSION = (\d+);", source)
    assert match and int(match.group(1)) == PROTOCOL_VERSION >= SUBMIT_SINCE
    client = (STATIC / "wireview.js").read_text(encoding="utf-8")
    assert "const { id, diff, children, ref, vsn, instances, submit } = payload;" in client
