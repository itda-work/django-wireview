"""A LiveComponent a nested component hides by its drawer's prop.

tests/test_inline_pass_lifecycle_e2e.py drives it. The root passes ``shown``
and ``note`` to the nest; the nest draws the leaf behind ``{% if shown %}`` and
passes it the note. The root's render draws the nest within its own pass, so
the leaf's ``leaving()``, ``joined()`` and ``update()`` ride on that render.
"""

from wireview import Component, LiveComponent

# What the leaves' hooks heard, as (hook, id, count); the test reads it
HEARD: list[tuple[str, str, int]] = []


class NestLeaf(LiveComponent):
    class Meta:
        template_name = "nestprobe/leaf.html"

    count: int = 0
    note: str = ""

    async def joined(self):
        HEARD.append(("joined", self.id, self.count))

    async def leaving(self):
        HEARD.append(("leaving", self.id, self.count))

    async def bump(self):
        self.count += 1


class NestBox(Component):
    class Meta:
        template_name = "nestprobe/box.html"

    shown: bool = True
    note: str = ""


class NestRoot(Component):
    class Meta:
        template_name = "nestprobe/root.html"

    shown: bool = True
    note: str = ""

    async def toggle(self):
        self.shown = not self.shown

    async def write(self):
        self.note = f"{self.note}!"
