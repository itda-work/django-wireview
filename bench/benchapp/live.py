"""Components rendered by the benchmarks. Kept deliberately plain."""

from wireview import Component, LiveComponent, abroadcast


class BenchFlat(Component):
    """Seven scalar values, no loops."""

    _template_name = "bench/flat.html"

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

    _template_name = "bench/list.html"
    _subscriptions = {"bench-shout"}

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

    _template_name = "bench/card.html"

    label: str = ""
    count: int = 0

    async def bump(self):
        self.count += 1

    async def reset(self):
        self.count = 0


class BenchBoard(Component):
    """A parent with three BenchCard children. The parent passes each card's count as a prop."""

    _template_name = "bench/board.html"

    title: str = "Board"
    note: str = ""
    cards: list[dict] = [
        {"id": "card-1", "label": "one", "count": 1},
        {"id": "card-2", "label": "two", "count": 2},
        {"id": "card-3", "label": "three", "count": 3},
    ]

    async def set_note(self, note: str = "note"):
        self.note = note
