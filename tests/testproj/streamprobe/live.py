"""Streams in a real browser: dom_id, limit, and infinite scroll.

tests/test_streams_e2e.py drives it. The items are plain dicts, so the default
``{name}-{item.pk}`` id cannot apply and ``dom_id`` has to. ``load_more`` is what
``wire-viewport-bottom`` calls as the sentinel under the list comes into view.
The probe's ticks are buttons: a click deletes that tick, and a blur is counted,
so a test can see a stream op's removal send none.
"""

import asyncio

from wireview import JS, Component, LiveComponent

PAGE = 15


def row_id(item: dict) -> str:
    return f"row-{item['n']}"


async def insert_tick(component, n: int, template: str = "streamprobe/tick.html") -> None:
    # Newest first, at most three on the page
    await component.stream_insert(
        "ticks",
        {"n": n},
        at=0,
        limit=3,
        template=template,
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


class SeedChild(LiveComponent):
    """A LiveComponent that streams from joined(), so its ops can arrive before its element does.

    Its joined() also pushes a JS command, which has the same element to wait for.
    """

    class Meta:
        template_name = "streamprobe/seed.html"

    async def joined(self):
        # First, so it is the first of this component's commands to find no element
        await self.push_js(JS().dispatch("seed-joined"))
        await self.stream(
            "seeds", [{"n": 1}, {"n": 2}], template="streamprobe/tick.html", dom_id=lambda i: f"seed-{i['n']}"
        )
        await insert_tick(self, 3)


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
    #: Blurs of the probe's tick items: the user's leave one, a stream op's removal must not
    blurs: int = 0
    #: Render a TickChild ahead of the ticks list (``?nest=1``)
    nest: bool = False
    #: Render a SeedChild, which streams in its joined()
    seeded: bool = False

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
        await insert_tick(self, self.ticks, "streamprobe/tick_button.html")

    async def untick(self, n: int, **_rest):
        # A delete only, no render: the item leaves through the stream op alone
        await self.stream_delete("ticks", f"tick-{n}")

    async def seed(self, **_rest):
        self.seeded = not self.seeded

    async def blurred(self, **_rest):
        self.blurs += 1
