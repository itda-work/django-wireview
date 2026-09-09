"""In-process benchmark: render payload sizes and per-event cost.

Runs the bench components through the same path the WebSocket consumer uses
(``WireviewMeta.render_diff`` with a live repository), so payload sizes are
exactly what would go over the wire.
"""

from __future__ import annotations

import json
import time
import tracemalloc
import typing as t

from wireview.testing import mount
from wireview.utils import db


async def _live(component_class, **state):
    view = await mount(component_class, **state)
    view._repo.is_live = True  # live render: signed state marked as dynamic, markers kept
    return view


def _size(payload: t.Any) -> int:
    return 0 if payload is None else len(json.dumps(payload))


async def _diff(view):
    return await view.wire.render_diff(view.component, view._repo)


class _Outbound:
    """Collects what the consumer would send to the browser."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, t.Any]] = []

    async def send_command(self, command: str, payload: t.Any) -> None:
        self.frames.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...

    def take(self) -> tuple[int, int]:
        """(bytes, frames) of the render frames sent since the last take."""
        renders = [payload for command, payload in self.frames if command == "render"]
        self.frames = []
        return sum(_size(p) for p in renders), len(renders)


async def _live_component_scenarios() -> dict[str, int]:
    """Bytes and frames the browser receives for a parent with three LiveComponents.

    Goes through ``WireviewConsumer.send_render`` because that is where the
    children's lifecycle runs. Older trees flushed the children in a separate
    step after the parent's render; calling it when it exists keeps the numbers
    comparable across ``bench-compare``.
    """
    from django.contrib.auth.models import AnonymousUser

    from bench.benchapp.live import BenchBoard  # noqa: F401  (registers the components)
    from wireview.consumer import WireviewConsumer
    from wireview.repository import ComponentRepository

    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "bench"
    outbound = _Outbound()
    consumer.outbound = outbound  # type: ignore[assignment]

    async def render(component) -> None:
        await consumer.send_render(component)
        flush = getattr(consumer, "_flush_pending_live_components", None)
        if flush is not None:
            await flush()

    result: dict[str, int] = {}
    board = await consumer.repo.join("BenchBoard", {"id": "board"})
    await render(board)
    result["live.first_join_bytes"], result["live.first_join_frames"] = outbound.take()

    await board.set_note("hello")
    await render(board)
    result["live.parent_only_change_bytes"], result["live.parent_only_change_frames"] = outbound.take()

    card = consumer.repo.get("card-2")
    await card.reset()
    await render(card)
    outbound.take()
    await board.set_note("again")
    await render(board)
    result["live.child_reset_then_parent_rerender_bytes"], result["live.child_reset_then_parent_rerender_frames"] = (
        outbound.take()
    )
    result["live.child_state_after_parent_rerender"] = consumer.repo.get("card-2").count

    return result


async def run(items: int = 50, iterations: int = 300) -> dict[str, t.Any]:
    from bench.benchapp.live import BenchFlat, BenchList

    payload: dict[str, int] = {}
    timing: dict[str, float] = {}

    # --- flat template ---
    view = await _live(BenchFlat)
    payload["flat.first_render"] = _size(await _diff(view))
    await view.call("increment")
    payload["flat.change_one_value"] = _size(await _diff(view))

    # --- list template ---
    def make_items():
        return [{"name": f"item {i}", "qty": i, "done": i % 3 == 0} for i in range(items)]

    view = await _live(BenchList, items=make_items())
    payload["list.first_render"] = _size(await _diff(view))
    payload["list.no_change"] = _size(await _diff(view))
    await view.call("bump", index=1)
    payload["list.change_one_item"] = _size(await _diff(view))
    await view.call("append_item")
    payload["list.append_item"] = _size(await _diff(view))
    await view.call("toggle", index=2)
    payload["list.toggle_if_inside_item"] = _size(await _diff(view))
    await view.call("set_note", note="hello")
    payload["list.top_level_if_on"] = _size(await _diff(view))
    await view.call("increment")
    payload["list.change_one_value"] = _size(await _diff(view))

    # --- a parent with nested LiveComponents, through the consumer's render path ---
    payload.update(await _live_component_scenarios())

    # --- per-event cost on the list template ---
    view = await _live(BenchList, items=make_items())
    await _diff(view)
    t0 = time.perf_counter()
    for i in range(iterations):
        await view.call("bump", index=i % items)
        await _diff(view)
    timing["list.event_ms"] = (time.perf_counter() - t0) * 1000 / iterations

    wire, comp, repo = view.wire, view.component, view._repo
    context = await wire._get_context_async(comp, repo)
    t0 = time.perf_counter()
    for _ in range(iterations):
        await db(wire._render_with_context)(comp, context)
    timing["list.template_render_ms"] = (time.perf_counter() - t0) * 1000 / iterations

    view = await _live(BenchFlat)
    await _diff(view)
    t0 = time.perf_counter()
    for _ in range(iterations):
        await view.call("increment")
        await _diff(view)
    timing["flat.event_ms"] = (time.perf_counter() - t0) * 1000 / iterations

    # --- memory of one mounted list component with its render snapshot ---
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    views = []
    for _ in range(50):
        v = await _live(BenchList, items=make_items())
        await _diff(v)
        views.append(v)
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    delta = sum(s.size_diff for s in after.compare_to(before, "filename"))
    memory = {"list.component_kb": delta / 50 / 1024}

    return {"payload_bytes": payload, "timing": timing, "memory": memory}
