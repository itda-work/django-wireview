"""A render context whose properties are read when the template reads them (#187).

A component that declares ``Meta.lazy_properties`` renders from a
``LazyContext``. Its fields and async properties are read up front as ever;
its sync properties (``property``, ``cached_property``) are read the first
time the template looks their name up, once a render. A property the template
never names is not run, and neither is its SQL.

Django hands a dict given without a request to the template as it is
(``make_context``), so the lookups reach this one. Whatever iterates it --
``Context.flatten()``, ``dict(context)`` -- reads every pending name first:
the result is right, only the saving is lost.

Django's variable lookup swallows some exceptions (``AttributeError`` among
them, and any under an ``{% if %}`` operator), and a property read up front
raised instead. So the first failure is kept (``failure``) and the render
raises it once the template is done (``WireviewMeta._render_lazily``).
"""

from __future__ import annotations

import functools
import inspect
import typing as t

__all__ = ("LazyContext", "PropertyFailed", "is_lazy")


class PropertyFailed(Exception):
    """A property raised while the template read it; ``__cause__`` is what it raised."""


#: A property that computed to a callable: absent, as the eager read leaves it out
_ABSENT = object()


def is_lazy(component: t.Any, name: str) -> bool:
    """Whether ``component``'s ``name`` is a sync property not yet computed on the instance."""
    if name in component.__dict__:
        return False
    static = inspect.getattr_static(type(component), name, None)
    if isinstance(static, functools.cached_property):
        return True
    return isinstance(static, property) and not inspect.iscoroutinefunction(static.fget)


class LazyContext(dict):
    """The values read up front, and the ``pending`` names read on their first lookup.

    A pending name wins over a value of the same name read up front: the
    component's own property over a name the render adds around it.
    """

    def __init__(self, eager: t.Mapping[str, t.Any], component: t.Any, pending: t.Iterable[str]) -> None:
        super().__init__(eager)
        self._component = component
        self._pending = set(pending)
        for name in self._pending:
            dict.pop(self, name, None)
        #: The first exception a property raised while the template read it
        self.failure: BaseException | None = None

    def _compute(self, attr_name: str) -> t.Any:
        # render_queries reads the property's name and owner off this frame
        component = self._component
        self._pending.discard(attr_name)
        try:
            value = getattr(component, attr_name)
        except Exception as error:
            if self.failure is None:
                self.failure = error
            raise PropertyFailed(f"{type(component).__name__}.{attr_name}") from error
        if callable(value):
            return _ABSENT
        dict.__setitem__(self, attr_name, value)
        return value

    def __missing__(self, key: str) -> t.Any:
        if key in self._pending and (value := self._compute(key)) is not _ABSENT:
            return value
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if dict.__contains__(self, key):
            return True
        if key not in self._pending:
            return False
        try:
            self[key]  # a property that computes to a callable is not there
        except KeyError:
            return False
        return True

    def get(self, key: str, default: t.Any = None) -> t.Any:  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    def stored(self) -> dict[str, t.Any]:
        """The values read so far, without reading the pending names."""
        return dict(dict.items(self))

    def __len__(self) -> int:
        # Not read for it: a truth test is not a lookup. A pending property that
        # computes to a callable is counted until it is read.
        return dict.__len__(self) + len(self._pending)

    def __iter__(self) -> t.Iterator[str]:
        self._force()
        return dict.__iter__(self)

    def keys(self):  # type: ignore[override]
        self._force()
        return dict.keys(self)

    def values(self):  # type: ignore[override]
        self._force()
        return dict.values(self)

    def items(self):  # type: ignore[override]
        self._force()
        return dict.items(self)

    def copy(self) -> dict[str, t.Any]:  # type: ignore[override]
        self._force()
        return dict(dict.items(self))

    def _force(self) -> None:
        for name in sorted(self._pending):
            if name in self._pending:
                try:
                    self[name]
                except KeyError:
                    pass
