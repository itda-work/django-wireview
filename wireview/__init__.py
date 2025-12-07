# Lazy imports to avoid Django AppRegistryNotReady errors
# Import these at module level only after Django is configured


def __getattr__(name: str):
    """Lazy import to avoid circular import issues with Django."""
    if name == "Component":
        from .component import Component

        return Component
    if name == "broadcast":
        from .component import broadcast

        return broadcast
    if name == "JS":
        from .js import JS

        return JS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ("Component", "JS", "broadcast")
