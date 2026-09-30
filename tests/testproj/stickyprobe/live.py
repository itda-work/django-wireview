from wireview import Component


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
