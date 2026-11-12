"""What a user's click, change, input and submit send: the browser's answer and the test helpers' (GAP-056, #252).

``tests/test_dom_events.py`` does each action in ``SCENARIOS`` with the helpers
on a mounted component, ``tests/test_dom_events_e2e.py`` does it in a browser on
the page, and both compare what the handlers heard with the same expected list.
Every handler records its class, its name and every argument it got in
``HEARD`` -- the fields the client serialized included -- so a difference in
delegation, ownership or serialization shows as a different entry.
"""

from __future__ import annotations

import typing as t

from wireview import JS, Component, LiveComponent

#: ``(class name, handler, kwargs)`` of every handler that ran, in order
HEARD: list[tuple[str, str, dict[str, t.Any]]] = []


class _Recording:
    def _heard(self, handler: str, kwargs: dict[str, t.Any]) -> None:
        HEARD.append((type(self).__name__, handler, kwargs))
        # What the page shows, for a browser to wait on
        self.heard = f"{self.heard} {handler}".strip()  # type: ignore[attr-defined]


class DomBubble(_Recording, Component):
    """Delegation: bindings on the way up, .stop, a modifier that does not hold, a debounce, a JS() chain."""

    class Meta:
        template_name = "domprobe/bubble.html"

    heard: str = ""

    @property
    def mark(self) -> JS:
        return JS().add_class("[data-testid=js]", "marked")

    async def inner(self, **kwargs: t.Any) -> None:
        self._heard("inner", kwargs)

    async def wrapped(self, **kwargs: t.Any) -> None:
        self._heard("wrapped", kwargs)


class DomForm(_Recording, Component):
    """What a form's fields send: every kind of field, named buttons, a disabled field, fields outside the form.

    It keeps what a change tells it and draws the fields from that, as a form
    backed by its component does: the answer to a change sets the form's fields
    to what the render says (#92), and a form that drew its first values again
    would undo what the user did before a submit.
    """

    class Meta:
        template_name = "domprobe/form.html"

    heard: str = ""
    title: str = "t0"
    size: str = "m"
    tags: list[str] = ["a", "c"]
    agree: bool = False
    opts: list[str] = ["x"]
    plan: str = "free"

    async def saved(self, **kwargs: t.Any) -> None:
        self._heard("saved", kwargs)

    async def changed(self, **kwargs: t.Any) -> None:
        self._heard("changed", kwargs)
        self.title = kwargs.get("title", "")
        self.size = kwargs.get("size", "")
        self.tags = kwargs.get("tags", [])
        self.agree = "agree" in kwargs
        # One value of a name reaches a handler: the last checked
        self.opts = [kwargs["opts"]] if "opts" in kwargs else []
        self.plan = kwargs.get("plan", "")

    async def typed(self, **kwargs: t.Any) -> None:
        self._heard("typed", kwargs)

    async def clicked(self, **kwargs: t.Any) -> None:
        self._heard("clicked", kwargs)


class DomLeaf(_Recording, Component):
    """A component inside DomNest: its own field and binding."""

    class Meta:
        template_name = "domprobe/leaf.html"

    heard: str = ""

    async def leaf(self, **kwargs: t.Any) -> None:
        self._heard("leaf", kwargs)


class DomLive(_Recording, LiveComponent):
    """A LiveComponent inside DomNest, its binding aimed at itself (myself=True)."""

    class Meta:
        template_name = "domprobe/live.html"

    heard: str = ""

    async def bump(self, **kwargs: t.Any) -> None:
        self._heard("bump", kwargs)


class DomNest(_Recording, Component):
    """Ownership: a click in a nested component with no binding of its own reaches the parent's."""

    class Meta:
        template_name = "domprobe/nest.html"

    heard: str = ""

    async def wrapped(self, **kwargs: t.Any) -> None:
        self._heard("wrapped", kwargs)


class DomKeys(_Recording, Component):
    """Key and focus events: key conditions, a key code, Enter's implicit submission, focus that does not bubble."""

    class Meta:
        template_name = "domprobe/keys.html"

    heard: str = ""

    async def sent(self, **kwargs: t.Any) -> None:
        self._heard("sent", kwargs)

    async def cancelled(self, **kwargs: t.Any) -> None:
        self._heard("cancelled", kwargs)

    async def ctrl_sent(self, **kwargs: t.Any) -> None:
        self._heard("ctrl_sent", kwargs)

    async def letter(self, **kwargs: t.Any) -> None:
        self._heard("letter", kwargs)

    async def focused(self, **kwargs: t.Any) -> None:
        self._heard("focused", kwargs)

    async def blurred(self, **kwargs: t.Any) -> None:
        self._heard("blurred", kwargs)

    async def entered(self, **kwargs: t.Any) -> None:
        self._heard("entered", kwargs)

    async def left(self, **kwargs: t.Any) -> None:
        self._heard("left", kwargs)

    async def searched(self, **kwargs: t.Any) -> None:
        self._heard("searched", kwargs)

    async def submitted(self, **kwargs: t.Any) -> None:
        self._heard("submitted", kwargs)


class DomChat(_Recording, Component):
    """A chat input that sends on Enter: ``chat_unbound.html`` is the same input with the binding deleted."""

    class Meta:
        template_name = "domprobe/chat.html"

    heard: str = ""

    async def send(self, draft: str) -> None:
        self._heard("send", {"draft": draft})


class DomTrap(_Recording, Component):
    """The mistake the helpers catch and ``call()`` does not: a button that is not wired to ``remove``.

    ``domprobe/trap.html`` wires it. ``tests/test_dom_events.py`` points
    subclasses at ``trap_unbound.html`` (the binding deleted) and
    ``trap_miswired.html`` (the button calls another handler). A misspelt handler
    needs no fixture: ``{% on %}`` refuses it while the page renders, and the
    template diagnostics (``wireview_check_templates``) report it before that.
    """

    class Meta:
        template_name = "domprobe/trap.html"

    heard: str = ""

    async def remove(self, id: int) -> None:
        self._heard("remove", {"id": id})

    async def archive(self, id: int) -> None:
        self._heard("archive", {"id": id})
