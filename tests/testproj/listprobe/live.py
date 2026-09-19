"""A list whose rows carry client-only state, for the item-rearrangement E2E.

Each row has an id and an input the server never sees. Moving rows must leave
the browser in the same state whether the diff arrived as runs of the previous
items (protocol version 2, GAP-030) or positionally (a client that named no
version): the new form changes what goes over the wire, not what the DOM ends
up as. tests/test_comprehension_moves_e2e.py drives it.
"""

from wireview import Component


class ListProbe(Component):
    _template_name = "listprobe/probe.html"

    rows: list[str] = [f"r{n}" for n in range(8)]

    async def rotate(self):
        self.rows.insert(0, self.rows.pop())

    async def insert_front(self):
        self.rows.insert(0, f"r{len(self.rows)}")

    async def remove_first(self):
        self.rows.pop(0)
