# Lazy imports to avoid Django AppRegistryNotReady errors
# Import these at module level only after Django is configured
from __future__ import annotations

import typing as t

# Type hints for lazy imports (helps IDE and type checkers)
if t.TYPE_CHECKING:
    from .async_result import AsyncResult as AsyncResult
    from .async_result import AsyncState as AsyncState
    from .core.component import Component as Component
    from .core.component import ComponentNotFound as ComponentNotFound
    from .core.component import abroadcast as abroadcast
    from .core.component import broadcast as broadcast
    from .core.meta import WireviewMeta as WireviewMeta
    from .core.session import SessionView as SessionView
    from .features.uploads import ExternalUploadMeta as ExternalUploadMeta
    from .function_component import FunctionComponent as FunctionComponent
    from .function_component import function_component as function_component
    from .js import JS as JS
    from .live_component import LiveComponent as LiveComponent
    from .testing import ComponentTestCase as ComponentTestCase
    from .testing import MountedComponent as MountedComponent
    from .testing import mount as mount


def __getattr__(name: str) -> t.Any:
    """Lazy import to avoid circular import issues with Django."""
    if name == "Component":
        from .core.component import Component

        return Component
    if name == "ComponentNotFound":
        from .core.component import ComponentNotFound

        return ComponentNotFound
    if name == "broadcast":
        from .core.component import broadcast

        return broadcast
    if name == "abroadcast":
        from .core.component import abroadcast

        return abroadcast
    if name == "WireviewMeta":
        from .core.meta import WireviewMeta

        return WireviewMeta
    if name == "SessionView":
        from .core.session import SessionView

        return SessionView
    if name == "JS":
        from .js import JS

        return JS
    # Testing utilities
    if name == "mount":
        from .testing import mount

        return mount
    if name == "MountedComponent":
        from .testing import MountedComponent

        return MountedComponent
    if name == "ComponentTestCase":
        from .testing import ComponentTestCase

        return ComponentTestCase
    # Async utilities
    if name == "AsyncResult":
        from .async_result import AsyncResult

        return AsyncResult
    if name == "AsyncState":
        from .async_result import AsyncState

        return AsyncState
    # Function components
    if name == "function_component":
        from .function_component import function_component

        return function_component
    if name == "FunctionComponent":
        from .function_component import FunctionComponent

        return FunctionComponent
    # Live components
    if name == "LiveComponent":
        from .live_component import LiveComponent

        return LiveComponent
    # Upload utilities
    if name == "ExternalUploadMeta":
        from .features.uploads import ExternalUploadMeta

        return ExternalUploadMeta
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = (
    "Component",
    "ComponentNotFound",
    "JS",
    "WireviewMeta",
    "SessionView",
    "broadcast",
    "abroadcast",
    # Function components
    "function_component",
    "FunctionComponent",
    # Live components
    "LiveComponent",
    # Upload utilities
    "ExternalUploadMeta",
    # Testing utilities
    "mount",
    "MountedComponent",
    "ComponentTestCase",
    # Async utilities
    "AsyncResult",
    "AsyncState",
)
