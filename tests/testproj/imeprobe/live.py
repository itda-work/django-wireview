"""Renders that land while an IME is composing in a field (#168).

tests/test_ime_e2e.py drives it. ``q`` and ``notes`` send what is in them on a
debounced ``input`` -- which fires during a composition too, so the server
renders the composing text back -- and ``memo`` sends nothing. ``pings`` moves
when anyone on the page's topic presses ``ping``: a render the user composing
did not ask for.
"""

from wireview import Component


class ImeBox(Component):
    class Meta:
        template_name = "imeprobe/box.html"
        subscriptions = {"imeprobe"}

    q: str = ""
    notes: str = ""
    pings: int = 0

    async def search(self, q: str = "", **_rest):
        self.q = q

    async def note(self, notes: str = "", **_rest):
        self.notes = notes

    async def ping(self, **_rest):
        await self.broadcast("imeprobe")

    async def notification(self, channel, **kwargs):
        self.pings += 1
