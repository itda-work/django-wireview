"""Components whose server code raises (#94).

tests/test_errors_e2e.py drives it. ``bump_then_raise`` changes the state and
then raises, so the page shows whether that change was rolled back;
``ErrorJoin`` cannot join at all, and ``ErrorHolder`` draws one again on
every render. The ``late/`` page holds one ``ErrorBox``
and links back to itself, so a test can join it again under its id while an
answer to the join before is still on its way (#139). Its ``ErrorNest`` holds
a LiveComponent, ``nest-child``, whose own render -- or the ``remove`` its
``vanish`` asks for -- can be on its way when the parent joins again;
``?visit=swap`` puts a root ``ErrorBox`` under that id instead (#146).
Enter in the child's field asks for ``vanish`` too, and the field's blur is
bound, so a test can see whether the page sends it while the ``remove`` applies.
"""

from wireview import Component, LiveComponent


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
        self.pokes += 1

    async def handle_hook_event(self, hook_id, event, payload):
        self.pokes += 1


class ErrorHolder(Component):
    """Holds an ``ErrorJoin`` and renders again on ``bump``: its template pass
    builds a new instance under the id whose join failed."""

    class Meta:
        template_name = "errorprobe/holder.html"

    count: int = 0

    async def bump(self, **_rest):
        self.count += 1


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
