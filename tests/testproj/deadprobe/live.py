from wireview import Component

#: The notes, kept by the process: what the view and the handler both add to
NOTES: list[str] = []


class DeadNotes(Component):
    """One form, two ways in: the handler with JavaScript, the view without (#73)."""

    class Meta:
        template_name = "deadprobe/notes.html"

    notes: list[str] = []
    shouted: bool = False

    async def add(self, text: str = ""):
        if text:
            NOTES.append(text)
            self.notes = list(NOTES)

    async def shout(self):
        self.shouted = True
