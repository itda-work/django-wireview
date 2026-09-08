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
