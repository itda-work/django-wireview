"""What a user_event says beyond its fields, and who gets it (#244).

A client that heard ``EVENT_META_SINCE`` from the server sends ``meta`` with a
user_event: the field the event came from, the fields of its form nobody has
used, and the ones whose input the browser could not read. A handler gets each
only by naming its parameter -- ``_target``, ``_unused``, ``_bad_input`` -- and
never through ``**kwargs``: a handler that passes the form on as
``Model(**data)`` must not find keys no field of the form has.

The browser and the DOM helpers are held to the same answers by the ``meta-*``
scenarios of ``testproj.domprobe.scenarios``.
"""

from __future__ import annotations

import re
import typing as t
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from pydantic import validate_call

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.handlers import event_meta, named_event_meta
from wireview.core.rendered import EVENT_META_SINCE, PROTOCOL_VERSION
from wireview.repository import ComponentRepository

STATIC = Path(__file__).resolve().parent.parent / "wireview" / "static" / "wireview"

TEMPLATES = {"em/page.html": "{% load wireview %}<div {% tag_header %}></div>"}

#: What each handler got, in order
GOT: list[tuple[str, dict[str, t.Any]]] = []


class EmPage(Component):
    class Meta:
        template_name = "em/page.html"

    async def named(self, _target: str | None = None, _unused: list[str] = [], _bad_input: list[str] = [], **data):
        GOT.append(("named", {"_target": _target, "_unused": _unused, "_bad_input": _bad_input, **data}))

    async def spread(self, **data):
        GOT.append(("spread", data))

    async def strict(self, title: str = ""):
        GOT.append(("strict", {"title": title}))


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


async def _send(*events: dict[str, t.Any]) -> list[tuple[str, dict[str, t.Any]]]:
    """Join an EmPage and send ``events`` as user_event payloads, through the consumer's own entry."""
    GOT.clear()
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="em-channel")
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "em-channel"
    consumer.outbound = FakeOutbound()  # type: ignore[assignment]
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        component = await consumer.repo.join("EmPage", {"id": "p"})
        await consumer.send_render(component)
        for event in events:
            await consumer.receive_json({"command": "user_event", "payload": {"id": "p", **event}})
    return list(GOT)


FORM = {"title": ["T"], "_target": ["a field called _target"]}
META = {"target": "title", "unused": ["body"], "bad_input": ["age"]}


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_handler_gets_what_it_names():
    got = await _send({"command": "named", "implicit_args": FORM, "explicit_args": {}, "meta": META})
    # The page's word on the event wins over a field of the same name
    assert got == [("named", {"_target": "title", "_unused": ["body"], "_bad_input": ["age"], "title": "T"})]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_kwargs_get_the_form_and_nothing_else():
    got = await _send(
        {"command": "spread", "implicit_args": {"title": ["T"]}, "explicit_args": {}, "meta": META},
        {"command": "strict", "implicit_args": {"title": ["T"]}, "explicit_args": {}, "meta": META},
    )
    assert got == [("spread", {"title": "T"}), ("strict", {"title": "T"})]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_client_that_sends_no_meta_gives_the_defaults():
    # A page on a bundle older than EVENT_META_SINCE: the parameters still get values
    got = await _send({"command": "named", "implicit_args": {"title": ["T"]}, "explicit_args": {}})
    assert got == [("named", {"_target": None, "_unused": [], "_bad_input": [], "title": "T"})]


@pytest.mark.unit
def test_what_does_not_fit_is_dropped():
    assert event_meta({"target": 3, "unused": ["a", 1, None], "bad_input": "age", "extra": True}) == {
        "_target": None,
        "_unused": ["a"],
        "_bad_input": [],
    }
    assert event_meta(["not", "a", "dict"]) == {"_target": None, "_unused": [], "_bad_input": []}


@pytest.mark.unit
def test_only_a_parameter_of_that_name_takes_it():
    meta = event_meta(META)

    async def spread(**data): ...

    async def positional(_target, /): ...

    async def keyword(*, _unused: list[str]): ...

    @validate_call
    async def validated(_bad_input: list[str] = []): ...

    assert named_event_meta(spread, meta) == {}
    assert named_event_meta(positional, meta) == {}
    assert named_event_meta(keyword, meta) == {"_unused": ["body"]}
    assert named_event_meta(validated, meta) == {"_bad_input": ["age"]}
    assert named_event_meta(keyword, None) == {}


@pytest.mark.unit
def test_the_client_sends_meta_only_to_a_server_that_takes_it():
    # An older server's command_user_event has no meta parameter: the session drops the
    # whole event as a payload that does not fit, so the page must not send it there
    source = (STATIC / "rendered.mjs").read_text(encoding="utf-8")
    match = re.search(r"export const EVENT_META_SINCE = (\d+);", source)
    assert match and int(match.group(1)) == EVENT_META_SINCE <= PROTOCOL_VERSION
    client = (STATIC / "wireview.js").read_text(encoding="utf-8")
    assert "connection.serverVsn >= EVENT_META_SINCE ? this.eventMeta(" in client
