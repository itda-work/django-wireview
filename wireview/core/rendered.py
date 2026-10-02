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
- ``<!--$n--><!--@wv:id--><!--/$n-->`` a component reference (``{% live_component %}``
  output in a live render): the child renders and ships on its own, and the
  parent's slot only names it, so the parent's diff never carries child markup
- ``<!--@wv(:id-->…<!--@wv):id-->`` where a nested ``{% component %}`` drew
  itself in a live render. Parsing drops these: they only tell a slot's owner,
  which keeps a pre-rendered fill as text, which part of it is that component

A comprehension becomes a single dynamic slot whose value holds the item
template's statics once and one list of dynamics per item, mirroring
Phoenix's comprehensions. Adding or changing an item therefore never changes
the parent's fingerprint.

Items are diffed positionally, or, for a client that speaks protocol version
``MOVES_SINCE`` or later, matched by content: two items with equal dynamics
render the same HTML, so an item that only moved is sent as a range of the
previous list instead of its dynamics (GAP-030,
docs/design/keyed-comprehension.md).

Reference: https://hexdocs.pm/phoenix_live_view/Phoenix.LiveView.Engine.html
"""

from __future__ import annotations

import hashlib
import json
import re
import typing as t
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qs

# Flat marker pattern, kept for callers that only need plain dynamic values.
MARKER_PATTERN = re.compile(r"<!--\$(\d+)-->(.*?)<!--/\$\1-->", re.DOTALL)
_TOKEN = re.compile(r"<!--(/?)\$([CIB]?)(\d+)-->")
_REF_PREFIX = "<!--@wv:"
_REF = re.compile(r"<!--@wv:([^>]+?)-->")
# A reference with its marker whole, or any other marker
_MARKER_OR_REF = re.compile(r"(<!--\$(\d+)--><!--@wv:[^>]+?--><!--/\$\2-->)|<!--/?\$[CIB]?(\d+)-->")
_NESTED_PREFIX = "<!--@wv("
_NESTED = re.compile(r"<!--@wv[()]:[^>]+?-->")
# A reference, or a nested component's whole output (the outermost, by its own id)
_PLACE = re.compile(r"<!--@wv:([^>]+?)-->|<!--@wv\(:([^>]+?)-->.*?<!--@wv\):\2-->", re.DOTALL)

Dynamic = t.Union[str, "Comprehension", "Rendered", "ComponentRef"]
Payload = t.Union[str, dict[str, t.Any]]

# The diff protocol this server speaks. A client names the version it
# understands in the WebSocket URL (``?vsn=``); the server never sends a form
# newer than that, and a client that names none gets version 0. Keep in step
# with ``PROTOCOL_VERSION`` in static/wireview/rendered.mjs.
PROTOCOL_VERSION = 6
# First version whose clients apply ``{"k": [...]}`` comprehension updates.
MOVES_SINCE = 2
# First version that pairs a user event with its render through ``ref`` (#92).
# It runs the other way: the server announces its version on the render that
# answers a join, and a client sends ``ref`` only to a server that did.
REFS_SINCE = 3
# First version whose clients understand ``error``: a component whose handler
# raised is joined again from the state its element still carries, and one
# whose join failed is marked rather than removed (#94).
ERRORS_SINCE = 4
# First version whose clients hear ``joined``: the join and everything joined()
# queued have gone out. Infinite scroll waits for it to judge the list (#112).
JOINED_SINCE = 5
# First version that takes a ``ref`` on ``join`` and returns it on the render and
# the error that answer it. Like REFS_SINCE it runs the other way: a client names
# its join only to a server that announced this version (#139).
JOIN_REFS_SINCE = 6
# First version whose clients send the old page's leaves before the new page's
# joins on a boosted visit (#146). Not a form: it says what a join under an id
# that already joined can mean. Before it, the join may come while the old page
# is still in the repository, so a component retired for it keeps no slot.
LEAVES_FIRST_SINCE = 6


def protocol_version(query_string: bytes | str) -> int:
    """The protocol version a client named in its WebSocket URL, conservatively.

    Anything but exactly one non-negative decimal ``vsn`` reads as 0, a client
    that understands only the oldest forms: guessing high would send an old
    page shapes it renders as ``[object Object]``.
    """
    if isinstance(query_string, bytes):
        query_string = query_string.decode("latin-1")
    values = parse_qs(query_string, keep_blank_values=True).get("vsn", [])
    if len(values) != 1 or not (values[0].isascii() and values[0].isdigit()):
        return 0
    return int(values[0])


def _fingerprint(static: list[str]) -> str:
    """Hash the static parts, keeping their structure (``['a', 'b']`` != ``['ab']``)."""
    return hashlib.md5("\0".join(static).encode()).hexdigest()[:8]


def _interleave(static: list[str], dynamic: list[Dynamic]) -> str:
    if not dynamic:
        return "".join(static)
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
        if isinstance(value.get("c"), str):
            return ComponentRef(value["c"])
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


def _diff_value(new: Dynamic, old: Dynamic | None, moves: bool = False) -> Payload | None:
    """Change payload for one dynamic slot, or ``None`` when it is unchanged."""
    if isinstance(new, str):
        return None if new == old else new
    return new.diff(old, moves)


def _identity(value: Dynamic) -> t.Hashable:
    """A hashable stand-in for a dynamic: equal identities mean equal dynamics.

    Tags keep the kinds apart (a block and a comprehension with the same
    parts are not the same thing), and a component reference is its own
    identity because it is frozen.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Rendered):
        return ("r", tuple(value.static), tuple(_identity(v) for v in value.dynamic))
    if isinstance(value, Comprehension):
        return ("s", tuple(value.static), tuple(tuple(_identity(v) for v in item) for item in value.dynamics))
    return value


def _item_identity(item: list[Dynamic]) -> t.Hashable:
    # Most items hold only strings and references, which hash as they are. The
    # tagged tuples the slow path builds never equal a string or a reference, so
    # the two kinds of key cannot collide.
    key = tuple(item)
    try:
        hash(key)
    except TypeError:
        return tuple(_identity(v) for v in item)
    return key


def _longer_than(positional: dict[str, t.Any], limit: int) -> bool:
    """Whether ``json.dumps(positional)`` is longer than ``limit``, serializing no more than it must.

    Counts the characters ``json.dumps`` would write for ``{"u": {...}, "n": n}``
    with its default separators, one entry at a time, and stops as soon as the
    total passes ``limit``. A rotation of a long list would otherwise serialize
    every item only to learn that two runs are smaller.
    """
    size = len('{"u": {}, "n": }') + len(str(positional["n"]))
    for i, (key, value) in enumerate(positional["u"].items()):
        size += len(key) + len('"": ') + len(json.dumps(value)) + (len(", ") if i else 0)
        if size > limit:
            return True
    return size > limit


def _matched_segments(old: list[list[Dynamic]], new: list[list[Dynamic]]) -> list[t.Any]:
    """Describe ``new`` as ranges of ``old`` and new items, matching items by content.

    The common prefix and suffix are compared with ``==`` only; the middle is
    matched through a map from item identity to the old positions holding it.
    Among equal items the earliest is taken: any of them renders the same HTML.
    No old position is used twice, so the client may reuse old item objects.
    """
    top = min(len(old), len(new))
    lo = 0
    while lo < top and old[lo] == new[lo]:
        lo += 1
    hi = 0
    while hi < top - lo and old[len(old) - 1 - hi] == new[len(new) - 1 - hi]:
        hi += 1

    where: dict[t.Hashable, deque[int]] = {}
    for j in range(lo, len(old) - hi):
        where.setdefault(_item_identity(old[j]), deque()).append(j)

    segments: list[t.Any] = [[0, lo]] if lo else []

    def take(j: int) -> None:
        last = segments[-1] if segments else None
        if isinstance(last, list) and last[0] + last[1] == j:
            last[1] += 1
        else:
            segments.append([j, 1])

    for item in new[lo : len(new) - hi]:
        slots = where.get(_item_identity(item))
        if slots:
            take(slots.popleft())
        else:
            segments.append({"d": [_payload_value(v) for v in item]})
    if hi:
        tail = len(old) - hi
        last = segments[-1] if segments else None
        if isinstance(last, list) and last[0] + last[1] == tail:
            last[1] += hi
        else:
            segments.append([tail, hi])
    return segments


def _positional_suffices(old: list[list[Dynamic]], new: list[list[Dynamic]]) -> bool:
    """True when the positional form needs no comparison: it is the smaller one, or kept on purpose.

    That is when one list is a prefix of the other (an append, a truncation,
    no change at all) or the lists have the same length and differ in at most
    one position. Both stay byte-identical to what older clients receive.
    """
    top = min(len(old), len(new))
    lo = 0
    while lo < top and old[lo] == new[lo]:
        lo += 1
    if lo == top:
        return True
    if len(old) != len(new):
        return False
    return all(old[i] == new[i] for i in range(lo + 1, len(new)))


@dataclass(frozen=True)
class ComponentRef:
    """A slot that holds a nested LiveComponent, identified by id.

    The child's HTML is not here. The client keeps every component's render
    and substitutes the child's current HTML when it builds the parent, so a
    parent re-render never re-sends child markup and a child update never
    touches the parent. Wire form: ``{"c": id}``.
    """

    id: str

    def to_html(self) -> str:
        return component_ref_placeholder(self.id)

    def to_payload(self) -> dict[str, t.Any]:
        return {"c": self.id}

    def diff(self, previous: Dynamic | None, moves: bool = False) -> dict[str, t.Any] | None:
        return None if previous == self else self.to_payload()


def component_ref_placeholder(component_id: str) -> str:
    """The comment a live render emits where a LiveComponent goes."""
    return f"{_REF_PREFIX}{component_id}-->"


def component_ref_marker(component_id: str, index: int) -> str:
    """A component reference wrapped as dynamic slot ``index``."""
    return inject_marker(component_ref_placeholder(component_id), index)


@dataclass
class Comprehension:
    """A ``{% for %}`` loop: the item template's statics once, dynamics per item."""

    static: list[str] = field(default_factory=list)
    dynamics: list[list[Dynamic]] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.static) if self.static else ""

    def to_html(self) -> str:
        return "".join(_interleave(self.static, item) for item in self.dynamics)

    def to_payload(self) -> dict[str, t.Any]:
        return {
            "s": list(self.static),
            "d": [[_payload_value(v) for v in item] for item in self.dynamics],
        }

    def diff(self, previous: Dynamic | None, moves: bool = False) -> dict[str, t.Any] | None:
        """Diff against the previous value of the same slot.

        Returns the full comprehension when the item template changed (or the
        slot was not a comprehension before), or ``None`` when nothing changed.
        Otherwise the positional form ``{"u": {index: dynamics}, "n": count}``
        with only the items that changed; or, when ``moves`` is on and it is
        smaller on the wire, ``{"k": [segment, ...]}`` where a segment is
        ``[start, length]`` (a run of the previous items) or ``{"d": dynamics}``
        (a new item). Ties go to the positional form.
        """
        if not isinstance(previous, Comprehension) or previous.static != self.static:
            return self.to_payload()
        positional = self._positional(previous)
        if positional is None or not moves or _positional_suffices(previous.dynamics, self.dynamics):
            return positional
        matched = {"k": _matched_segments(previous.dynamics, self.dynamics)}
        # Sizes as the consumer sends them: Channels serializes with json.dumps defaults.
        return matched if _longer_than(positional, len(json.dumps(matched))) else positional

    def _positional(self, previous: Comprehension) -> dict[str, t.Any] | None:
        updates: dict[str, list[Payload]] = {}
        for i, item in enumerate(self.dynamics):
            if i >= len(previous.dynamics) or item != previous.dynamics[i]:
                updates[str(i)] = [_payload_value(v) for v in item]

        if not updates and len(self.dynamics) == len(previous.dynamics):
            return None
        return {"u": updates, "n": len(self.dynamics)}


@dataclass(init=False)
class Rendered:
    """
    Represents a rendered template with static and dynamic parts separated.

    The static and dynamic parts alternate:
    [static[0], dynamic[0], static[1], dynamic[1], ..., static[n]]

    Note: len(static) == len(dynamic) + 1
    """

    static: list[str]
    dynamic: list[Dynamic]
    _fingerprint: str | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        static: list[str] | None = None,
        dynamic: list[Dynamic] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        self.static = static if static is not None else []
        self.dynamic = dynamic if dynamic is not None else []
        # Hashing is deferred: nested blocks and comprehension items never need it.
        self._fingerprint = fingerprint or None

    @property
    def fingerprint(self) -> str:
        """Hash of the static parts, used to detect structural changes. Computed once."""
        if self._fingerprint is None:
            self._fingerprint = _fingerprint(self.static) if self.static else ""
        return self._fingerprint

    def _compute_fingerprint(self) -> str:
        return _fingerprint(self.static)

    def to_html(self) -> str:
        """Reconstruct full HTML by interleaving static and dynamic parts."""
        return _interleave(self.static, self.dynamic)

    def to_payload(self) -> dict[str, t.Any]:
        """Wire form when this render is a nested block inside another render."""
        return {"r": list(self.static), "d": [_payload_value(v) for v in self.dynamic]}

    def changes(self, previous: Rendered, moves: bool = False) -> dict[str, Payload]:
        """Changed dynamic slots against a render with the same structure."""
        changes: dict[str, Payload] = {}
        for i, (new_val, old_val) in enumerate(zip(self.dynamic, previous.dynamic)):
            change = _diff_value(new_val, old_val, moves)
            if change is not None:
                changes[str(i)] = change
        return changes

    def diff(self, previous: Dynamic | None, moves: bool = False) -> dict[str, t.Any] | None:
        """Diff as a nested block: full ``{"r", "d"}`` or partial ``{"p": changes}``."""
        if not isinstance(previous, Rendered) or previous.static != self.static:
            return self.to_payload()
        changes = self.changes(previous, moves)
        return {"p": changes} if changes else None

    def get_diff(self, previous: Rendered | None, vsn: int = 0) -> RenderedDiff | None:
        """
        Compute the diff between this render and the previous one.

        ``vsn`` is the protocol version the receiving client speaks; forms newer
        than that are never produced.

        Returns:
            - Full render if first render or structure changed
            - Partial diff with only changed indices if values changed
            - None if nothing changed
        """
        if previous is None or self.static != previous.static:
            return RenderedDiff(
                is_full=True,
                static=self.static,
                dynamic=[_payload_value(v) for v in self.dynamic],
                fingerprint=self.fingerprint,
            )

        changes = self.changes(previous, moves=vsn >= MOVES_SINCE)
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
    def from_marked_html(cls, html: str, stale: t.Collection[int] = ()) -> Rendered:
        """Parse HTML with markers into a Rendered structure.

        ``stale`` names the marker indices of the parts that read a temporary
        assign that was reset (wireview/core/render_reads.py); they come out
        as ``Stale`` until ``settle()``.
        """
        if _NESTED_PREFIX in html:
            html = _NESTED.sub("", html)
        static, dynamic = _parse(html, set(stale))
        return cls(static=static, dynamic=dynamic)

    def settle(self, previous: Rendered | None) -> None:
        """Give each ``Stale`` part the value it had in ``previous``, so it is unchanged.

        A part is matched by position, which only means something while the
        statics around it are the same; otherwise -- a first render, another
        branch, another template -- it keeps what it rendered now.
        """
        aligned = previous is not None and previous.static == self.static
        for i, value in enumerate(self.dynamic):
            before = previous.dynamic[i] if aligned and previous is not None else None
            if isinstance(value, Stale):
                self.dynamic[i] = before if before is not None else value.value
                if isinstance(self.dynamic[i], Rendered) and before is None:
                    self.dynamic[i].settle(None)  # type: ignore[union-attr]
            elif isinstance(value, Rendered):
                value.settle(before if isinstance(before, Rendered) else None)

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


@dataclass
class Stale:
    """A part that read a reset temporary assign: unchanged, whatever it rendered (#111).

    Only alive between parsing and ``Rendered.settle()``.
    """

    value: Dynamic

    def to_html(self) -> str:
        return self.value if isinstance(self.value, str) else self.value.to_html()


def _finish(kind: str, static: list[str], dynamic: list[t.Any]) -> t.Any:
    """Turn a closed marker region into its dynamic value."""
    if kind == "C":
        return _comprehension(static, dynamic)
    inner = [_flatten_item(v) for v in dynamic]
    if kind == "I":
        return _Item(Rendered(static=static, dynamic=inner))
    if kind == "B" and inner:
        return Rendered(static=static, dynamic=inner)
    # A variable, or a branch with no dynamics of its own: plain text.
    text = _interleave(static, inner)
    if kind == "" and text.startswith(_REF_PREFIX) and (match := _REF.fullmatch(text)):
        return ComponentRef(match.group(1))
    return text


def _parse(html: str, stale: set[int] | None = None) -> tuple[list[str], list[Dynamic]]:
    """Split the rendered HTML on marker comments and fold the regions.

    ``re.split`` with the three marker groups yields
    ``[text, close, kind, index, text, close, kind, index, ..., text]``; one pass
    over that list with an explicit stack is several times faster than a
    recursive parser built on ``re.Match`` objects, and this runs on every
    render.
    """
    parts = _TOKEN.split(html)
    static: list[str] = [parts[0]]
    dynamic: list[t.Any] = []
    stack: list[tuple[str, int, list[str], list[t.Any]]] = []
    for i in range(1, len(parts), 4):
        close, kind, index, text = parts[i], parts[i + 1], int(parts[i + 2]), parts[i + 3]
        if not close:
            stack.append((kind, index, static, dynamic))
            static, dynamic = [text], []
        elif stack and stack[-1][0] == kind and stack[-1][1] == index:
            value = _finish(kind, static, dynamic)
            _kind, _index, static, dynamic = stack.pop()
            # Inside a loop the item is compared whole, so a part there cannot keep
            # its own value; only the loop can (wireview/core/render_reads.py).
            if stale and kind != "I" and index in stale and not any(entry[0] in ("I", "C") for entry in stack):
                value = Stale(value)
            dynamic.append(value)
            static.append(text)
        else:
            static[-1] += text  # stray closing marker: drop it, keep the text
    while stack:  # unclosed regions: fold their content back into the parent as text
        kind, index, parent_static, parent_dynamic = stack.pop()
        parent_static[-1] += _interleave(static, [_flatten_item(v) for v in dynamic])
        static, dynamic = parent_static, parent_dynamic
    return static, [_flatten_item(v) for v in dynamic]


def keep_stale(
    html: str,
    stale: t.Collection[int],
    previous: Rendered | None,
    first: int = 0,
    mark: t.Callable[[], int] | None = None,
) -> str:
    """``html`` with the parts in ``stale`` as ``previous`` drew them, their markers kept.

    For a component drawn in another component's pass (#111): ``stale`` names
    the parts of its marked output that read nothing but a reset temporary
    assign, and ``previous`` is its own last render. Each such part takes back
    what that render drew there, exactly as ``settle()`` would give it on the
    component's own next render -- the page shows it, and the other
    component's diff puts that drawing on the page. A part ``previous`` has
    nothing for, or one in a loop, stays as drawn. The part keeps its marker,
    so the enclosing render keeps its structure; what it holds goes in as
    text. Not a value that names a LiveComponent: as text the reference would
    reach the page as a comment. With ``mark``, which hands out marker indices
    of the enclosing pass, it goes in marked as ``previous`` holds it, so the
    reference stays one and the LiveComponent stays on the page; without, the
    part stays as drawn.

    Markers numbered below ``first`` are the enclosing pass's, in a fill it
    drew before the component. The component's own render has that fill as
    text, so they are left out when lining the two up -- all but a
    LiveComponent's reference and a nested component's output, which its own
    render numbers too.
    """
    if not stale or previous is None:
        return html
    stale = set(stale)
    # The outermost stale parts outside any loop, in the order they open: the
    # order settle() meets them, which is what pairs them with ``previous``
    spans: list[tuple[int, int, int]] = []
    stack: list[tuple[str, int, int]] = []
    for match in _TOKEN.finditer(html):
        close, kind, index = match.group(1), match.group(2), int(match.group(3))
        if not close:
            stack.append((kind, index, match.end()))
        elif stack and stack[-1][0] == kind and stack[-1][1] == index:
            start = stack.pop()[2]
            if kind != "I" and index in stale and not any(entry[0] in ("I", "C") for entry in stack):
                spans = [span for span in spans if span[0] < start]  # a stale part inside this one
                spans.append((start, match.start(), index))
    if stack or not spans:
        return html
    if first:
        nested = [match.span() for match in _PLACE.finditer(html) if match.group(2)]

        def own(match: re.Match[str]) -> str:
            kept = match.group(1) or int(match.group(3)) >= first
            return match.group(0) if kept or any(a <= match.start() < b for a, b in nested) else ""

        html_own = _MARKER_OR_REF.sub(own, html)
    else:
        html_own = html
    parsed = Rendered.from_marked_html(html_own, stale)
    kept: list[Dynamic | None] = []

    def walk(rendered: Rendered, before: Rendered | None) -> None:
        aligned = before is not None and before.static == rendered.static
        for i, value in enumerate(rendered.dynamic):
            old = before.dynamic[i] if aligned and before is not None else None
            if isinstance(value, Stale):
                kept.append(old)
            elif isinstance(value, Rendered):
                walk(value, old if isinstance(old, Rendered) else None)

    walk(parsed, previous)
    if len(kept) != len(spans):
        return html  # markers this parse does not read the same way: draw it as it is
    parts: list[str] = []
    end = 0
    for (start, stop, index), value in zip(spans, kept):
        if value is None:
            continue
        if isinstance(value, str):
            text = value
        elif not _names_components(value):
            text = value.to_html()
        elif mark is not None:
            text = _marked_content(value, index, mark)
        else:
            continue
        parts += [html[end:start], text]
        end = stop
    parts.append(html[end:])
    return "".join(parts)


def _marked(value: Dynamic, mark: t.Callable[[], int]) -> str:
    """``value`` as marked HTML that parses back to it, numbered by ``mark``."""
    if isinstance(value, str):
        return inject_marker(value, mark())
    if isinstance(value, ComponentRef):
        return component_ref_marker(value.id, mark())
    index = mark()
    kind = "B" if isinstance(value, Rendered) else "C"
    return f"<!--${kind}{index}-->{_marked_content(value, index, mark)}<!--/${kind}{index}-->"


def _marked_content(value: Dynamic, index: int, mark: t.Callable[[], int]) -> str:
    """What goes between the markers of part ``index`` for it to parse back to ``value``."""
    if isinstance(value, Rendered):
        return _interleave(value.static, [_marked(v, mark) for v in value.dynamic])
    if isinstance(value, Comprehension):
        return "".join(
            f"<!--$I{index}-->{_interleave(value.static, [_marked(v, mark) for v in item])}<!--/$I{index}-->"
            for item in value.dynamics
        )
    return value if isinstance(value, str) else value.to_html()


def _names_components(value: Dynamic) -> bool:
    return bool(component_refs(Rendered(["", ""], [value])))


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
        and all(item.rendered.static == items[0].rendered.static for item in items[1:])
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


def holds_nested_components(html: str) -> bool:
    """Whether ``html`` holds a nested component's marked output."""
    return _NESTED_PREFIX in html


def shift_markers(html: str, by: int) -> str:
    """``html`` with each marker's index moved ``by`` places (``by`` keeps them apart from what is around them)."""
    if not by:
        return html
    return _TOKEN.sub(lambda m: f"<!--{m.group(1)}${m.group(2)}{int(m.group(3)) + by}-->", html)


def nested_component_html(component_id: str, html: str) -> str:
    """``html``, a nested component's live output, marked as that component's (dropped when parsed)."""
    return f"{_NESTED_PREFIX}:{component_id}-->{html}<!--@wv):{component_id}-->"


def split_components(html: str) -> list[tuple[str, str | None, bool]]:
    """``html`` cut where components go: ``(text before, id, live)`` each, then ``(the rest, None, False)``.

    ``live`` is true for a LiveComponent's reference and false for a nested
    component's marked output, which is cut out whole.
    """
    parts: list[tuple[str, str | None, bool]] = []
    start = 0
    for match in _PLACE.finditer(html):
        live = match.group(1) is not None
        parts.append((html[start : match.start()], match.group(1) if live else match.group(2), live))
        start = match.end()
    parts.append((html[start:], None, False))
    return parts


def payload_component_refs(payload: t.Any) -> list[str]:
    """Ids of every LiveComponent referenced in a diff payload, full or partial, in order."""
    found: list[str] = []

    def walk(value: t.Any) -> None:
        if isinstance(value, dict):
            if isinstance(ref := value.get("c"), str):
                found.append(ref)
                return
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found


def component_refs(rendered: Rendered) -> list[str]:
    """Ids of every LiveComponent referenced anywhere in a render, in document order."""
    found: list[str] = []

    def walk(value: Dynamic) -> None:
        if isinstance(value, ComponentRef):
            found.append(value.id)
        elif isinstance(value, Rendered):
            for v in value.dynamic:
                walk(v)
        elif isinstance(value, Comprehension):
            for item in value.dynamics:
                for v in item:
                    walk(v)

    for v in rendered.dynamic:
        walk(v)
    return found
