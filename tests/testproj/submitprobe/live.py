"""Forms the server has the page submit (``wire.submit_form``) and forms locked while submitted (#246).

tests/test_submit_form_e2e.py drives it in a browser. ``SubmitLogin`` is the pattern
docs/features/forms.md teaches: the handler checks the fields over the socket,
and only then has the page post them to a view that logs the user in -- a
cookie no message on the socket can set.
"""

import asyncio
import threading

from wireview import Component

#: Set by a test to let ``SubmitLock.slow`` answer
GATE = threading.Event()
#: What the gated handler heard, in order
HEARD: list[str] = []


class SubmitLogin(Component):
    """A login form checked over the socket, then submitted to the login view."""

    class Meta:
        template_name = "submitprobe/login.html"

    username: str = ""
    error: str = ""
    checks: int = 0

    async def check(self, username: str = "", password: str = "", **_rest):
        self.checks += 1
        self.username = username
        if password != "secret":
            self.error = "wrong password"
            return
        self.error = ""
        await self.wire.submit_form("form")

    async def elsewhere(self, **_rest):
        """Asks for a form whose action is another origin's: the page refuses it."""
        await self.wire.submit_form("#away")

    async def missing(self, **_rest):
        await self.wire.submit_form("#no-such-form")

    async def go(self, which: str, **_rest):
        """Submit the form ``#<which>`` as it stands: the page decides what goes and where."""
        await self.wire.submit_form(f"#{which}")


class SubmitLock(Component):
    """A form whose handler waits for the test: what the page locks meanwhile, and gives back."""

    class Meta:
        template_name = "submitprobe/lock.html"

    saved: int = 0
    note: str = ""
    finished: bool = False

    async def slow(self, note: str = "", **_rest):
        # The submit button that sent it comes along, though the lock disabled it
        HEARD.append(f"slow:{note}:go={_rest.get('go')}")
        await asyncio.to_thread(GATE.wait, 15)
        self.saved += 1
        self.note = note

    async def finish(self, **_rest):
        """Draws the submit button disabled from now on: the server's word outlasts the lock."""
        HEARD.append("finish")
        await asyncio.to_thread(GATE.wait, 15)
        self.finished = True

    async def broken(self, **_rest):
        HEARD.append("broken")
        await asyncio.to_thread(GATE.wait, 15)
        raise RuntimeError("the handler of a locked form raised")
