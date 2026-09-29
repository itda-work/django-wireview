"""A temporary assign on a live page (#111).

tests/test_temporary_assigns_e2e.py drives it: the list is loaded, reset after
the render, and an event that has nothing to do with it must leave it on the page.
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
