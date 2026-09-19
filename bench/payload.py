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


def _list_items(count: int, start: int = 0) -> list[dict[str, t.Any]]:
    return [{"name": f"item {i}", "qty": i, "done": i % 3 == 0} for i in range(start, start + count)]


def _swap_middle(items: list) -> None:
    mid = len(items) // 2
    items[mid - 1], items[mid] = items[mid], items[mid - 1]


# Edits that move items relative to their old positions. A positional diff
# resends every item after the edit point; these are what GAP-030 is about.
# Each mutates the component's list directly: the bench runs against older
# trees too, so it cannot rely on handlers that only exist in this one.
_LIST_EDITS: dict[str, t.Callable[[list], None]] = {
    "insert_front": lambda items: items.insert(0, _list_items(1, 9999)[0]),
    "insert_middle": lambda items: items.insert(len(items) // 2, _list_items(1, 9999)[0]),
    "remove_first": lambda items: items.pop(0),
    "move_last_to_first": lambda items: items.insert(0, items.pop()),
    "swap_middle": _swap_middle,
    "reverse": lambda items: items.reverse(),
}


async def _list_edit_scenarios(component_class, count: int, prefix: str = "list") -> dict[str, int]:
    """Bytes of the diff (signed state included) for each edit, from a fresh list of ``count`` items."""
    result: dict[str, int] = {}
    for name, edit in _LIST_EDITS.items():
        view = await _live(component_class, items=_list_items(count))
        await _diff(view)
        edit(view.component.items)
        result[f"{prefix}.{name}"] = _size(await _diff(view))
    return result


async def _list_edit_timing(component_class, count: int, iterations: int) -> dict[str, float]:
    """Per-render ms for the costliest edits on ``count`` items, template render included.

    ``rotate_duplicates`` is the worst case for matching by content: every item
    identical, so trimming the common prefix and suffix removes nothing.
    """
    result: dict[str, float] = {}
    cases: dict[str, tuple[list[dict[str, t.Any]], t.Callable[[list, int], None]]] = {
        "insert_remove_front": (
            _list_items(count),
            lambda items, i: items.insert(0, _list_items(1, 9999)[0]) if i % 2 == 0 else items.pop(0),
        ),
        "rotate": (_list_items(count), lambda items, i: items.insert(0, items.pop())),
        "rotate_duplicates": (
            [{"name": "same", "qty": 0, "done": False}] * (count - 1) + [{"name": "odd", "qty": 1, "done": True}],
            lambda items, i: items.insert(0, items.pop()),
        ),
    }
    for name, (initial, edit) in cases.items():
        view = await _live(component_class, items=[dict(item) for item in initial])
        await _diff(view)
        t0 = time.perf_counter()
        for i in range(iterations):
            edit(view.component.items, i)
            await _diff(view)
        result[f"list{count}.{name}_ms"] = (time.perf_counter() - t0) * 1000 / iterations
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

    # --- list edits that shift item positions (GAP-030) ---
    payload.update(await _list_edit_scenarios(BenchList, items))
    payload.update(await _list_edit_scenarios(BenchList, 500, prefix="list500"))
    timing.update(await _list_edit_timing(BenchList, 500, iterations=100))

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
