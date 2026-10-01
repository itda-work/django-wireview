"""LiveComponents placed in slots.

tests/test_slot_live_components_e2e.py drives it. The host fills a nested
component's slot and a LiveComponent's slot with LiveComponents of its own, and
the frame's slot with a plain component too. The frame and the box render those
slots on their own -- the frame on its join and its events, the box on its
events -- and what is in them must stay on the page as it is and keep answering
clicks. The frame can also hide its slot and show it again, and raise, which
joins it again.
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
    show: bool = True

    async def click(self):
        self.clicks += 1

    async def toggle(self):
        self.show = not self.show

    async def boom(self):
        raise RuntimeError("slotprobe: the frame raised on purpose")


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


class SlotProbePlain(Component):
    class Meta:
        template_name = "slotprobe/plain.html"

    clicks: int = 0

    async def click(self):
        self.clicks += 1
