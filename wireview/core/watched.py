"""Stand-ins for names a render must not read because they differ from viewer to viewer.

Two renders are made once and shown to many viewers, and both must read
nothing of the viewer:

- a ``Meta.shared_render`` component's render, shared between the connections
  handling one broadcast (``core/shared_render.py``, #176), watched while
  ``VERIFY_SHARED_RENDER`` says so;
- a ``Broadcast`` stream item, rendered once by whoever publishes it
  (``core/patches.py``, #178), watched always.

Each puts a ``Watched`` where such a name would be. Any use of it -- an
attribute, ``str()``, a truth test, a comparison, iteration -- raises the
error its owner gave, naming the variable. The error is not one a template
swallows: it has no ``silent_variable_failure``, and it is none of the
exception types Django's variable lookup catches.
"""

from __future__ import annotations

import typing as t


class Watched:
    """Stands in for a name a render must not read. Any use raises ``error(message)``."""

    __slots__ = ("_name", "_error", "_message", "_stands_for")

    def __init__(self, name: str, error: type[Exception], message: str, *, stands_for: type | None = None) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_error", error)
        object.__setattr__(self, "_message", message)
        object.__setattr__(self, "_stands_for", stands_for)

    def _raise(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        error = object.__getattribute__(self, "_error")
        raise error(object.__getattribute__(self, "_message"))

    def __getattr__(self, attr: str) -> t.Any:
        self._raise()

    __getitem__ = __str__ = __bool__ = __iter__ = __len__ = __eq__ = __html__ = __contains__ = _raise  # type: ignore[assignment]
    __hash__ = None  # type: ignore[assignment]


def name_of(value: t.Any) -> str | None:
    """The name ``value`` stands in for, or ``None`` when it is not a ``Watched``."""
    if type(value) is Watched:
        return object.__getattribute__(value, "_name")
    return None


def stands_for(value: t.Any) -> type | None:
    """The component class a watched ``this`` stands for, or ``None``.

    A ``Broadcast`` item is rendered for every instance of one class, so a tag
    that only needs the class -- ``{% on %}`` checking that a handler exists --
    can ask it here instead of reading the instance it cannot have.
    """
    if type(value) is Watched:
        return object.__getattribute__(value, "_stands_for")
    return None
