"""Components whose server code raises (#94).

tests/test_errors_e2e.py drives it. ``bump_then_raise`` changes the state and
then raises, so the page shows whether that change was rolled back;
``ErrorJoin`` cannot join at all.
"""

from wireview import Component


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

    async def joined(self):
        raise RuntimeError("errorprobe: joined() raised on purpose")

    async def poke(self, **_rest):
        pass
