"""``start_async`` hands its result to ``handle_async`` and renders it, through the consumer (#110).

Nothing asserted that ``handle_async`` was ever called with a result: the only
check was that a cancelled task did not call it. And a ``handle_async`` that
raised skipped the render and vanished inside a task nobody awaited -- not even
logged. It now recovers like a raising handler (#94): the component is joined
again from its last rendered state.
"""

import asyncio
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import AsyncResult, Component
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "sa/page.html": "{% load wireview %}<div {% tag_header %}>{{ this.result }}</div>",
    "sa/stats.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if this.stats.failed %}error: {{ this.stats.error_message }}{% elif this.stats.ok %}"
        "{{ this.stats.result.n }}{% endif %}</div>"
    ),
}

HANDLED: list[tuple[str, str]] = []


class SaPage(Component):
    class Meta:
        template_name = "sa/page.html"

    result: str = ""

    async def search(self, q: str):
        await self.start_async("search", self._find(q))

    async def fail(self):
        await self.start_async("search", self._broken())

    async def slow(self):
        await self.start_async("slow", asyncio.sleep(10))

    async def stop(self):
        await self.cancel_async("slow")

    async def explode(self):
        await self.start_async("explode", self._find("x"))

    async def _find(self, q: str) -> str:
        await asyncio.sleep(0)
        return f"found {q}"

    async def _broken(self) -> str:
        raise LookupError("index offline")

    async def handle_async(self, name, result):
        assert isinstance(result, AsyncResult) and result.done
        HANDLED.append((name, "ok" if result.ok else "failed"))
        if name == "explode":
            raise RuntimeError("handle_async broke")
        self.result = result.result if result.ok else f"failed: {result.error}"


class SaStats(Component):
    class Meta:
        template_name = "sa/stats.html"

    stats: AsyncResult | None = None

    async def load(self, fail: bool = False):
        self.stats = await self.assign_async(self._fetch(fail))

    async def _fetch(self, fail: bool) -> dict:
        await asyncio.sleep(0)
        if fail:
            raise LookupError("stats offline")
        return {"n": 7}


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass

    def last_render(self) -> str:
        return str([payload for command, payload in self.commands if command == "render"][-1])


class LoopbackBroker:
    """Hands a session message straight back to the consumer, as the channel layer would."""

    def __init__(self, consumer: WireviewConsumer) -> None:
        self.consumer = consumer

    async def send_to_session(self, channel: str, message: dict[str, t.Any]) -> None:
        await getattr(self.consumer, message["type"])(message)


@pytest.fixture(autouse=True)
def _templates():
    HANDLED.clear()
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
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="c", vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    component = await consumer.repo.join("SaPage", {"id": "p"})
    component.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(component)
    await component.wire.flush_pending()
    return consumer, outbound, component


async def settle(component: Component, name: str) -> None:
    task = component._async_tasks.get(name)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 2)


async def test_the_result_reaches_handle_async_and_renders():
    consumer, outbound, component = await joined_page()

    await consumer.command_user_event("p", "search", {}, {"q": "tea"})
    await settle(component, "search")

    assert HANDLED == [("search", "ok")]
    assert "found tea" in outbound.last_render()


async def test_a_failed_operation_reaches_handle_async_as_failed():
    consumer, outbound, component = await joined_page()

    await consumer.command_user_event("p", "fail", {}, {})
    await settle(component, "search")

    assert HANDLED == [("search", "failed")]
    assert "failed: index offline" in outbound.last_render()


async def test_cancel_async_stops_the_operation_before_handle_async():
    consumer, _, component = await joined_page()
    await consumer.command_user_event("p", "slow", {}, {})
    task = component._async_tasks["slow"]

    await consumer.command_user_event("p", "stop", {}, {})
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)

    assert task.cancelled() or task.done()
    assert "slow" not in component._async_tasks
    assert HANDLED == []


async def test_a_handle_async_that_raises_joins_the_component_again(caplog):
    consumer, outbound, component = await joined_page()

    await consumer.command_user_event("p", "explode", {}, {})
    await settle(component, "explode")

    assert ("error", {"id": "p", "during": "event"}) in outbound.commands
    assert consumer.repo.get("p") is None
    assert "handle_async broke" in caplog.text


async def test_a_failed_assign_async_renders_its_message_and_survives_a_rejoin():
    # #113: the exception in AsyncResult.error made the state unsignable, so a
    # component whose load failed could not be rendered at all
    consumer, outbound, _ = await joined_page()
    stats = await consumer.repo.join("SaStats", {"id": "s"})
    stats.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(stats)
    await stats.wire.flush_pending()

    await consumer.command_user_event("s", "load", {}, {"fail": True})
    await asyncio.wait_for(asyncio.gather(*list(stats._assign_tasks), return_exceptions=True), 2)

    assert "stats offline" in outbound.last_render()
    again = await consumer.repo.join("SaStats", {"id": "s2", **stats.model_dump(include={"stats"}, mode="json")})
    assert again.stats.failed and again.stats.error_message == "stats offline"


async def test_a_loaded_assign_async_renders_its_result():
    consumer, outbound, _ = await joined_page()
    stats = await consumer.repo.join("SaStats", {"id": "s"})
    stats.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(stats)
    await stats.wire.flush_pending()

    await consumer.command_user_event("s", "load", {}, {})
    await asyncio.wait_for(asyncio.gather(*list(stats._assign_tasks), return_exceptions=True), 2)

    assert "7" in outbound.last_render()
