import asyncio
import threading
import typing as t

from wireview import AsyncResult, Component


class StickyPlayer(Component):
    """Survives a boosted move to a page that has it again (#72)."""

    class Meta:
        template_name = "stickyprobe/player.html"
        sticky = True

    count: int = 0

    async def increment(self):
        self.count += 1


class StickyTicker(Component):
    """Rendered without an id: sticks all the same, under an id from its class (#128)."""

    class Meta:
        template_name = "stickyprobe/ticker.html"
        sticky = True

    count: int = 0

    async def increment(self):
        self.count += 1


class PlainCounter(Component):
    """The control: the same id on both pages, but not sticky, so it starts over."""

    class Meta:
        template_name = "stickyprobe/plain.html"

    count: int = 0

    async def increment(self):
        self.count += 1


class LateSticky(Component):
    """Sticky, and draws ``inner`` only once the work its joined() starts again lands.

    ``gate`` holds the work. Its join carries ``inner``'s entry from the HTTP
    render, and keeps it across a boosted move while the work is held.
    """

    class Meta:
        template_name = "stickyprobe/late.html"
        sticky = True

    inner: bool = True
    data: AsyncResult[str] = AsyncResult.success("ok")
    gate: t.ClassVar[threading.Event] = threading.Event()

    async def joined(self):
        self.data = await self.assign_async(self._load())

    async def _load(self) -> str:
        while not LateSticky.gate.is_set():
            await asyncio.sleep(0.01)
        return "ok"


class InnerCounter(Component):
    """Drawn inside ``LateSticky`` on one page, and as a root of its own on the next."""

    class Meta:
        template_name = "stickyprobe/inner.html"

    count: int = 0

    async def increment(self):
        self.count += 1
