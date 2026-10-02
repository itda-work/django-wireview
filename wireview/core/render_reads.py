"""Which parts of a render read nothing but a temporary assign that was reset (#111).

``Meta.temporary_assigns`` fields go back to their default after each render.
Until a handler assigns them again they are *stale*: the page still shows what
the last render drew from them, and a render that reads the default would
erase it. Phoenix does not count the reset as a change, so the parts that
depend on it are not sent again. The diff here compares rendered values, so it
has to be told which parts those are.

A ``RenderReads`` is active on the rendering thread for one render of one
component. It sorts the component's names in two: *stale* (a stale field, or a
property computed from nothing but stale fields) and *other* (every other field
and property). Reads reach it two ways:

- by name from the template context (``messages``), through ``TrackingContext``;
- through the component (``this.messages``, or a property that reads it),
  through a ``__getattribute__`` installed only on classes that declare
  temporary assigns, so no other component pays for it.

The template engine opens a slot for each dynamic part it renders, and a read
marks the innermost one. A part that read something stale and nothing else
keeps its previous value: the parser turns it into ``Stale`` and the diff
settles it. A part that also read another name renders from what it has now --
the reset value included -- since that other name may be the change that has
to go out.

A component drawn in another component's pass -- a nested ``{% component %}``
the host draws again, or one in a slot its owner draws -- tracks its own reads
there with a ``RenderReads`` of its own, numbered in that pass; the pass's
tracking resumes after it. Its stale parts take back what its own last render
drew (``rendered.keep_stale``): the other component's diff puts the drawing on
the page.

Two kinds of part need more than the reads of this render:

- an ``{% if %}`` block whose condition is stale did not render the branch that
  would read the rest, so the names in all its branches are found statically
  (``template_engine.referenced_names``), an included template's among them. An
  ``{% include %}`` is a block of its own for the same reason;
- a loop is compared item by item, so a part inside one cannot keep its value
  on its own. The loop decides: over a stale list it keeps its items, like
  Phoenix's ``phx-update="append"`` keeps the DOM.

What a kept part drew from elsewhere is not for this component's names to
decide: a nested component's drawing carries that component's state, and a
slot is the filler's. Each render of its own records what each part drew of
them, and a part is drawn again when one of those moved on since
(``template_engine._PartNode``). A LiveComponent in a kept part is only named
there, so it stays: the lifecycle counts the ones the render shows, not only
those its template named (``ComponentRepository.take_lifecycle``), and another
component's pass keeps it named (``rendered.keep_stale``). A fill that read a
stale name keeps what it drew last in that part
(``SlotContainer.keeping_stale``), as the enclosing render keeps it on the page.
"""

from __future__ import annotations

import threading
import typing as t

from django.template import Context

_local = threading.local()


class _Slot:
    __slots__ = ("index", "stale", "other")

    def __init__(self, index: int | None) -> None:
        self.index = index
        self.stale = False
        self.other = False


class RenderReads:
    """The stale names of one component's render, and the dynamic parts that read only them."""

    def __init__(self, component_id: int, stale: t.Iterable[str], other: t.Iterable[str]) -> None:
        self.component_id = component_id
        self.stale = set(stale)
        self.other = set(other) - self.stale
        #: Marker indices of the parts that read a stale name and nothing else
        self.slots: set[int] = set()
        self._open: list[_Slot] = []
        self._outer: RenderReads | None = None

    def saw(self, name: str) -> None:
        """The innermost open slot read ``name``."""
        if not self._open:
            return
        if name in self.stale:
            self._open[-1].stale = True
        elif name in self.other:
            self._open[-1].other = True

    def saw_attribute(self, owner: object, name: str) -> None:
        if id(owner) == self.component_id:
            self.saw(name)

    def open(self, index: int | None = None) -> _Slot:
        slot = _Slot(index)
        self._open.append(slot)
        return slot

    def close(self, slot: _Slot, index: int | None = None) -> _Slot:
        """Close ``slot``; ``index`` names it when it was not known on opening."""
        self._open.pop()
        if index is not None:
            slot.index = index
        if slot.stale and not slot.other and slot.index is not None:
            self.slots.add(slot.index)
        return slot

    def __enter__(self) -> RenderReads:
        # A component drawn in another's pass tracks its own reads inside the
        # other's, which resumes afterwards
        self._outer = active()
        _local.active = self
        return self

    def __exit__(self, *exc: object) -> None:
        _local.active, self._outer = self._outer, None


def active() -> RenderReads | None:
    """The reads being tracked on this thread, if a render is tracking any."""
    return getattr(_local, "active", None)


class TrackingContext(Context):
    """A template context that reports the names a template resolves."""

    def __getitem__(self, key: t.Any) -> t.Any:
        if (reads := active()) is not None and isinstance(key, str):
            reads.saw(key)
        return super().__getitem__(key)


def install(cls: type) -> None:
    """Report attribute reads on ``cls``'s instances while a render tracks them.

    Only classes that declare temporary assigns: nothing else can be stale.
    """
    if not cls._meta.temporary_assigns:  # type: ignore[attr-defined]
        return
    base = next(k.__dict__["__getattribute__"] for k in cls.__mro__[1:] if "__getattribute__" in k.__dict__)
    if getattr(base, "_wireview_tracks", False):
        return  # a base already reports

    def __getattribute__(self: t.Any, name: str) -> t.Any:
        if (reads := active()) is not None:
            reads.saw_attribute(self, name)
        return base(self, name)

    __getattribute__._wireview_tracks = True  # type: ignore[attr-defined]
    cls.__getattribute__ = __getattribute__  # type: ignore[method-assign]
