"""django-wireview's public API.

Everything a project imports comes from here: ``from wireview import
Component``. The submodules are implementation, free to move between minor
releases; only the names in ``__all__`` are covered by the compatibility
policy (docs/COMPATIBILITY.md, #98). tests/test_public_api.py keeps
``__all__``, this table and the documentation in step.

The names load lazily: importing ``wireview`` must not import Django models
before the app registry is ready.
"""

from __future__ import annotations

import importlib
import typing as t

# Type hints for lazy imports (helps IDE and type checkers)
if t.TYPE_CHECKING:
    from . import telemetry as telemetry
    from .async_result import AsyncResult as AsyncResult
    from .async_result import AsyncState as AsyncState
    from .checks import iter_exposed_handlers as iter_exposed_handlers
    from .core.component import Component as Component
    from .core.component import abroadcast as abroadcast
    from .core.component import broadcast as broadcast
    from .core.live_session import LiveSession as LiveSession
    from .core.live_session import LiveSessionContext as LiveSessionContext
    from .core.live_session import invalidate_authentication as invalidate_authentication
    from .core.live_session import live_session as live_session
    from .core.meta import WireviewMeta as WireviewMeta
    from .core.session import SessionView as SessionView
    from .deprecation import WireviewDeprecationWarning as WireviewDeprecationWarning
    from .features.presence import PresenceConfig as PresenceConfig
    from .features.presence import PresenceMixin as PresenceMixin
    from .features.presence import PresenceState as PresenceState
    from .features.presence import PresenceTrackerMixin as PresenceTrackerMixin
    from .features.presence import PresenceUser as PresenceUser
    from .features.toasts import atoast as atoast
    from .features.toasts import toast as toast
    from .features.toasts import toast_channel as toast_channel
    from .features.uploads import ConsumedUpload as ConsumedUpload
    from .features.uploads import ExternalUploadMeta as ExternalUploadMeta
    from .features.uploads import UploadConfig as UploadConfig
    from .features.uploads import UploadEntry as UploadEntry
    from .function_components import FunctionComponent as FunctionComponent
    from .function_components import function_component as function_component
    from .function_components import get_function_component as get_function_component
    from .js import JS as JS
    from .live_component import LiveComponent as LiveComponent
    from .schemas import AutoBroadcast as AutoBroadcast
    from .schemas import ModelAction as ModelAction
    from .testing import ComponentTestCase as ComponentTestCase
    from .testing import MountedComponent as MountedComponent
    from .testing import Navigation as Navigation
    from .testing import mount as mount

#: Public name -> the module that defines it. A name that is its module's own
#: name exports the module: ``telemetry`` is a namespace of signals.
_EXPORTS: dict[str, str] = {
    # Components
    "Component": ".core.component",
    "LiveComponent": ".live_component",
    "function_component": ".function_components",
    "FunctionComponent": ".function_components",
    "get_function_component": ".function_components",
    "WireviewMeta": ".core.meta",
    "SessionView": ".core.session",
    "JS": ".js",
    # Page boundaries
    "live_session": ".core.live_session",
    "LiveSession": ".core.live_session",
    "LiveSessionContext": ".core.live_session",
    "invalidate_authentication": ".core.live_session",
    # Broadcasts and notifications
    "broadcast": ".core.component",
    "abroadcast": ".core.component",
    "toast": ".features.toasts",
    "atoast": ".features.toasts",
    "toast_channel": ".features.toasts",
    "AutoBroadcast": ".schemas",
    "ModelAction": ".schemas",
    # Presence
    "PresenceMixin": ".features.presence",
    "PresenceTrackerMixin": ".features.presence",
    "PresenceConfig": ".features.presence",
    "PresenceUser": ".features.presence",
    "PresenceState": ".features.presence",
    # Uploads
    "UploadConfig": ".features.uploads",
    "UploadEntry": ".features.uploads",
    "ConsumedUpload": ".features.uploads",
    "ExternalUploadMeta": ".features.uploads",
    # Async
    "AsyncResult": ".async_result",
    "AsyncState": ".async_result",
    # Testing
    "mount": ".testing",
    "MountedComponent": ".testing",
    "Navigation": ".testing",
    "ComponentTestCase": ".testing",
    # Tooling
    "iter_exposed_handlers": ".checks",
    "telemetry": ".telemetry",
    "WireviewDeprecationWarning": ".deprecation",
}

# Written out, not computed, so static checkers see it. tests/test_public_api.py
# checks it against _EXPORTS.
__all__ = (
    "Component",
    "LiveComponent",
    "function_component",
    "FunctionComponent",
    "get_function_component",
    "WireviewMeta",
    "SessionView",
    "JS",
    "live_session",
    "LiveSession",
    "LiveSessionContext",
    "invalidate_authentication",
    "broadcast",
    "abroadcast",
    "toast",
    "atoast",
    "toast_channel",
    "AutoBroadcast",
    "ModelAction",
    "PresenceMixin",
    "PresenceTrackerMixin",
    "PresenceConfig",
    "PresenceUser",
    "PresenceState",
    "UploadConfig",
    "UploadEntry",
    "ConsumedUpload",
    "ExternalUploadMeta",
    "AsyncResult",
    "AsyncState",
    "mount",
    "MountedComponent",
    "Navigation",
    "ComponentTestCase",
    "iter_exposed_handlers",
    "telemetry",
    "WireviewDeprecationWarning",
)


def __getattr__(name: str) -> t.Any:
    """Load a public name on first use (see the module docstring)."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    loaded = importlib.import_module(module, __name__)
    value = loaded if module == f".{name}" else getattr(loaded, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *__all__])
