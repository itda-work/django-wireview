"""Phoenix LiveView-style Rendered structure for efficient HTML diffing.

This module implements a static/dynamic separation approach similar to
Phoenix LiveView's rendering engine, enabling minimal diff payloads
by only transmitting changed dynamic parts.

Reference: https://hexdocs.pm/phoenix_live_view/Phoenix.LiveView.Engine.html
"""

from __future__ import annotations

import hashlib
import re
import typing as t
from dataclasses import dataclass, field

# Marker pattern: <!--$0-->content<!--/$0-->
MARKER_PATTERN = re.compile(r"<!--\$(\d+)-->(.*?)<!--/\$\1-->", re.DOTALL)


@dataclass
class Rendered:
    """
    Represents a rendered template with static and dynamic parts separated.

    This structure mirrors Phoenix LiveView's approach where:
    - static: list of literal HTML strings that never change
    - dynamic: list of computed values that may change between renders
    - fingerprint: hash of static parts to detect structural changes

    The static and dynamic parts alternate:
    [static[0], dynamic[0], static[1], dynamic[1], ..., static[n]]

    Note: len(static) == len(dynamic) + 1
    """

    static: list[str] = field(default_factory=list)
    dynamic: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        """Compute fingerprint if not provided."""
        if not self.fingerprint and self.static:
            self.fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """Compute a hash of the static parts to detect structural changes.

        The fingerprint must capture both the content AND structure of static parts.
        Simply joining strings loses structure information (e.g., ['a', 'b'] vs ['ab']).
        We use a null separator that cannot appear in HTML content.
        """
        # Use null character as separator to preserve structure
        content = "\0".join(self.static)
        return hashlib.md5(content.encode()).hexdigest()[:8]

    def to_html(self) -> str:
        """Reconstruct full HTML by interleaving static and dynamic parts."""
        parts: list[str] = []
        for i, s in enumerate(self.static):
            parts.append(s)
            if i < len(self.dynamic):
                parts.append(self.dynamic[i])
        return "".join(parts)

    def get_diff(self, previous: Rendered | None) -> RenderedDiff | None:
        """
        Compute the diff between this render and the previous one.

        Returns:
            - Full render dict if first render or structure changed
            - Partial dict with only changed indices if values changed
            - None if nothing changed
        """
        if previous is None or self.fingerprint != previous.fingerprint:
            # First render or structure changed - send everything
            return RenderedDiff(
                is_full=True,
                static=self.static,
                dynamic=self.dynamic,
                fingerprint=self.fingerprint,
            )

        # Same structure - find changed dynamic values
        changes: dict[str, str] = {}
        for i, (new_val, old_val) in enumerate(zip(self.dynamic, previous.dynamic)):
            if new_val != old_val:
                changes[str(i)] = new_val

        if not changes:
            return None

        return RenderedDiff(is_full=False, changes=changes)

    @classmethod
    def from_marked_html(cls, html: str) -> Rendered:
        """
        Parse HTML with markers into a Rendered structure.

        Markers are in the format: <!--$0-->content<!--/$0-->
        where 0 is the index of the dynamic part.

        Args:
            html: HTML string with embedded markers

        Returns:
            Rendered instance with separated static/dynamic parts
        """
        static: list[str] = []
        dynamic: list[str] = []
        last_end = 0

        for match in MARKER_PATTERN.finditer(html):
            # Add static part before this marker
            static.append(html[last_end : match.start()])
            # Add dynamic content
            dynamic.append(match.group(2))
            last_end = match.end()

        # Add remaining static content after last marker
        static.append(html[last_end:])

        return cls(static=static, dynamic=dynamic)

    @classmethod
    def from_html_without_markers(cls, html: str) -> Rendered:
        """
        Create a Rendered from plain HTML (no markers).

        This is used for backward compatibility when templates
        don't have dynamic markers. The entire HTML is treated
        as a single static part.

        Args:
            html: Plain HTML string without markers

        Returns:
            Rendered with the entire HTML as static
        """
        return cls(static=[html], dynamic=[])

    def has_markers(self) -> bool:
        """Check if this Rendered has any dynamic parts."""
        return len(self.dynamic) > 0


@dataclass
class RenderedDiff:
    """
    Represents the diff between two Rendered instances.

    For full renders (first render or structure change):
        - is_full=True
        - static, dynamic, fingerprint are set

    For partial updates:
        - is_full=False
        - changes contains {index: new_value} mapping
    """

    is_full: bool = False
    static: list[str] = field(default_factory=list)
    dynamic: list[str] = field(default_factory=list)
    fingerprint: str = ""
    changes: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, t.Any]:
        """Convert to wire format for WebSocket transmission."""
        if self.is_full:
            return {
                "s": self.static,
                "d": self.dynamic,
                "f": self.fingerprint,
            }
        return self.changes

    def is_empty(self) -> bool:
        """Check if this diff contains no changes."""
        return not self.is_full and not self.changes


def has_markers(html: str) -> bool:
    """Check if HTML contains dynamic markers."""
    return "<!--$" in html


def inject_marker(content: str, index: int) -> str:
    """Wrap content with dynamic markers."""
    return f"<!--${index}-->{content}<!--/${index}-->"
