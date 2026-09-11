"""JavaScript hooks: the client half of a component.

A hook is what a component uses when the browser has to do something the server
cannot describe -- format a time in the visitor's own clock, drive a third-party
widget, keep a caret where it was across a re-render.

This example keeps the browser work small on purpose (a relative timestamp) and
spends the rest of the page showing **when each lifecycle callback runs**, since
that is the part a project gets wrong.
"""

from django.utils import timezone
from pydantic import Field

from wireview import Component


class XLifecycle(Component):
    """A timestamp the browser formats, and a counter the hook talks to the server about."""

    _template_name = "hooks/x-lifecycle.html"

    #: An ISO instant. The server decides *when*; the hook decides how it reads.
    stamp: str = Field(default_factory=lambda: timezone.now().isoformat())
    #: Whether the hooked element is on the page at all -- taking it away is what
    #: makes ``destroyed()`` run.
    visible: bool = True
    #: How many times the hook has told the server something.
    notes: int = 0

    async def touch(self) -> None:
        """Re-stamp. The element stays, its content changes: a morph, so beforeUpdate/updated."""
        self.stamp = timezone.now().isoformat()

    async def hide(self) -> None:
        """Take the hooked element off the page. destroyed() runs."""
        self.visible = False

    async def reveal(self) -> None:
        """Put it back. mounted() runs again, on a new hook instance."""
        self.visible = True

    async def highlight(self) -> None:
        """Push an event *to* the hooks of this component (``handleEvent`` on the other side)."""
        await self.push_event("highlight", {"text": "서버가 보냈다"})

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict):
        """Answer an event a hook pushed.

        Whatever this returns goes back to the callback the hook passed to
        ``pushEvent``. Returning ``None`` means "no reply", and the hook's
        callback never runs.
        """
        if event == "noted":
            self.notes += 1
            return {"count": self.notes}
        return None
