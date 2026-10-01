"""LiveComponents placed in slots.

tests/test_slot_live_components_e2e.py drives it. The host fills a nested
component's slot and a LiveComponent's slot with LiveComponents of its own. The
frame and the box render those slots on their own -- the frame on its join and
its events, the box on its events -- and the LiveComponents must stay on the
page and keep answering clicks.
"""

from wireview import Component, LiveComponent


class SlotProbeHost(Component):
    class Meta:
        template_name = "slotprobe/host.html"

    n: int = 0

    async def bump(self):
        self.n += 1


class SlotProbeFrame(Component):
    class Meta:
        template_name = "slotprobe/frame.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlotProbeBox(LiveComponent):
    class Meta:
        template_name = "slotprobe/box.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1


class SlotProbeLeaf(LiveComponent):
    class Meta:
        template_name = "slotprobe/leaf.html"

    pokes: int = 0

    async def poke(self):
        self.pokes += 1
