import asyncio
import threading

from wireview import Component

#: ``slow_bump`` sets ENTERED once it has counted, and goes on when the test sets RELEASE
ENTERED = threading.Event()
RELEASE = threading.Event()


class ReloadBox(Component):
    """A counter whose template the tests rewrite under the dev server's feet (#180).

    The template ships here; a test shadows it from a directory of its own put
    first in ``TEMPLATES["DIRS"]`` (``testproj.reloadprobe.shadow``) and changes
    that copy.
    """

    class Meta:
        template_name = "reloadprobe/box.html"

    count: int = 0

    async def bump(self):
        self.count += 1

    async def slow_bump(self):
        """A handler still being handled when the file changes: counted, not yet answered."""
        self.count += 1
        ENTERED.set()
        await asyncio.to_thread(RELEASE.wait, 10)
