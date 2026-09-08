"""Phoenix LiveView-style Rendered structure for efficient HTML diffing.

The rendered HTML of a component is split into *static* parts (template
literals) and *dynamic* parts (variable output, the signed state, and
``{% for %}`` loops). Between renders only the dynamic parts that changed
are sent; the static parts are identified by a fingerprint.

Markers are HTML comments emitted by ``wireview.template_engine``:

- ``<!--$n-->…<!--/$n-->``   a dynamic value (variable output)
- ``<!--$Cn-->…<!--/$Cn-->`` a comprehension (``{% for %}`` output)
- ``<!--$In-->…<!--/$In-->`` one iteration of comprehension ``n``
- ``<!--$Bn-->…<!--/$Bn-->`` a block (``{% if %}`` output): a nested render
  with its own statics, so switching branches never changes the parent's
  statics and loop items with conditionals stay uniform

A comprehension becomes a single dynamic slot whose value holds the item
template's statics once and one list of dynamics per item, mirroring
Phoenix's comprehensions. Adding or changing an item therefore never changes
the parent's fingerprint.

Reference: https://hexdocs.pm/phoenix_live_view/Phoenix.LiveView.Engine.html
"""

from __future__ import annotations

import hashlib
import re
import typing as t
from dataclasses import dataclass, field

# Flat marker pattern, kept for callers that only need plain dynamic values.
MARKER_PATTERN = re.compile(r"<!--\$(\d+)-->(.*?)<!--/\$\1-->", re.DOTALL)
_TOKEN = re.compile(r"<!--(/?)\$([CIB]?)(\d+)-->")

Dynamic = t.Union[str, "Comprehension", "Rendered"]
Payload = t.Union[str, dict[str, t.Any]]


def _fingerprint(static: list[str]) -> str:
    """Hash the static parts, keeping their structure (``['a', 'b']`` != ``['ab']``)."""
    return hashlib.md5("\0".join(static).encode()).hexdigest()[:8]


def _interleave(static: list[str], dynamic: list[Dynamic]) -> str:
    parts: list[str] = []
    for i, s in enumerate(static):
        parts.append(s)
        if i < len(dynamic):
            value = dynamic[i]
            parts.append(value if isinstance(value, str) else value.to_html())
    return "".join(parts)


def _payload_value(value: Dynamic) -> Payload:
    if isinstance(value, str):
        return value
    return value.to_payload()


def _value_from_payload(value: t.Any) -> Dynamic:
    if isinstance(value, dict):
        if "r" in value:
            return Rendered(
                static=list(value.get("r", [])),
                dynamic=[_value_from_payload(v) for v in value.get("d", [])],
            )
        return Comprehension(
            static=list(value.get("s", [])),
            dynamics=[[_value_from_payload(v) for v in item] for item in value.get("d", [])],
        )
    return "" if value is None else str(value)


def _diff_value(new: Dynamic, old: Dynamic | None) -> Payload | None:
    """Change payload for one dynamic slot, or ``None`` when it is unchanged."""
    if isinstance(new, str):
        return None if new == old else new
    return new.diff(old)


@dataclass
class Comprehension:
    """A ``{% for %}`` loop: the item template's statics once, dynamics per item."""

    static: list[str] = field(default_factory=list)
    dynamics: list[list[Dynamic]] = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint and self.static:
            self.fingerprint = _fingerprint(self.static)

    def to_html(self) -> str:
        return "".join(_interleave(self.static, item) for item in self.dynamics)

    def to_payload(self) -> dict[str, t.Any]:
        return {
            "s": list(self.static),
            "d": [[_payload_value(v) for v in item] for item in self.dynamics],
        }

    def diff(self, previous: Dynamic | None) -> dict[str, t.Any] | None:
        """Diff against the previous value of the same slot.

        Returns the full comprehension when the item template changed (or the
        slot was not a comprehension before), ``{"u": {index: dynamics}, "n":
        count}`` with only the items that changed, or ``None`` when nothing did.
        """
        if not isinstance(previous, Comprehension) or previous.fingerprint != self.fingerprint:
            return self.to_payload()

        updates: dict[str, list[Payload]] = {}
        for i, item in enumerate(self.dynamics):
            if i >= len(previous.dynamics) or item != previous.dynamics[i]:
                updates[str(i)] = [_payload_value(v) for v in item]

        if not updates and len(self.dynamics) == len(previous.dynamics):
            return None
        return {"u": updates, "n": len(self.dynamics)}


@dataclass
class Rendered:
    """
    Represents a rendered template with static and dynamic parts separated.

    The static and dynamic parts alternate:
    [static[0], dynamic[0], static[1], dynamic[1], ..., static[n]]

    Note: len(static) == len(dynamic) + 1
    """

    static: list[str] = field(default_factory=list)
    dynamic: list[Dynamic] = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        """Compute fingerprint if not provided."""
        if not self.fingerprint and self.static:
            self.fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """Hash of the static parts, used to detect structural changes."""
        return _fingerprint(self.static)

    def to_html(self) -> str:
        """Reconstruct full HTML by interleaving static and dynamic parts."""
        return _interleave(self.static, self.dynamic)

    def to_payload(self) -> dict[str, t.Any]:
        """Wire form when this render is a nested block inside another render."""
        return {"r": list(self.static), "d": [_payload_value(v) for v in self.dynamic]}

    def changes(self, previous: Rendered) -> dict[str, Payload]:
        """Changed dynamic slots against a render with the same structure."""
        changes: dict[str, Payload] = {}
        for i, (new_val, old_val) in enumerate(zip(self.dynamic, previous.dynamic)):
            change = _diff_value(new_val, old_val)
            if change is not None:
                changes[str(i)] = change
        return changes

    def diff(self, previous: Dynamic | None) -> dict[str, t.Any] | None:
        """Diff as a nested block: full ``{"r", "d"}`` or partial ``{"p": changes}``."""
        if not isinstance(previous, Rendered) or previous.fingerprint != self.fingerprint:
            return self.to_payload()
        changes = self.changes(previous)
        return {"p": changes} if changes else None

    def get_diff(self, previous: Rendered | None) -> RenderedDiff | None:
        """
        Compute the diff between this render and the previous one.

        Returns:
            - Full render if first render or structure changed
            - Partial diff with only changed indices if values changed
            - None if nothing changed
        """
        if previous is None or self.fingerprint != previous.fingerprint:
            return RenderedDiff(
                is_full=True,
                static=self.static,
                dynamic=[_payload_value(v) for v in self.dynamic],
                fingerprint=self.fingerprint,
            )

        changes = self.changes(previous)
        if not changes:
            return None

        return RenderedDiff(is_full=False, changes=changes)

    def to_dict(self) -> dict[str, t.Any]:
        """Serialize for storage outside the process (session state, another worker)."""
        return {"s": list(self.static), "d": [_payload_value(v) for v in self.dynamic], "f": self.fingerprint}

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> Rendered:
        """Rebuild from ``to_dict()`` output. The fingerprint is recomputed, never trusted."""
        return cls(
            static=list(data.get("s", [])),
            dynamic=[_value_from_payload(v) for v in data.get("d", [])],
        )

    @classmethod
    def from_marked_html(cls, html: str) -> Rendered:
        """Parse HTML with markers into a Rendered structure."""
        static, dynamic = _Parser(html).parse()
        return cls(static=static, dynamic=dynamic)

    @classmethod
    def from_html_without_markers(cls, html: str) -> Rendered:
        """Create a Rendered from plain HTML: the entire HTML is one static part."""
        return cls(static=[html], dynamic=[])

    def has_markers(self) -> bool:
        """Check if this Rendered has any dynamic parts."""
        return len(self.dynamic) > 0


@dataclass
class _Item:
    """A parsed comprehension iteration, only alive while parsing."""

    rendered: Rendered


class _Parser:
    """Recursive-descent parser over the marker comments of a rendered template."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.tokens = [
            (m.group(1) == "/", m.group(2), int(m.group(3)), m.start(), m.end()) for m in _TOKEN.finditer(html)
        ]
        self.pos = 0

    def parse(self) -> tuple[list[str], list[Dynamic]]:
        static, dynamic, _ = self._region(None, 0)
        return static, [_flatten_item(v) for v in dynamic]

    def _region(self, closing: tuple[str, int] | None, cursor: int) -> tuple[list[str], list[Dynamic | _Item], int]:
        """Collect statics/dynamics until ``closing`` is met. Returns the end offset."""
        static: list[str] = []
        dynamic: list[Dynamic | _Item] = []
        while self.pos < len(self.tokens):
            is_close, kind, idx, start, end = self.tokens[self.pos]
            self.pos += 1
            if is_close:
                if closing is not None and (kind, idx) == closing:
                    static.append(self.html[cursor:start])
                    return static, dynamic, end
                continue  # stray closing marker: keep it as text
            static.append(self.html[cursor:start])
            inner_static, inner_dynamic, cursor = self._region((kind, idx), end)
            if kind == "C":
                dynamic.append(_comprehension(inner_static, inner_dynamic))
            elif kind == "I":
                dynamic.append(_Item(Rendered(static=inner_static, dynamic=[_flatten_item(v) for v in inner_dynamic])))
            elif kind == "B":
                dynamic.append(Rendered(static=inner_static, dynamic=[_flatten_item(v) for v in inner_dynamic]))
            else:
                dynamic.append(_interleave(inner_static, [_flatten_item(v) for v in inner_dynamic]))
        static.append(self.html[cursor:])
        return static, dynamic, len(self.html)


def _flatten_item(value: Dynamic | _Item) -> Dynamic:
    """An item marker outside its comprehension is just text."""
    if isinstance(value, _Item):
        return value.rendered.to_html()
    return value


def _comprehension(static: list[str], dynamic: list[Dynamic | _Item]) -> Dynamic:
    """Turn a parsed ``C`` region into a Comprehension, or plain text when it is not uniform.

    A uniform loop is a sequence of items sharing one item template, with
    nothing but whitespace between them. Anything else (an ``{% empty %}``
    clause, items whose conditionals produced different structures) is sent
    as one dynamic string.
    """
    items = [v for v in dynamic if isinstance(v, _Item)]
    uniform = (
        len(items) == len(dynamic)
        and all(not s.strip() for s in static)
        and len({item.rendered.fingerprint for item in items}) <= 1
    )
    if not uniform:
        return _interleave(static, [_flatten_item(v) for v in dynamic])
    if not items:
        return Comprehension()
    return Comprehension(
        static=items[0].rendered.static,
        dynamics=[item.rendered.dynamic for item in items],
    )


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
    dynamic: list[Payload] = field(default_factory=list)
    fingerprint: str = ""
    changes: dict[str, Payload] = field(default_factory=dict)

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


def strip_markers(html: str) -> str:
    """Remove every marker comment, leaving the plain HTML."""
    return _TOKEN.sub("", html)
