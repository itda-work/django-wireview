"""Plain POST forms inside components, each with ``{% csrf_token %}`` (#206).

tests/test_csrf_token_e2e.py submits them in a browser to a view that checks
the token, after each kind of render that draws them: the join, an event, a
notification, a LiveComponent's and a function component's. None of the forms
is intercepted: they post as a page's form would.
"""

from wireview import Component, LiveComponent, abroadcast, function_component

TOPIC = "csrfprobe"
#: What the notes list shows: data outside any component, as a table would be
NOTES: list[str] = []


class CsrfNotes(Component):
    class Meta:
        template_name = "csrfprobe/notes.html"
        subscriptions = {TOPIC}

    shown: bool = False

    @property
    def notes(self) -> list[str]:
        return list(NOTES)

    async def show(self):
        self.shown = True

    async def notification(self, channel: str, **kwargs):
        pass


class CsrfChild(LiveComponent):
    class Meta:
        template_name = "csrfprobe/child.html"


class CsrfPublisher(Component):
    class Meta:
        template_name = "csrfprobe/publisher.html"

    async def publish(self, text: str = "note"):
        NOTES.append(text)
        await abroadcast(TOPIC)


@function_component(template="csrfprobe/form.html")
def csrf_form(name: str = ""):
    return {"name": name}
