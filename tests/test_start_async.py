"""``start_async`` hands its result to ``handle_async`` and renders it, through the consumer (#110).

Nothing asserted that ``handle_async`` was ever called with a result: the only
check was that a cancelled task did not call it. And a ``handle_async`` that
raised skipped the render and vanished inside a task nobody awaited -- not even
logged. It now recovers like a raising handler (#94): the component is joined
again from its last rendered state.
"""

import asyncio
import re
import threading
import time
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.base import Variable
from django.test import override_settings

from wireview import AsyncResult, Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, strip_markers
from wireview.core.state import unsign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "sa/page.html": "{% load wireview %}<div {% tag_header %}>{{ this.result }}</div>",
    "sa/stats.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if this.stats.failed %}error: {{ this.stats.error_message }}{% elif this.stats.ok %}"
        "{{ this.stats.result.n }}{% endif %}</div>"
    ),
    "sa/progress.html": "{% load wireview %}<div {% tag_header %}>{{ this.progress }} {{ this.note }}</div>",
    "sa/feed.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "{% if this.stats.ok %}{{ this.stats.result }}{% elif this.stats.loading %}loading{% endif %}"
        '<ul id="items" wire-stream="items"></ul></div>'
    ),
    "sa/feed_item.html": "<li>{{ item.label }}</li>",
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


class SaProgress(Component):
    class Meta:
        template_name = "sa/progress.html"

    progress: int = 0
    note: str = ""

    async def run(self):
        await self.start_async("run", self._work())

    async def _work(self) -> str:
        # The documented progress pattern: the operation itself changes the state
        for step in range(1, 4):
            await asyncio.sleep(0)
            self.progress = step
            await self.send_render()
        return "done"

    async def handle_async(self, name, result):
        self.note = result.result


class SlowItem:
    """A stream item whose template read holds the worker thread until the test lets it go."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.reading = asyncio.Event()
        self.release = threading.Event()

    @property
    def label(self) -> str:
        self.loop.call_soon_threadsafe(self.reading.set)
        assert self.release.wait(2)
        return "a"


class BrokenItem:
    """A stream item whose template read raises."""

    @property
    def label(self) -> str:
        raise RuntimeError("item template broke")


class SaFeed(Component):
    class Meta:
        template_name = "sa/feed.html"

    stats: AsyncResult | None = None
    _item: t.Any = None
    _go: t.Any = None
    _ticked: bool = False
    _fetched: bool = False

    async def tick(self):
        await self.start_async("tick", self._tick())

    async def _tick(self) -> None:
        await self._go.wait()
        self._ticked = True

    async def feed(self):
        await self.start_async("feed", self._feed())

    async def _feed(self) -> str:
        await self.stream_insert("items", self._item, dom_id=lambda item: "items-a")
        return "fed"

    async def load(self):
        self.stats = await self.assign_async(self._fetch())

    async def _fetch(self) -> str:
        self._fetched = True
        await asyncio.sleep(0)
        return "loaded"


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass

    def mark(self) -> int:
        return len(self.commands)

    def rendered_since(self, mark: int) -> str:
        """Every render sent after ``mark``, as one string.

        Not the last one: which render carries a change depends on when the
        task ran (#135). A task that finished before the event's render lets
        that render show the result, and its own render then changes nothing.
        It can no longer finish in the middle of a render (#138).
        """
        return str([payload for command, payload in self.commands[mark:] if command == "render"])


def shown(component: Component) -> str:
    """The page once the browser has applied every render: the tree the last diff was taken from."""
    assert component.wire._last_rendered is not None
    return component.wire._last_rendered.to_html()


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


@pytest.fixture(params=["prompt", "late"])
def render_thread(request, monkeypatch):
    """A render thread that keeps up with the loop, and one that does not (#135).

    "late" pauses the worker thread at every ``this.`` lookup in a template, after
    the root tag has signed data-state: the loop would run the background task in
    the gap, as a slow CI runner let it. The tests below failed that way on half
    the grid while passing on every laptop, and a frame came out signed with one
    state and drawn from another until the task was held for the render (#138).
    """
    if request.param == "late":
        resolve = Variable._resolve_lookup

        def late(self, context):
            if threading.current_thread() is not threading.main_thread() and self.var.startswith("this."):
                time.sleep(0.02)
            return resolve(self, context)

        monkeypatch.setattr(Variable, "_resolve_lookup", late)
    return request.param


@pytest.fixture
def frames(monkeypatch) -> list[str]:
    """The HTML of every live render, as the server diffed it: one frame each."""
    seen: list[str] = []
    diff = WireviewMeta._compute_rendered_diff

    def record(self, html, *args, **kwargs):
        seen.append(strip_markers(html))
        return diff(self, html, *args, **kwargs)

    monkeypatch.setattr(WireviewMeta, "_compute_rendered_diff", record)
    return seen


def signed_in(frame: str, name: str) -> dict[str, t.Any]:
    signed = re.search(r'data-state="([^"]+)"', frame)
    assert signed is not None
    return unsign_state(signed[1], name)


def body_of(frame: str) -> str:
    return frame.split(">", 1)[1].rsplit("<", 1)[0]


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
    """Wait for the task under ``name`` to end, however it ends.

    A handle_async that raises ends its own task cancelled: the component is
    discarded and its tasks with it. The task may still be running when the
    event returns -- it waits out the event's render (#138).
    """
    task = component._async_tasks.get(name)
    if task is not None:
        done, _ = await asyncio.wait([task], timeout=2)
        assert done, f"{name} did not finish"


async def test_the_result_reaches_handle_async_and_renders(render_thread):
    consumer, outbound, component = await joined_page()
    mark = outbound.mark()

    await consumer.command_user_event("p", "search", {}, {"q": "tea"})
    await settle(component, "search")

    assert HANDLED == [("search", "ok")]
    assert "found tea" in outbound.rendered_since(mark)
    assert "found tea" in shown(component)


async def test_a_failed_operation_reaches_handle_async_as_failed(render_thread):
    consumer, outbound, component = await joined_page()
    mark = outbound.mark()

    await consumer.command_user_event("p", "fail", {}, {})
    await settle(component, "search")

    assert HANDLED == [("search", "failed")]
    assert "failed: index offline" in outbound.rendered_since(mark)
    assert "failed: index offline" in shown(component)


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


async def test_a_failed_assign_async_renders_its_message_and_survives_a_rejoin(render_thread):
    # #113: the exception in AsyncResult.error made the state unsignable, so a
    # component whose load failed could not be rendered at all
    consumer, outbound, _ = await joined_page()
    stats = await consumer.repo.join("SaStats", {"id": "s"})
    stats.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(stats)
    await stats.wire.flush_pending()
    mark = outbound.mark()

    await consumer.command_user_event("s", "load", {}, {"fail": True})
    await asyncio.wait_for(asyncio.gather(*list(stats._assign_tasks), return_exceptions=True), 2)

    assert "stats offline" in outbound.rendered_since(mark)
    assert "error: stats offline" in shown(stats)
    # From the page's data-state, not the instance: what a rejoin starts from.
    # A frame signed before the task finished once showed its result (#138).
    signed = re.search(r'data-state="([^"]+)"', shown(stats))
    assert signed is not None
    state = unsign_state(signed[1], "SaStats")
    again = await consumer.repo.join("SaStats", {**state, "id": "s2"})
    assert again.stats.failed and again.stats.error_message == "stats offline"


async def test_a_loaded_assign_async_renders_its_result(render_thread):
    consumer, outbound, _ = await joined_page()
    stats = await consumer.repo.join("SaStats", {"id": "s"})
    stats.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(stats)
    await stats.wire.flush_pending()
    mark = outbound.mark()

    await consumer.command_user_event("s", "load", {}, {})
    await asyncio.wait_for(asyncio.gather(*list(stats._assign_tasks), return_exceptions=True), 2)

    assert "7" in outbound.rendered_since(mark)
    assert ">7</div>" in shown(stats)


async def test_every_frame_of_an_assign_async_shows_the_state_it_signs(render_thread, frames):
    # #138: the worker thread signed data-state, then read the body while the loop
    # finished the task -- a frame signed "loading" showed the failure
    consumer, _, _ = await joined_page()
    stats = await consumer.repo.join("SaStats", {"id": "s"})
    stats.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(stats)
    await stats.wire.flush_pending()

    await consumer.command_user_event("s", "load", {}, {"fail": True})
    await asyncio.wait_for(asyncio.gather(*list(stats._assign_tasks), return_exceptions=True), 2)

    shown_frames = [frame for frame in frames if 'data-name="SaStats"' in frame]
    assert any("error: stats offline" in frame for frame in shown_frames)
    for frame in shown_frames:
        failed = (signed_in(frame, "SaStats")["stats"] or {}).get("state") == "error"
        assert ("error: stats offline" in body_of(frame)) is failed, frame


async def test_every_frame_of_a_start_async_shows_the_state_it_signs(render_thread, frames):
    # The operation and handle_async both change the component on the loop; no
    # frame may carry a body from one state and a data-state from another
    consumer, _, _ = await joined_page()
    progress = await consumer.repo.join("SaProgress", {"id": "g"})
    progress.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(progress)
    await progress.wire.flush_pending()

    await consumer.command_user_event("g", "run", {}, {})
    await settle(progress, "run")

    shown_frames = [frame for frame in frames if 'data-name="SaProgress"' in frame]
    assert "3 done" in body_of(shown_frames[-1])
    for frame in shown_frames:
        state = signed_in(frame, "SaProgress")
        assert body_of(frame).strip() == f"{state['progress']} {state['note']}".strip(), frame


async def test_a_stream_item_rendered_by_the_work_overlapping_an_event_render_leaves_later_work_running():
    # The operation's stream_insert renders its item on the worker thread and a
    # click's render queues behind it. Neither may let other work of the
    # component run while it is in flight, and once both are done, the operation
    # and the assign_async after it finish (#138)
    consumer, outbound, _ = await joined_page()
    feed = await consumer.repo.join("SaFeed", {"id": "f"})
    feed.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(feed)
    await feed.wire.flush_pending()
    item = feed._item = SlowItem()
    feed._go = asyncio.Event()

    try:
        await consumer.command_user_event("f", "tick", {}, {})
        await consumer.command_user_event("f", "feed", {}, {})
        op = feed._async_tasks["feed"]
        await asyncio.wait_for(item.reading.wait(), 2)
        # Other work wakes up while the item renders: the stream item's render holds it
        feed._go.set()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not feed._ticked, "other work ran while the stream item rendered"
        # A click while the item renders: its handler starts an assign_async, and
        # its render waits for the worker thread
        click = asyncio.create_task(consumer.command_user_event("f", "load", {}, {}))
        for _ in range(20):
            await asyncio.sleep(0)
        assert feed._assign_tasks, "the handler ran"
        assert not feed._fetched, "the assign_async ran while the stream item rendered"
    finally:
        # A failed assertion must not leave the shared worker thread held for
        # the next test: database_sync_to_async runs every test on one thread
        item.release.set()
    await asyncio.wait_for(click, 2)
    tick = feed._async_tasks["tick"]
    done, _ = await asyncio.wait([op, tick, *feed._assign_tasks], timeout=2)
    assert op in done, "the operation never got past its stream render"
    assert tick in done and feed._ticked
    assert len(done) == 3, "the assign_async never ran"
    assert any("items-a" in str(payload) for command, payload in outbound.commands if command == "stream_op")
    assert "loaded" in shown(feed)


async def test_work_after_a_stream_item_render_that_raised_runs():
    # The item's template raises inside the operation's stream_insert: the
    # operation fails, and the render it marked is over. Work the component
    # starts afterwards must not wait for that render for good (#138)
    consumer, outbound, _ = await joined_page()
    feed = await consumer.repo.join("SaFeed", {"id": "f"})
    feed.wire.broker = LoopbackBroker(consumer)  # type: ignore[assignment]
    await consumer.send_render(feed)
    await feed.wire.flush_pending()
    feed._item = BrokenItem()

    await consumer.command_user_event("f", "feed", {}, {})
    await settle(feed, "feed")
    assert not any(command == "stream_op" for command, _ in outbound.commands), "the item rendered"
    await consumer.command_user_event("f", "load", {}, {})
    gate = feed.wire._render_gate
    done, _ = await asyncio.wait(list(feed._assign_tasks), timeout=2)
    if not done:
        # Free it by hand: cancelling a held task waits for a render that is gone
        gate._renders.clear()
        if gate._idle is not None:
            gate._idle.set()
        await asyncio.gather(*feed._assign_tasks, return_exceptions=True)
    assert done, "the assign_async waited for a stream item render that had raised"
    assert gate._renders == {}
    assert "loaded" in shown(feed)
