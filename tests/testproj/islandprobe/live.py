"""Islands in a browser (#221). tests/test_islands_e2e.py drives it.

- The board's island is mounted once with its props, handed them again only
  when a render changes them, and keeps what it drew. Its ``ctx`` pushes to
  the board (``bump``: a reply behind the render) and hears the board's
  ``push_event`` (``greet``). ``boom`` raises: the page joins the board again,
  and the island stays.
- The dock is sticky and on both pages: its island crosses a boosted move.
  The board is on the first page only: its island is unmounted on the way out.
  ``again/`` draws the board under the same id: a move there keeps its element,
  its hooks and its island, which hears the new page's props.
- ``holder/a/`` and ``holder/b/`` draw an island under the same id in two
  components of different ids: a move hands the element to the other one,
  which mounts an island of its own on it. ``sides/a/`` and ``sides/b/`` draw
  both of two components, the island in one or the other: a move hands it
  across while both stay.
"""

from wireview import Component


class IslandBoard(Component):
    class Meta:
        template_name = "islandprobe/board.html"

    zoom: int = 3
    note: str = ""

    @property
    def map_props(self) -> dict:
        return {"zoom": self.zoom}

    async def zoom_in(self):
        self.zoom += 1

    async def write_note(self):
        """A render whose props are the same."""
        self.note += "x"

    async def boom(self):
        raise RuntimeError("islandprobe boom")

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "bump":
            self.zoom += 1
            return {"zoom": self.zoom}
        if event == "greet":
            await self.push_event("greeted", {"to": payload.get("to")})
            return {"greeted": True}


class IslandHolder(Component):
    class Meta:
        template_name = "islandprobe/holder.html"

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "whose":
            return {"id": self.id}


class IslandSide(Component):
    class Meta:
        template_name = "islandprobe/side.html"

    holds: bool = False

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "whose":
            return {"id": self.id}


class IslandDock(Component):
    class Meta:
        template_name = "islandprobe/dock.html"
        sticky = True

    count: int = 0

    async def increment(self):
        self.count += 1
