"""A component that runs every JS() command and every loading class in a real browser.

tests/test_js_commands_e2e.py drives it. Each command is a property because a
template cannot call JS() with arguments; each button binds one of them.
"""

import asyncio

from wireview import JS, Component

# Long enough for a Playwright assertion to see the loading state before the answer clears it.
SLOW = 1.5


class JsProbe(Component):
    class Meta:
        template_name = "jsprobe/probe.html"

    count: int = 0
    saved: int = 0
    submitted: str = ""
    changed: str = ""

    @property
    def show_box(self) -> JS:
        return JS().show("#box")

    @property
    def hide_box(self) -> JS:
        return JS().hide("#box")

    @property
    def add_hot(self) -> JS:
        return JS().add_class("#box", "hot big")

    @property
    def remove_hot(self) -> JS:
        return JS().remove_class("#box", "hot big")

    @property
    def toggle_hot(self) -> JS:
        return JS().toggle_class("#box", "hot")

    @property
    def mark(self) -> JS:
        return JS().set_attr("#box", "data-mark", "on")

    @property
    def unmark(self) -> JS:
        return JS().remove_attr("#box", "data-mark")

    @property
    def pulse(self) -> JS:
        return JS().transition("#box", "pulse", time=800)

    @property
    def focus_name(self) -> JS:
        return JS().focus("#name")

    @property
    def focus_fields(self) -> JS:
        return JS().focus_first("#fields")

    @property
    def ping(self) -> JS:
        return JS().dispatch("probe:ping", to="#box", detail={"n": 1})

    @property
    def go(self) -> JS:
        return JS().navigate("/jsprobe/landed/")

    @property
    def go_replace(self) -> JS:
        return JS().navigate("/jsprobe/landed/", replace=True)

    @property
    def chain(self) -> JS:
        return JS().add_class("#box", "chained").set_attr("#box", "data-step", "2").push("increment")

    @property
    def slow_chain(self) -> JS:
        return JS().toggle_class("#box", "opened").push("slow_save")

    page: str = ""

    async def params_changed(self, params, uri):
        self.page = params.get("page", "")

    async def go_redirect(self, **_rest):
        await self.wire.redirect_to("/jsprobe/landed/")

    async def go_push(self, **_rest):
        await self.wire.push_to("?page=2")

    async def go_replace_url(self, **_rest):
        await self.wire.replace_to("?page=3")

    async def announce(self, **_rest):
        await self.push_title("Announced")
        await self.put_flash("info", "Saved", timeout=0)

    async def increment(self, **_rest):
        self.count += 1

    async def slow_save(self, **_rest):
        await asyncio.sleep(SLOW)
        self.saved += 1

    async def slow_submit(self, q: str = "", **_rest):
        await asyncio.sleep(SLOW)
        self.submitted = q

    async def slow_change(self, pick: str = "", **_rest):
        await asyncio.sleep(SLOW)
        self.changed = pick


class JsProbeCard(Component):
    """The binding sits on the component's own root element."""

    class Meta:
        template_name = "jsprobe/card.html"

    clicks: int = 0

    async def slow_click(self, **_rest):
        await asyncio.sleep(SLOW)
        self.clicks += 1


class JsProbeSlowJoin(Component):
    """A join that takes long enough for a click to go out before its answer (#118).

    The page counts as live once the join is sent, so an event can reach the
    server behind the join. The join's answer then arrives while the event
    waits for its own, and it answers nothing the event started. A broadcast
    it receives does the same after the join: its render lands between a click
    and the click's answer.
    """

    class Meta:
        template_name = "jsprobe/slow_join.html"
        subscriptions = {"jsprobe-nudge"}

    ready: bool = False
    saved: int = 0
    nudged: int = 0

    async def joined(self):
        await asyncio.sleep(SLOW)
        self.ready = True

    async def save(self, **_rest):
        await asyncio.sleep(SLOW)
        self.saved += 1

    async def nudge(self, **_rest):
        await self.broadcast("jsprobe-nudge")

    async def notification(self, channel, **kwargs):
        # Slow, so a click sent now waits behind it and its render lands first
        await asyncio.sleep(SLOW)
        self.nudged += 1
