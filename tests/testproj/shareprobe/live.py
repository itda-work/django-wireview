"""A board every viewer sees alike, and a greeting that is each viewer's own (#176).

``ShareBoard`` declares ``Meta.shared_render``: the connections that handle one
broadcast render it once between them. ``ShareGreeting`` hears the same
broadcast and names its viewer, so it does not declare it. tests/test_shared_render_e2e.py
opens the page in several browser contexts, one user each.
"""

from collections import Counter

from wireview import Component, abroadcast

TOPIC = "shareprobe"
#: What every viewer's board shows: data outside any component, as a store or a table would be
BOARD = {"headline": "quiet", "items": ["one", "two"]}
#: Renders per class, counted where every render reads the headline
RENDERS: Counter[str] = Counter()


class ShareBoard(Component):
    class Meta:
        template_name = "shareprobe/board.html"
        subscriptions = {TOPIC}
        shared_render = True

    @property
    def headline(self) -> str:
        RENDERS["ShareBoard"] += 1
        return BOARD["headline"]

    @property
    def items(self) -> list[str]:
        return BOARD["items"]

    async def announce(self, text: str):
        BOARD["headline"] = text
        BOARD["items"] = [*BOARD["items"], text]
        await abroadcast(TOPIC)

    async def notification(self, channel: str, **kwargs):
        pass


class ShareGreeting(Component):
    class Meta:
        template_name = "shareprobe/greeting.html"
        subscriptions = {TOPIC}

    @property
    def headline(self) -> str:
        RENDERS["ShareGreeting"] += 1
        return BOARD["headline"]

    async def notification(self, channel: str, **kwargs):
        pass
