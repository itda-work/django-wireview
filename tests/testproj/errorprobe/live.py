"""Components whose server code raises (#94).

tests/test_errors_e2e.py drives it. ``bump_then_raise`` changes the state and
then raises, so the page shows whether that change was rolled back;
``ErrorJoin`` cannot join at all, and ``ErrorHolder`` draws one again on
every render, with an ``ErrorJoinNest`` -- which cannot join either -- holding
a LiveComponent, ``held-child``, whose hook and button would reach the
instance the holder's pass builds for it. The ``late/`` page holds one ``ErrorBox``
and links back to itself, so a test can join it again under its id while an
answer to the join before is still on its way (#139). Its ``ErrorNest`` holds
a LiveComponent, ``nest-child``, whose own render -- or the ``remove`` its
``vanish`` asks for -- can be on its way when the parent joins again;
``?visit=swap`` puts a root ``ErrorBox`` under that id instead (#146).
Enter in the child's field asks for ``vanish`` too, and the field's blur is
bound, so a test can see whether the page sends it while the ``remove`` applies.
The ``slot/`` page's ``ErrorSlotHost`` puts its own LiveComponent in the slot
of a component whose join fails, next to one that holds its own.
"""

from wireview import Component, LiveComponent

# What the handlers below heard, as (component id, what): the server runs in the
# test's process, so a test reads it to tell an event the server refused from
# one that never left the page.
HEARD: list[tuple[str, str]] = []


class ErrorBox(Component):
    class Meta:
        template_name = "errorprobe/box.html"

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1

    async def bump_then_raise(self, **_rest):
        self.count += 100
        raise RuntimeError("errorprobe: the handler raised on purpose")


class ErrorJoin(Component):
    class Meta:
        template_name = "errorprobe/join.html"

    pokes: int = 0

    async def joined(self):
        raise RuntimeError("errorprobe: joined() raised on purpose")

    async def poke(self, **_rest):
        HEARD.append((self.id, "poke"))
        self.pokes += 1

    async def handle_hook_event(self, hook_id, event, payload):
        HEARD.append((self.id, "hook"))
        self.pokes += 1


class ErrorHolder(Component):
    """Holds an ``ErrorJoin`` and renders again on ``bump``: its template pass
    builds a new instance under the id whose join failed."""

    class Meta:
        template_name = "errorprobe/holder.html"

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1


class ErrorJoinNest(Component):
    class Meta:
        template_name = "errorprobe/join_nest.html"

    async def joined(self):
        raise RuntimeError("errorprobe: the nest's joined() raised on purpose")


class ErrorHeldChild(LiveComponent):
    class Meta:
        template_name = "errorprobe/held_child.html"

    pokes: int = 0

    async def poke(self, **_rest):
        HEARD.append((self.id, "poke"))
        self.pokes += 1

    async def handle_hook_event(self, hook_id, event, payload):
        HEARD.append((self.id, "hook"))
        self.pokes += 1


class ErrorNest(Component):
    class Meta:
        template_name = "errorprobe/nest.html"


class ErrorNestChild(LiveComponent):
    class Meta:
        template_name = "errorprobe/nest_child.html"

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1

    async def vanish(self, **_rest):
        await self.destroy()

    async def blurred(self, **_rest):
        pass


class ErrorSlotHost(Component):
    """The ``slot/`` page's root: two ``ErrorSlotNest`` whose joins fail, one
    with ``slot-leaf`` in its slot -- the host's own LiveComponent, drawn in the
    host's template pass -- and one holding ``own-leaf`` in its own template.
    ``toggle`` takes both nests off the page."""

    class Meta:
        template_name = "errorprobe/slot_host.html"

    shown: bool = True
    count: int = 0

    async def toggle(self, **_rest):
        self.shown = not self.shown

    async def bump(self, **_rest):
        self.count += 1


class ErrorSlotNest(Component):
    class Meta:
        template_name = "errorprobe/slot_nest.html"

    async def joined(self):
        raise RuntimeError("errorprobe: the slot nest's joined() raised on purpose")


class ErrorSlotLeaf(LiveComponent):
    class Meta:
        template_name = "errorprobe/slot_leaf.html"

    pokes: int = 0

    async def poke(self, **_rest):
        HEARD.append((self.id, "poke"))
        self.pokes += 1

    async def handle_hook_event(self, hook_id, event, payload):
        HEARD.append((self.id, "hook"))
