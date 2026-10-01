"""Deprecated: import from ``wireview`` instead.

``from wireview.component import Component`` predates the package's public
namespace. It keeps working through 1.x with a
:class:`~wireview.WireviewDeprecationWarning` and is removed in 2.0 (#98).
Two of its names, ``ComponentNotFound`` and ``MessagePayload``, were never
public and go with it.
"""

from .core.component import Component, ComponentNotFound, MessagePayload, broadcast
from .core.meta import WireviewMeta
from .deprecation import warn_deprecated

warn_deprecated(
    "the wireview.component module",
    "`Component`, `WireviewMeta` and `broadcast` from `wireview`"
    " (`ComponentNotFound` and `MessagePayload` are internal and have no replacement)",
    stacklevel=2,
)

__all__ = ("Component", "ComponentNotFound", "MessagePayload", "WireviewMeta", "broadcast")
