"""Fields the user is editing, and the renders that land while they do (#91).

tests/test_input_values_e2e.py drives it. None of the text inputs renders its
value except `server_set`, so without protection every render resets them to
empty: that is the defect. The Enter input and the form render no value on
purpose too: their answers are meant to empty them, as examples/todo does.
"""

import asyncio

from wireview import Component


class ValueProbe(Component):
    _template_name = "valueprobe/probe.html"

    pings: int = 0
    typed: str = ""
    added: str = ""
    submitted: str = ""
    server_set: str = ""

    # Every named input of the component rides along with each event.
    async def ping(self, **_rest):
        self.pings += 1

    async def typing(self, draft: str = "", **_rest):
        self.typed = draft

    async def add(self, item: str = "", **_rest):
        self.added = item

    async def submit(self, first: str = "", second: str = "", **_rest):
        self.submitted = f"{first}+{second}"

    async def set_from_server(self, **_rest):
        self.server_set = "from server"

    async def slow_set_from_server(self, **_rest):
        await asyncio.sleep(0.3)
        self.server_set = "late server value"
