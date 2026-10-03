"""What a page gets back when its socket comes back (#168).

tests/test_reconnect_state_e2e.py drives it. ``count`` and ``note`` change only
through events, so after a reconnect they say whether the join rebuilt the
component from the last render's ``data-state`` or from the page load's. ``pid``
names the process that rendered, so a test can tell a restarted server, or
another worker, from the one the page came from. ``scratch`` is not state at all:
what the user typed there is the page's alone.
"""

import os

from wireview import Component


class ReconnectBox(Component):
    class Meta:
        template_name = "reconnectprobe/box.html"

    count: int = 0
    note: str = ""

    @property
    def pid(self) -> int:
        return os.getpid()

    async def bump(self, **_rest):
        self.count += 1

    async def save(self, note: str = "", **_rest):
        self.note = note

    async def nothing(self, **_rest):
        pass
