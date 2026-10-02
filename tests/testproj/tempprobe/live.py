"""A temporary assign on a live page (#111).

tests/test_temporary_assigns_e2e.py drives it: the list is loaded, reset after
the render, and an event that has nothing to do with it must leave it on the page.
On ``?nest=1`` the probe is drawn in a host's pass and in a frame's slot, and
the host's or the frame's render must leave it too.
"""

from wireview import Component


class TempProbe(Component):
    class Meta:
        template_name = "tempprobe/probe.html"
        temporary_assigns = {"messages"}

    count: int = 0
    messages: list[str] = []

    async def load(self):
        self.messages = ["one", "two", "three"]

    async def bump(self):
        self.count += 1


class TempHost(Component):
    class Meta:
        template_name = "tempprobe/host.html"

    count: int = 0

    async def bump(self):
        self.count += 1


class TempFrame(Component):
    class Meta:
        template_name = "tempprobe/frame.html"

    count: int = 0

    async def bump(self):
        self.count += 1
