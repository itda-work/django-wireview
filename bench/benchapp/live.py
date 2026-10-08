"""Components rendered by the benchmarks. Kept deliberately plain."""

import asyncio

from wireview import AsyncResult, Component, LiveComponent, abroadcast


class BenchFlat(Component):
    """Seven scalar values, no loops."""

    class Meta:
        template_name = "bench/flat.html"

    title: str = "Flat"
    a: int = 1
    b: int = 2
    c: int = 3
    d: int = 4
    e: int = 5
    count: int = 0

    async def increment(self):
        self.count += 1


class BenchList(Component):
    """A list with a conditional per item plus a top-level conditional."""

    class Meta:
        template_name = "bench/list.html"
        subscriptions = {"bench-shout"}

    title: str = "List"
    note: str = ""
    count: int = 0
    shouts: int = 0
    items: list[dict] = []

    async def shout(self):
        """Broadcast to every BenchList in every process; each one re-renders."""
        await abroadcast("bench-shout", n=1)

    async def notification(self, channel: str, **kwargs):
        self.shouts += 1

    async def increment(self):
        self.count += 1

    async def bump(self, index: int = 0):
        self.items[index]["qty"] += 1

    async def append_item(self):
        self.items.append({"name": f"item {len(self.items)}", "qty": 0, "done": False})

    async def toggle(self, index: int = 0):
        self.items[index]["done"] = not self.items[index]["done"]

    async def set_note(self, note: str = "note"):
        self.note = note


class BenchCard(LiveComponent):
    """A nested LiveComponent: a label from the parent and a count of its own."""

    class Meta:
        template_name = "bench/card.html"

    label: str = ""
    count: int = 0

    async def bump(self):
        self.count += 1

    async def reset(self):
        self.count = 0


class BenchBoard(Component):
    """A parent with three BenchCard children. The parent passes each card's count as a prop."""

    class Meta:
        template_name = "bench/board.html"

    title: str = "Board"
    note: str = ""
    cards: list[dict] = [
        {"id": "card-1", "label": "one", "count": 1},
        {"id": "card-2", "label": "two", "count": 2},
        {"id": "card-3", "label": "three", "count": 3},
    ]

    async def set_note(self, note: str = "note"):
        self.note = note


class BenchAsync(Component):
    """Background work through ``start_async`` and ``assign_async``, the path the render gate holds."""

    class Meta:
        template_name = "bench/async.html"

    progress: int = 0
    found: str = ""
    stats: AsyncResult | None = None

    async def _search(self, steps: int) -> str:
        # The progress pattern: every step changes the state the render reads
        for step in range(steps):
            self.progress = step
            await asyncio.sleep(0)
        return "found"

    async def handle_async(self, name, result):
        self.found = str(result.result if isinstance(result, AsyncResult) else result)


class BenchLeaving(Component):
    """leaving() that takes a while, for bench/servers_shutdown.py: does the server let it finish?"""

    class Meta:
        template_name = "bench/flat.html"

    title: str = "Leaving"
    a: int = 1
    b: int = 2
    c: int = 3
    d: int = 4
    e: int = 5
    count: int = 0

    async def increment(self):
        self.count += 1

    async def leaving(self):
        import os
        import time

        log = os.environ["BENCH_LEAVING_LOG"]
        with open(log, "a") as f:
            f.write(f"start {self.id} {time.time()}\n")
        await asyncio.sleep(float(os.environ.get("BENCH_LEAVING_SECONDS", "2")))
        with open(log, "a") as f:
            f.write(f"done {self.id} {time.time()}\n")
