"""Components for the live_session E2E pages."""

import typing as t

from wireview import Component


class LsStaffPanel(Component):
    """Only ever mountable inside the staff boundary."""

    _template_name = "livesession/staff-panel.html"
    _live_sessions: t.ClassVar[set[str]] = {"ls-staff"}

    secret: str = "staff-only-payload"

    async def bump(self) -> None:
        self.secret = f"{self.secret}!"

    async def leave_the_boundary(self) -> None:
        """Server-driven navigation out of the session, the `push` command path."""
        await self.wire.push_to("/livesession/public/")


class LsPublicNote(Component):
    """Mountable anywhere: the page outside every boundary."""

    _template_name = "livesession/public-note.html"

    note: str = "public"

    async def bump(self) -> None:
        self.note = f"{self.note}!"
