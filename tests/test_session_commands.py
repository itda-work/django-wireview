"""Every command a component sends to its session reaches the browser (#110).

``WireviewMeta.send("title", ...)`` travels through the channel layer and comes
back to the consumer as ``component_title``. ``push_title()``, ``put_flash()``
and ``clear_flash()`` had no such method, so the lookup raised and took the
socket down: the one call a page made to set its title closed its connection.
Their tests read ``MockWireviewMeta.sent_messages`` and never reached the
consumer. Here the list of commands is read from the source, so a new one
cannot be added without its handler.
"""

import ast
import typing as t
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.repository import ComponentRepository

ROOT = Path(__file__).resolve().parent.parent / "wireview"

TEMPLATES = {"sc/page.html": "{% load wireview %}<div {% tag_header %}>{{ this.label }}</div>"}


def _sent_commands() -> set[str]:
    """Command names passed as a literal to ``send``/``_do_send`` (first) or ``send_to`` (second)."""
    found = set()
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            position = {"send": 0, "_do_send": 0, "send_to": 1}.get(node.func.attr)
            if position is None or len(node.args) <= position:
                continue
            arg = node.args[position]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
    return found


SENT = sorted(_sent_commands())


@pytest.mark.unit
def test_the_source_is_read():
    assert {"title", "flash", "clear_flash", "url_change", "stream_op"} <= set(SENT)


@pytest.mark.unit
@pytest.mark.parametrize("command", SENT)
def test_every_sent_command_has_a_consumer_handler(command):
    assert callable(getattr(WireviewConsumer, f"component_{command}", None)), (
        f'WireviewMeta.send("{command}") has no WireviewConsumer.component_{command}'
    )


class ScPage(Component):
    class Meta:
        template_name = "sc/page.html"

    label: str = ""

    async def announce(self):
        await self.push_title("Page 2")
        await self.put_flash("info", "Saved", timeout=0, dismissible=False)
        await self.clear_flash()


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


class LoopbackBroker:
    """Hands a session message straight back to the consumer, as the channel layer would."""

    def __init__(self, consumer: WireviewConsumer) -> None:
        self.consumer = consumer

    async def send_to_session(self, channel: str, message: dict[str, t.Any]) -> None:
        await getattr(self.consumer, message["type"])(message)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_title_and_flash_reach_the_client():
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="test-channel")
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
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
        component = await consumer.repo.join("ScPage", {"id": "p"})
        component.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
        await consumer.send_render(component)
        await component.wire.flush_pending()  # what command_join does after the first render
        await consumer.command_user_event("p", "announce", {}, {})

    sent = [(command, payload) for command, payload in outbound.commands if command != "render"]
    assert sent == [
        ("title", {"title": "Page 2"}),
        ("flash", {"flash_type": "info", "message": "Saved", "timeout": 0, "dismissible": False}),
        ("clear_flash", {"flash_id": None}),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_command_with_no_handler_is_dropped_not_raised():
    consumer = WireviewConsumer()

    await consumer.message_from_component({"type": "message_from_component", "command": "nope", "kwargs": {}})
