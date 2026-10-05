"""Back and Forward after the ways a page moves (#168, #169).

tests/test_history_e2e.py drives it. ``tab`` follows the URL (``params_changed``),
``count`` changes only through events, so after a move a test can tell what the
URL brought back from what only the page on screen knew. ``rendered`` names the
page render the component was built from: a fetch of the URL gives a new number.
``HistoryLeaf`` is the box's LiveComponent, whose ``count`` a patch must keep as
well; it hears ``params_changed`` too (``heard``). ``HistoryDock`` is sticky and on
every page: a navigation carries it across (#170). ``HistoryTray`` is sticky too,
but only the box page has it: a navigation away ends it, and it must not hear
where the navigation went. ``HistoryBar`` is not sticky and the bar pages
both have it under one id, as a layout's navigation bar would be: Back paints
the cached page over the one being left, and the bar there is still the left
page's (#170).

``HEARD`` is every ``params_changed`` a connection ran, in order: which component,
which instance (the box's ``rendered``), and the uri. The tests read it to tell
who heard a navigation's params (#170). An HTTP render runs it too, before the
first HTML (#177): that goes to ``HTTP_HEARD`` instead, as nothing on the page
was told anything yet.

``HistoryGuarded`` and ``HistoryGuardedList`` (the ``guarded/`` page) return
early when the query matches their own field: the join must still hear it,
because it starts from the state before the HTTP render heard it (#177).
"""

import itertools

from wireview import AsyncResult, Component, LiveComponent

_renders = itertools.count(1)

#: (component id, the box's ``rendered`` or 0, uri), in the order they were heard
HEARD: list[tuple[str, int, str]] = []
#: The same, heard by an HTTP render
HTTP_HEARD: list[tuple[str, int, str]] = []


def _heard(component, instance: int, uri: str) -> None:
    # An HTTP render's instance has no connection to send to
    (HEARD if component.wire.channel_name else HTTP_HEARD).append((component.id, instance, uri))


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
        _heard(self, self.rendered, uri)
        self.tab = params.get("tab", "a")

    async def bump(self, **_rest):
        self.count += 1

    async def push_same(self, **_rest):
        # The URL on screen, once a push gave it a query
        await self.wire.push_to(f"?tab={self.tab}")

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
        _heard(self, 0, uri)
        self.heard = params.get("tab", "a")

    async def bump(self, **_rest):
        self.count += 1


class HistoryDock(Component):
    """Sticky, on every page: what a navigation tells the components it carries (#170)."""

    class Meta:
        template_name = "historyprobe/dock.html"
        sticky = True

    heard: str = "-"
    times: int = 0

    async def params_changed(self, params, uri):
        _heard(self, 0, uri)
        self.heard = params.get("tab", "a")
        self.times += 1


class HistoryTray(Component):
    """Sticky, on the box page only: a navigation to another page ends it (#170)."""

    class Meta:
        template_name = "historyprobe/tray.html"
        sticky = True

    async def params_changed(self, params, uri):
        _heard(self, 0, uri)


class HistoryBar(Component):
    """Not sticky, under the same id on both bar pages (#170). ``built`` names
    the instance; only the one a page's own join made may hear its params."""

    class Meta:
        template_name = "historyprobe/bar.html"

    tab: str = "-"
    built: int = 0

    def model_post_init(self, context) -> None:
        super().model_post_init(context)
        if not self.built:
            self.built = next(_renders)

    async def params_changed(self, params, uri):
        _heard(self, self.built, uri)
        self.tab = params.get("tab", "-")


class HistoryGuarded(Component):
    """Loads rows for the query, and returns early when the query matches its own ``q`` (#177).

    The HTTP render cancels the load (nothing connected would hear it finish).
    The join starts from the state before that render heard the query, so the
    guard lets the load start again rather than leave the page on ``loading``.
    """

    class Meta:
        template_name = "historyprobe/guarded.html"

    q: str = ""
    rows: AsyncResult[str] | None = None

    async def params_changed(self, params, uri):
        q = params.get("q", "")
        if q == self.q:
            return
        self.q = q
        self.rows = await self.assign_async(self._load(q))

    async def _load(self, q: str) -> str:
        return q.upper()


class HistoryGuardedList(Component):
    """The same guard, with its result in a temporary assign the signed state leaves out (#177)."""

    class Meta:
        template_name = "historyprobe/guarded_list.html"
        temporary_assigns = ["items"]

    q: str = ""
    items: list[str] = []
    connected: bool = False

    async def joined(self):
        self.connected = True

    async def params_changed(self, params, uri):
        q = params.get("q", "")
        if q == self.q:
            return
        self.q = q
        self.items = [q, q * 2]
