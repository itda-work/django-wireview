"""Back and Forward after the ways a page moves (#168, #169).

tests/test_history_e2e.py drives it. ``tab`` follows the URL (``params_changed``),
``count`` changes only through events, so after a move a test can tell what the
URL brought back from what only the page on screen knew. ``rendered`` names the
page render the component was built from: a fetch of the URL gives a new number.
``HistoryLeaf`` is the box's LiveComponent, whose ``count`` a patch must keep as
well; it hears ``params_changed`` too (``heard``).

``HEARD`` is every ``params_changed`` on the server, in order: which component,
which instance (the box's ``rendered``), and the uri. The tests read it to tell
who heard a navigation's params (#170).
"""

import itertools

from wireview import Component, LiveComponent

_renders = itertools.count(1)

#: (component id, the box's ``rendered`` or 0, uri), in the order they were heard
HEARD: list[tuple[str, int, str]] = []


class HistoryBox(Component):
    class Meta:
        template_name = "historyprobe/box.html"

    tab: str = "a"
    count: int = 0
    rendered: int = 0

    def model_post_init(self, context) -> None:
        super().model_post_init(context)
        if not self.rendered:
            self.rendered = next(_renders)

    async def params_changed(self, params, uri):
        HEARD.append((self.id, self.rendered, uri))
        self.tab = params.get("tab", "a")

    async def bump(self, **_rest):
        self.count += 1

    async def push(self, tab: str = "a", **_rest):
        await self.wire.push_to(f"?tab={tab}")

    async def replace(self, tab: str = "a", **_rest):
        await self.wire.replace_to(f"?tab={tab}")

    async def push_other(self, **_rest):
        await self.wire.push_to("/historyprobe/other/?tab=o")

    async def replace_other(self, **_rest):
        await self.wire.replace_to("/historyprobe/other/?tab=o")

    async def redirect(self, tab: str = "a", **_rest):
        await self.wire.redirect_to(f"?tab={tab}")


class HistoryLeaf(LiveComponent):
    class Meta:
        template_name = "historyprobe/leaf.html"

    count: int = 0
    heard: str = ""

    async def params_changed(self, params, uri):
        HEARD.append((self.id, 0, uri))
        self.heard = params.get("tab", "a")

    async def bump(self, **_rest):
        self.count += 1
