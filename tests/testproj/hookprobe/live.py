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
- The shelf draws the rooted component again with ``{% component %}``. That
  instance has to be joined like the first one: its ``joined()`` runs and its
  own viewport binding is watched, and sent to it, not to the shelf. So is
  one the shelf's patch draws inside it (``reach``).
- The shelf's own ``sprout`` draws a hook and pushes an event to it from the
  same handler: the event arrives before the frame that patches the hook in.
- The shelf's patch draws a hook inside the sprout (``light``), and the
  sprout's ``update()`` pushes to it: the sprout's element is there and no
  patch of its own is due.
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
    # Calls of a handler named like the rooted component's viewport handler: a
    # binding inside that component is not this one's to send
    stolen: int = 0
    # Handed to the rooted component, whose template draws a second viewport
    # binding for it: this one's patch draws a binding that is the rooted one's
    reach: bool = False
    # Handed to the sprout, whose update() pushes to the hook it draws for it
    lit: bool = False

    async def take_away(self):
        self.show = False

    async def bring_back(self):
        self.show = True

    async def sprout(self):
        self.sprouted = True
        # For the hook the same render draws: it is not on the page yet
        await self.push_event("pinged", {})

    async def light(self):
        self.lit = True

    async def reach_out(self):
        self.reach = True

    async def more(self):
        self.stolen += 1

    async def further(self):
        self.stolen += 1


class HookProbeSprout(LiveComponent):
    class Meta:
        template_name = "hookprobe/sprout.html"

    lit: bool = False

    async def joined(self):
        await self.push_event("pinged", {})

    async def update(self, **assigns):
        await super().update(**assigns)
        if assigns.get("lit"):
            # For the hook the shelf's patch draws in this element: the patch is
            # the shelf's, so this element is there and its own patch is not due
            await self.push_event("lit", {})


class HookProbeRooted(Component):
    class Meta:
        template_name = "hookprobe/rooted.html"

    # joined() adds 100 and its own viewport binding 1, so 101 says both ran
    mores: int = 0
    reach: bool = False
    furthers: int = 0

    async def joined(self):
        self.mores += 100

    async def more(self):
        self.mores += 1

    async def further(self):
        self.furthers += 1

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
