"""Streams module for efficient large list handling.

This module provides Phoenix LiveView-style streams for memory-efficient
handling of large lists in wireview components.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field


@dataclass
class StreamItem:
    """Stream item wrapper with DOM ID and rendered HTML."""

    dom_id: str
    html: str


@dataclass
class StreamOp:
    """Stream operation to send to client.

    Attributes:
        op: Operation type - "reset", "insert", or "delete"
        stream: Stream name (matches wire-stream attribute in template)
        items: List of StreamItem objects
        at: Insert position (-1 = append, 0 = prepend, n = at index)
    """

    op: t.Literal["reset", "insert", "delete"]
    stream: str
    items: list[StreamItem] = field(default_factory=list)
    at: int = -1

    def to_payload(self) -> dict[str, t.Any]:
        """Convert to payload dict for WebSocket transmission."""
        return {
            "op": self.op,
            "stream": self.stream,
            "items": [{"id": item.dom_id, "html": item.html} for item in self.items],
            "at": self.at,
        }
