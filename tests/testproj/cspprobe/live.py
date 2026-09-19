"""A component whose events cover every binding shape, served under a strict CSP.

tests/test_csp_e2e.py loads it with a policy that allows no inline script and no
inline style without a nonce, then checks that each binding still works and that
the browser reported no violation (#90, docs/design/csp-event-binding.md).
"""

import asyncio

from wireview import JS, Component


class CspProbe(Component):
    _template_name = "cspprobe/probe.html"

    count: int = 0
    first: str = ""
    second: str = ""
    keys: str = ""
    inner: int = 0
    outer: int = 0
    submitted: str = ""
    received: str = ""

    @property
    def toggle_panel(self) -> JS:
        """A client-only command chain; templates cannot call JS() with arguments."""
        return JS().toggle("#panel")

    async def joined(self):
        self.allow_upload("files", accept=[".txt"], max_entries=2)

    async def increment(self, **_rest):
        self.count += 1

    # Events send every named input of the component along; the handlers take the one they want.
    async def set_first(self, first: str = "", **_rest):
        self.first = first

    async def set_second(self, second: str = "", **_rest):
        self.second = second

    async def key(self, name: str = "", **_rest):
        self.keys += name

    async def click_inner(self, **_rest):
        self.inner += 1

    async def click_outer(self, **_rest):
        self.outer += 1

    async def slow_save(self, **_rest):
        await asyncio.sleep(0.6)

    async def save(self, q: str = "", **_rest):
        self.submitted = q

    async def on_upload_complete(self, name: str, entry) -> None:
        async for upload in self.consume_uploads(name):
            self.received = f"{upload.name}:{len(upload.read())}"
