"""Forms a new instance gets back (#245, GAP-049).

tests/test_form_recovery_e2e.py drives it. Each ``RecoverBox`` has:

- ``draft``: recovered through the handler ``keep``. ``LAYOUT`` -- which the test
  changes while the page is down or before an error -- says whether the next
  render draws it, under which id, and with which ``wire-auto-recover``: a form
  the new render dropped, renamed or turned off. It holds an upload input and a
  plain file input: neither is recovered.
- ``city``: a bare ``wire-auto-recover``, which replays the form's change.
- ``off``: ``wire-auto-recover="false"``, never recovered.
- ``ignore``: a handler named ``ignore`` -- a name, as it always was.
- ``explode`` (``LAYOUT["explode"]``): a recovery that raises.

``tag`` is the instance's, made in ``joined()`` and not signed, so every new
instance has another. ``HEARD`` is what reached which instance, in order.
``RecoverLeaf`` is a LiveComponent with its own recovered form: its root's
join builds it again.
"""

import uuid

from wireview import Component, LiveComponent

#: What the next render draws of the draft form
LAYOUT: dict = {}

#: (component id, instance tag, handler, what it got, who the instance is for)
HEARD: list[tuple[str, str, str, object, str]] = []


def reset() -> None:
    LAYOUT.clear()
    LAYOUT.update(shown=True, form_id="draft", recover="keep", explode=False)
    HEARD.clear()


reset()


class _Heard:
    def _heard(self, handler: str, got: object) -> None:
        user = getattr(self, "user", None)
        HEARD.append((self.id, self.tag, handler, got, getattr(user, "username", "") or ""))  # type: ignore[attr-defined]


class RecoverBox(_Heard, Component):
    class Meta:
        template_name = "recoverprobe/box.html"
        exclude_fields = ["tag"]

    count: int = 0
    kept: str = ""
    city: str = ""
    ignored: str = ""
    tag: str = ""

    async def joined(self):
        self.tag = uuid.uuid4().hex[:8]
        self.allow_upload("photo", accept=[".txt"], max_entries=1, auto_upload=False)

    @property
    def layout(self) -> dict:
        return LAYOUT

    async def bump(self, **_rest):
        self.count += 1

    async def boom(self, **_rest):
        self.count += 100
        raise RuntimeError("boom")

    async def keep(self, form_data: dict | None = None, **_rest):
        self._heard("keep", sorted((form_data or {}).items()))
        self.kept = (form_data or {}).get("draft", "")

    async def set_city(self, city: str = "", **_rest):
        self._heard("set_city", city)
        self.city = city

    async def set_off(self, off: str = "", **_rest):
        self._heard("set_off", off)

    async def ignore(self, form_data: dict | None = None, **_rest):
        self._heard("ignore", (form_data or {}).get("note", ""))
        self.ignored = (form_data or {}).get("note", "")

    async def explode(self, form_data: dict | None = None, **_rest):
        self._heard("explode", (form_data or {}).get("fuse", ""))
        raise RuntimeError("the recovery raised")


class RecoverLeaf(_Heard, LiveComponent):
    class Meta:
        template_name = "recoverprobe/leaf.html"
        exclude_fields = ["tag"]

    kept: str = ""
    tag: str = ""

    async def joined(self):
        self.tag = uuid.uuid4().hex[:8]

    async def keep(self, form_data: dict | None = None, **_rest):
        self._heard("keep", sorted((form_data or {}).items()))
        self.kept = (form_data or {}).get("draft", "")
