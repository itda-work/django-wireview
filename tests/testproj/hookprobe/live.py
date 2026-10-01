"""Hook lifecycle edges the hooks example does not reach (#107, #108).

tests/test_hooks_e2e.py drives it.

- A component nested in another owns the hooks inside it: what it pushes
  reaches them, though its parent joined first and scanned them first.
- A component whose *root* element carries a hook, rendered by a parent that
  can stop rendering it. The component leaves with its root; a hook on the root
  and one inside it both have to hear ``destroyed()``.
- Rows that a render moves keep a live hook: a move is a removal and an
  insertion to a MutationObserver, and the hook is destroyed on the way out.
- Two components whose hooks wait on ``pushEvent`` replies at the same time.
  Each reply has to reach the callback that asked, whatever order the
  components sit in on the page.
- A LiveComponent the shelf brings in pushes an event from ``joined()``. It
  arrives before the render that brings the element is patched in, so before
  the hook it is for exists.
"""

from wireview import Component, LiveComponent


class HookProbeShelf(Component):
    class Meta:
        template_name = "hookprobe/shelf.html"

    show: bool = True
    sprouted: bool = False

    async def take_away(self):
        self.show = False

    async def bring_back(self):
        self.show = True

    async def sprout(self):
        self.sprouted = True


class HookProbeSprout(LiveComponent):
    class Meta:
        template_name = "hookprobe/sprout.html"

    async def joined(self):
        await self.push_event("pinged", {})


class HookProbeRooted(Component):
    class Meta:
        template_name = "hookprobe/rooted.html"

    async def ping(self):
        await self.push_event("pinged", {})


class HookProbeRows(Component):
    """Rows with ids and a hook each, which a render moves."""

    class Meta:
        template_name = "hookprobe/rows.html"

    rows: list[str] = ["a", "b", "c"]

    async def rotate(self):
        self.rows = self.rows[1:] + self.rows[:1]

    async def ping_rows(self):
        await self.push_event("pinged", {})


class HookProbeAsker(Component):
    class Meta:
        template_name = "hookprobe/asker.html"

    who: str = ""

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "ask":
            return {"answer": self.who}
