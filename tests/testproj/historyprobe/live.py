"""Back and Forward after the ways a page moves (#168).

tests/test_history_e2e.py drives it. ``tab`` follows the URL (``params_changed``),
``count`` changes only through events, so after Back a test can tell what the
URL brought back from what only the left page knew. ``rendered`` names the page
render the component was built from: a fetch of the URL gives a new number, a
cached copy the old one.
"""

import itertools

from wireview import Component

_renders = itertools.count(1)


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
        self.tab = params.get("tab", "a")

    async def bump(self, **_rest):
        self.count += 1

    async def push(self, tab: str = "a", **_rest):
        await self.wire.push_to(f"?tab={tab}")

    async def replace(self, tab: str = "a", **_rest):
        await self.wire.replace_to(f"?tab={tab}")

    async def push_other(self, **_rest):
        await self.wire.push_to("/historyprobe/other/")
