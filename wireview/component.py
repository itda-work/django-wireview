"""
Backward-compatible module for wireview components.

This module re-exports from wireview.core for backward compatibility.
New code should import directly from wireview or wireview.core.
"""

from .core.component import Component, ComponentNotFound, MessagePayload, broadcast
from .core.meta import WireviewMeta

__all__ = ("Component", "ComponentNotFound", "MessagePayload", "WireviewMeta", "broadcast")
