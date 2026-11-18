"""What a user's click, change, input and submit send: the browser's answer and the test helpers' (GAP-056, #252).

``tests/test_dom_events.py`` does each action in ``SCENARIOS`` with the helpers
on a mounted component, ``tests/test_dom_events_e2e.py`` does it in a browser on
the page, and both compare what the handlers heard with the same expected list.
Every handler records its class, its name and every argument it got in
``HEARD`` -- the fields the client serialized included -- so a difference in
delegation, ownership or serialization shows as a different entry.
"""

from __future__ import annotations

import asyncio
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


class DomHooked(_Recording, Component):
    """A hook's pushEvent: what handle_hook_event hears, and the reply the hook gets."""

    class Meta:
        template_name = "domprobe/hooked.html"

    heard: str = ""

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict[str, t.Any]) -> t.Any:
        owner, hook, _ = hook_id.split(":", 2)
        self._heard(event, {"hook": hook, **payload})
        return {"event": event, "doubled": payload.get("x", 0) * 2, "owner_is_me": owner == self.id}


class DomHookLeaf(Component):
    class Meta:
        template_name = "domprobe/hook_leaf.html"

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict[str, t.Any]) -> t.Any:
        HEARD.append(("DomHookLeaf", event, {"hook_id": hook_id, **payload}))
        return {"leaf": True}


class DomStreamHost(Component):
    """Stream containers for ``tests/test_dom_events_e2e.py`` to put operations in: two named alike, one in a
    nested component, one that is the nested component's alone, and one that starts with an item of its own."""

    class Meta:
        template_name = "domprobe/stream_host.html"


class DomStreamGuest(Component):
    class Meta:
        template_name = "domprobe/stream_guest.html"


class DomTable(_Recording, Component):
    """Table rows streamed into a ``<tbody wire-stream>``: the client parsed each one as a body's content,
    where a ``<tr>`` outside a table is dropped, and the table stayed empty."""

    class Meta:
        template_name = "domprobe/table.html"

    heard: str = ""

    async def joined(self) -> None:
        await self.stream(
            "people",
            [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}],
            template="domprobe/table_item.html",
            dom_id=lambda row: f"person-{row['id']}",
        )

    async def remove(self, id: int) -> None:
        self._heard("remove", {"id": id})
        await self.stream_delete("people", f"person-{id}")


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


class DomMeta(_Recording, Component):
    """What an event says beyond its fields: the field it came from, the unused ones, the unreadable ones (#244).

    ``typed`` and ``saved`` name ``_target``, ``_unused`` and ``_bad_input`` and
    record them; ``changed`` takes the form as ``**data`` and records it, so a
    handler that does not name them is seen not to get them.
    """

    class Meta:
        template_name = "domprobe/meta.html"

    heard: str = ""

    async def typed(
        self, _target: str | None = None, _unused: list[str] = [], _bad_input: list[str] = [], age: str = ""
    ) -> None:
        self._heard("typed", {"target": _target, "unused": _unused, "bad": _bad_input, "age": age})

    async def changed(self, **data: t.Any) -> None:
        self._heard("changed", data)

    async def saved(self, _target: str | None = None, _unused: list[str] = [], **data: t.Any) -> None:
        self._heard("saved", {"target": _target, "unused": _unused, **data})


class DomPayload(_Recording, Component):
    """What a form's submit sends, form by form: the fields a browser's submission would (#244).

    ``testproj.domprobe.scenarios.PAYLOADS`` lists each form, the button that
    submits it and what goes; ``tests/test_dom_events.py`` holds the helpers to
    it and ``tests/test_dom_events_e2e.py`` the client and the browser's own
    ``FormData``.
    """

    class Meta:
        template_name = "domprobe/payload.html"

    heard: str = ""

    async def got(self, **kwargs: t.Any) -> None:
        self._heard("got", kwargs)


# ----------------------------------------------------------------------------
# A held page (GAP-056, #252): a parent, the component it draws and its
# LiveComponent, each keeping its own state from one render to the next
# ----------------------------------------------------------------------------


class DomFamily(_Recording, Component):
    """The parent: passes its title to both, shows and hides the LiveComponent, hears what it tells."""

    class Meta:
        template_name = "domprobe/family.html"

    heard: str = ""
    title: str = "a"
    shown: bool = True
    told: int = 0

    async def rename(self, **kwargs: t.Any) -> None:
        self._heard("rename", {})
        self.title += "b"

    async def toggle(self, **kwargs: t.Any) -> None:
        self._heard("toggle", {})
        self.shown = not self.shown

    async def told_me(self, n: int) -> None:
        self._heard("told_me", {"n": n})
        self.told = n


class DomKid(_Recording, Component):
    """A nested component: the label its drawer passes, a count of its own."""

    class Meta:
        template_name = "domprobe/kid.html"

    heard: str = ""
    label: str = ""
    count: int = 0

    async def joined(self) -> None:
        self._heard("joined", {})

    async def leaving(self) -> None:
        self._heard("leaving", {})

    async def bump(self, **kwargs: t.Any) -> None:
        self._heard("bump", {})
        self.count += 1


class DomPet(_Recording, LiveComponent):
    """A LiveComponent its parent owns: joined, update and leaving are recorded."""

    class Meta:
        template_name = "domprobe/pet.html"

    heard: str = ""
    title: str = ""
    pets: int = 0

    async def joined(self) -> None:
        self._heard("joined", {})

    async def update(self, **assigns: t.Any) -> None:
        self._heard("update", dict(assigns))
        await super().update(**assigns)

    async def leaving(self) -> None:
        self._heard("leaving", {})

    async def pet(self, **kwargs: t.Any) -> None:
        self._heard("pet", {})
        self.pets += 1

    async def tell(self, **kwargs: t.Any) -> None:
        self._heard("tell", {})
        await self.send_to_parent("told_me", n=self.pets)


class DomHttp(Component):
    """Forms sent to a view over HTTP: ``submit_form()`` and a browser's own submission (#252)."""

    class Meta:
        template_name = "domprobe/http.html"


CANCELLED: list[str] = []


class _Busy:
    @property
    def is_live_component(self) -> bool:
        return isinstance(self, LiveComponent)

    def get_subscriptions(self) -> set[str]:
        return {f"held-{self.id}"}

    async def joined(self) -> None:
        await self.start_async("wait", self._wait())  # type: ignore[attr-defined]

    async def _wait(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            CANCELLED.append(self.id)  # type: ignore[attr-defined]
            raise

    async def run(self, **kwargs: t.Any) -> None:
        self.runs += 1  # type: ignore[attr-defined]

    async def leaving(self) -> None:
        HEARD.append((type(self).__name__, "leaving", {}))

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        HEARD.append((type(self).__name__, "notification", kwargs))


class HeldBusyPet(_Busy, LiveComponent):
    class Meta:
        template_name = "domprobe/busy.html"

    runs: int = 0


class HeldBusyKid(_Busy, Component):
    class Meta:
        template_name = "domprobe/busy.html"

    runs: int = 0


class HeldShelter(Component):
    class Meta:
        template_name = "domprobe/shelter.html"

    shown: bool = True

    async def toggle(self) -> None:
        self.shown = not self.shown

    async def redraw(self) -> None:
        pass


class HaltNested:
    @staticmethod
    async def on_mount(component, params, session):
        return {"halt": True}


class _Guarded:
    @property
    def is_live_component(self) -> bool:
        return isinstance(self, LiveComponent)

    async def hit(self, **kwargs: t.Any) -> None:
        HEARD.append((type(self).__name__, "hit", {}))


class HeldRefused(_Guarded, Component):
    class Meta:
        template_name = "domprobe/guarded.html"
        live_sessions = {"ip-inside"}
        on_mount = [HaltNested]


class HeldRefusedPet(_Guarded, LiveComponent):
    class Meta:
        template_name = "domprobe/guarded.html"
        live_sessions = {"ip-inside"}
        on_mount = [HaltNested]


class HeldAllowed(_Guarded, Component):
    class Meta:
        template_name = "domprobe/guarded.html"


class HeldGuard(_Guarded, Component):
    class Meta:
        template_name = "domprobe/guard.html"


JOINED_ROWS: list[str] = []


class HeldRow(LiveComponent):
    class Meta:
        template_name = "domprobe/row.html"

    n: str = ""

    async def joined(self) -> None:
        JOINED_ROWS.append(self.id)


class HeldRows(Component):
    class Meta:
        template_name = "domprobe/rows.html"

    rows: list[str] = []


class _Deep:
    @property
    def is_live_component(self) -> bool:
        return isinstance(self, LiveComponent)

    @property
    def next_id(self) -> str:
        return f"deep-{self.level + 1}"  # type: ignore[attr-defined]

    @property
    def next_level(self) -> int:
        return self.level + 1  # type: ignore[attr-defined]


class HeldDeep(_Deep, LiveComponent):
    class Meta:
        template_name = "domprobe/deep.html"

    level: int = 0
    until: int = 0


class HeldDeepRoot(_Deep, Component):
    class Meta:
        template_name = "domprobe/deep.html"

    level: int = 0
    until: int = 0
