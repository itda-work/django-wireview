"""What a page gets back when its socket comes back (#168).

tests/test_reconnect_state_e2e.py drives it. ``count`` and ``note`` change only
through events, so after a reconnect they say whether the join rebuilt the
component from the last render's ``data-state`` or from the page load's. ``pid``
names the process that rendered, so a test can tell a restarted server, or
another worker, from the one the page came from. ``scratch`` is not state at all:
what the user typed there is the page's alone.

``RECONNECTPROBE_FIELDS`` picks the class's version, as a deploy would (#231):
unset is the first; ``default`` adds ``currency`` with a default, ``required``
adds it with none. A restarted process with another value joins the page the
first one drew.
"""

import os

from wireview import Component

FIELDS = os.environ.get("RECONNECTPROBE_FIELDS", "")


class _Box(Component, public=False):
    class Meta:
        template_name = "reconnectprobe/box.html"

    count: int = 0
    note: str = ""

    @property
    def version(self) -> str:
        return FIELDS or "first"

    @property
    def pid(self) -> int:
        return os.getpid()

    async def bump(self, **_rest):
        self.count += 1

    async def save(self, note: str = "", **_rest):
        self.note = note

    async def nothing(self, **_rest):
        pass


if FIELDS == "default":

    class ReconnectBox(_Box):
        currency: str = "KRW"

elif FIELDS == "required":

    class ReconnectBox(_Box):
        # The page passes it (box's {% component %}); a state signed before has none
        currency: str

else:

    class ReconnectBox(_Box):
        pass
