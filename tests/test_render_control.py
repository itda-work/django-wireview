"""skip_render, force_render and temporary_assigns through the consumer (#110).

``skip_render`` was checked on the internal ``wire`` and ``force_render`` not at
all; ``temporary_assigns`` only by calling the method that clears them. Here a
real ``command_user_event`` renders, the way a browser's event does.
"""

import json
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "rc/page.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ this.count }}</b>"
        "<ul>{% for m in this.messages %}<li>{{ m }}</li>{% endfor %}</ul></div>"
    )
}


class RcPage(Component):
    class Meta:
        template_name = "rc/page.html"
        temporary_assigns = {"messages"}

    count: int = 0
    messages: list[str] = []

    async def quietly(self):
        self.count += 1
        self.skip_render()

    async def loudly(self):
        self.count += 1

    async def again(self):
        self.force_render()

    async def load(self):
        self.messages = ["one", "two"]


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass

    def last_diff(self) -> t.Any:
        return [payload for command, payload in self.commands if command == "render"][-1]["diff"]


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


async def joined_page() -> tuple[WireviewConsumer, FakeOutbound, Component]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    component = await consumer.repo.join("RcPage", {"id": "p"})
    await consumer.send_render(component)
    return consumer, outbound, component


async def test_skip_render_answers_without_a_diff_and_the_next_render_catches_up():
    consumer, outbound, _ = await joined_page()

    await consumer.command_user_event("p", "quietly", {}, {})
    assert outbound.last_diff() is None, "the event is answered, with nothing to patch"

    await consumer.command_user_event("p", "loudly", {}, {})
    assert '"2"' in json.dumps(outbound.last_diff()), "both increments arrive with the next render"


async def test_force_render_sends_a_full_render_with_nothing_changed():
    consumer, outbound, _ = await joined_page()

    await consumer.command_user_event("p", "again", {}, {})

    assert "s" in outbound.last_diff(), "a full render carries the statics again"


async def test_temporary_assigns_are_cleared_after_a_live_render():
    consumer, outbound, component = await joined_page()

    await consumer.command_user_event("p", "load", {}, {})

    assert "one" in json.dumps(outbound.last_diff())
    assert component.messages == []


async def test_the_next_render_leaves_a_cleared_temporary_assign_on_the_page():
    # #111: the reset is not a change, as in Phoenix. The render for the other
    # field sends that field and nothing of the list.
    consumer, outbound, _ = await joined_page()
    await consumer.command_user_event("p", "load", {}, {})

    await consumer.command_user_event("p", "loudly", {}, {})

    diff = json.dumps(outbound.last_diff())
    assert '"1"' in diff
    assert '"s": []' not in diff and '"d": []' not in diff, diff
