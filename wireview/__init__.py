# Lazy imports to avoid Django AppRegistryNotReady errors
# Import these at module level only after Django is configured


def __getattr__(name: str):
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
    if name == "WireviewMeta":
        from .core.meta import WireviewMeta

        return WireviewMeta
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = (
    "Component",
    "ComponentNotFound",
    "JS",
    "WireviewMeta",
    "broadcast",
    # Testing utilities
    "mount",
    "MountedComponent",
    "ComponentTestCase",
    # Async utilities
    "AsyncResult",
    "AsyncState",
)
