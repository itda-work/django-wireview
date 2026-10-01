"""Fields the user is editing, and the renders that land while they do (#91).

tests/test_input_values_e2e.py drives it. None of the text inputs renders its
value except `server_set`, so without protection every render resets them to
empty: that is the defect. The Enter input and the form render no value on
purpose too: their answers are meant to empty them, as examples/todo does.
"""

import asyncio

from wireview import JS, Component, LiveComponent

# The child's label comes from here, not from the parent's state, so a handler
# that bumps it changes the child's render and leaves the parent's HTML (and so
# its diff) empty: the answer is children-only (#92).
CHILD_COUNTER = {"n": 0}


class ValueChild(LiveComponent):
    class Meta:
        template_name = "valueprobe/child.html"

    label: str = ""


class ValueProbe(Component):
    class Meta:
        template_name = "valueprobe/probe.html"

    pings: int = 0
    typed: str = ""
    added: str = ""
    submitted: str = ""
    server_set: str = ""
    server_set_rendered: str = ""
    typed_slowly: str = ""
    added_after: str = ""
    pushed: str = ""
    query: str = ""
    normalized: str = ""
    selected: int = 0
    editing: bool = False
    label: str = "original"
    saves: int = 0
    cancels: int = 0
    changes: int = 0

    @property
    def child_label(self) -> str:
        return str(CHILD_COUNTER["n"])

    @property
    def add_by_push(self) -> JS:
        return JS().push("add_pushed")

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
        self.server_set_rendered = self.server_set

    async def type_slowly(self, racing: str = "", **_rest):
        await asyncio.sleep(0.4)
        self.typed_slowly = racing

    async def add_after(self, racing: str = "", **_rest):
        # Slower than the typing handler, so the test can look at the field
        # between the two answers. Playwright polls with growing intervals
        # (0, 100, 250, 500 ms), so the window has to outlast that.
        await asyncio.sleep(1.5)
        self.added_after = racing

    async def bump_child(self, **_rest):
        CHILD_COUNTER["n"] += 1

    async def add_pushed(self, pushing: str = "", **_rest):
        self.pushed = pushing

    async def search(self, query: str = "", **_rest):
        self.query = query

    async def navigate(self, **_rest):
        self.selected += 1

    async def normalize(self, normalizing: str = "", **_rest):
        # The server's answer is a different value than was sent (upper case).
        await asyncio.sleep(0.8)
        self.normalized = normalizing.upper()

    # Tutorial 03's inline editor: Escape cancels and leaving the field saves.
    # The cancel's render removes the focused field, which blurs it; that blur
    # is the morph's, not the user's, and must not save (found with tutorial 03).
    async def start_edit(self, **_rest):
        self.editing = True

    async def save_edit(self, edit: str = "", **_rest):
        self.saves += 1
        self.label = edit
        self.editing = False

    async def cancel_edit(self, **_rest):
        self.cancels += 1
        self.editing = False

    # A field typed in is sent change before its blur, by the user's leaving and
    # by the cancel's render alike; only the first is the user's.
    async def edit_changed(self, **_rest):
        self.changes += 1
