"""Streams in a real browser: dom_id, limit, and infinite scroll.

tests/test_streams_e2e.py drives it. The items are plain dicts, so the default
``{name}-{item.pk}`` id cannot apply and ``dom_id`` has to. ``load_more`` is what
``wire-viewport-bottom`` calls as the sentinel under the list comes into view.
"""

import asyncio

from wireview import Component, LiveComponent

PAGE = 15


def row_id(item: dict) -> str:
    return f"row-{item['n']}"


async def insert_tick(component, n: int) -> None:
    # Newest first, at most three on the page
    await component.stream_insert(
        "ticks",
        {"n": n},
        at=0,
        limit=3,
        template="streamprobe/tick.html",
        dom_id=lambda i: f"tick-{i['n']}",
    )


class TickChild(LiveComponent):
    """A component nested in the probe with a stream of the same name, ahead of the probe's own."""

    class Meta:
        template_name = "streamprobe/child.html"

    ticks: int = 0

    async def tick(self, **_rest):
        self.ticks += 1
        await insert_tick(self, self.ticks)


class StreamProbe(Component):
    class Meta:
        template_name = "streamprobe/probe.html"

    #: Rows per page. The default fills a 600px viewport (rows are 80px); ``?size=1`` does not.
    size: int = PAGE
    #: Seconds joined() waits before the first page: long enough for the viewport
    #: bindings to be judged before the rows arrive, as a slow query would (#112).
    delay: float = 0
    pages: int = 1
    ticks: int = 0
    newer: int = 0
    #: Render a TickChild ahead of the ticks list (``?nest=1``)
    nest: bool = False

    async def joined(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        await self.stream("rows", [{"n": n} for n in range(self.size)], template="streamprobe/row.html", dom_id=row_id)

    async def load_more(self, **_rest):
        start = self.pages * self.size
        self.pages += 1
        for n in range(start, start + self.size):
            await self.stream_insert("rows", {"n": n}, template="streamprobe/row.html", dom_id=row_id)

    async def load_newer(self, **_rest):
        self.newer += 1

    async def tick(self, **_rest):
        self.ticks += 1
        await insert_tick(self, self.ticks)
