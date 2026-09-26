"""Components for the live_session E2E pages."""

from wireview import Component


class LsStaffPanel(Component):
    """Only ever mountable inside the staff boundary."""

    class Meta:
        template_name = "livesession/staff-panel.html"
        live_sessions = {"ls-staff"}

    secret: str = "staff-only-payload"

    async def bump(self) -> None:
        self.secret = f"{self.secret}!"

    async def leave_the_boundary(self) -> None:
        """Server-driven navigation out of the session, the `push` command path."""
        await self.wire.push_to("/livesession/public/")


class LsPublicNote(Component):
    """Mountable anywhere: the page outside every boundary."""

    class Meta:
        template_name = "livesession/public-note.html"

    note: str = "public"

    async def bump(self) -> None:
        self.note = f"{self.note}!"
