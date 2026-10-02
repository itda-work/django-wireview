"""A temporary assign on a live page (#111).

tests/test_temporary_assigns_e2e.py drives it: the list is loaded, reset after
the render, and an event that has nothing to do with it must leave it on the page.
On ``?nest=1`` the probe is drawn in a host's pass, in a frame's slot, and from
the host's ``{% component_block %}`` with a fill reading the host's ``title``;
the host's or the frame's render must leave it too.

``?rows=1``: a list whose rows a nested component draws, a row changing on its
own. ``?live=1``: a list and a LiveComponent kept together in a block the
host's pass draws. ``?notes=1``: a list kept in a block with a nested component
whose own temporary assign changes, its signed state staying as it was.
``?joined=1``: the same, the nested component loading its temporary assign in joined().
"""

from wireview import Component, LiveComponent


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
    title: str = "filled"

    async def bump(self):
        self.count += 1


class TempFrame(Component):
    class Meta:
        template_name = "tempprobe/frame.html"

    count: int = 0

    async def bump(self):
        self.count += 1


class TempRows(TempProbe):
    class Meta:
        template_name = "tempprobe/rows.html"


class TempRow(Component):
    class Meta:
        template_name = "tempprobe/row.html"

    text: str = ""

    async def shout(self):
        self.text = self.text.upper()


class TempLiveHost(TempHost):
    class Meta:
        template_name = "tempprobe/livehost.html"


class TempLiveProbe(TempProbe):
    class Meta:
        template_name = "tempprobe/liveprobe.html"


class TempLive(LiveComponent):
    class Meta:
        template_name = "tempprobe/live.html"

    hits: int = 0

    async def hit(self):
        self.hits += 1


class TempNotesProbe(TempProbe):
    class Meta:
        template_name = "tempprobe/notesprobe.html"


class TempNotes(Component):
    class Meta:
        template_name = "tempprobe/notes.html"
        temporary_assigns = {"notes"}

    notes: list[str] = []

    async def note(self):
        self.notes = ["x", "y"]


class TempJoinedProbe(TempProbe):
    class Meta:
        template_name = "tempprobe/joinedprobe.html"


class TempJoinedNotes(TempNotes):
    async def joined(self):
        self.notes = ["x", "y"]
