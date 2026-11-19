"""The page a mounted component draws, read the way the browser reads it (GAP-056, #252).

``MountedComponent.click()``, ``change()``, ``input()`` and ``submit()`` find an
element in the component's render, dispatch a DOM event at it, and send the
server what the client would: the handler and arguments of the ``wire-on-*``
bindings the event meets on its way up, with the fields the client serializes.
A binding the template does not have is not called -- that is the point.

Everything the client decides is mirrored from its source, which stays the
authority:

- the binding attributes and their modifiers: ``static/wireview/events.mjs``
  (``parseBinding``, ``bindingsFor``, ``runSteps``). ``tests/js/bindings.mjs``
  runs those functions on the inputs ``tests/test_dom_events.py`` gives these;
- delegation, the component an event goes to and the fields it carries:
  ``EventBindings.bubble``/``run``, ``wireview.send`` and ``Component.serialize``
  in ``static/wireview/wireview.js``. ``tests/test_dom_events_e2e.py`` does the
  same things in a browser and compares what the server heard.

No JavaScript runs here: a ``JS()`` chain is not executed, a debounce or a
throttle does not wait, a hook sees nothing. Standard library only.
"""

from __future__ import annotations

import dataclasses
import json
import re
import typing as t
from html.parser import HTMLParser

# ============================================================================
# A small DOM
# ============================================================================

#: Elements that never have content (the HTML parser closes them at once)
VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"}
)
#: An open element of these is closed by the start of another of the same:
#: what a browser's parser does with an unclosed ``<li>`` or ``<option>``
SELF_CLOSING_SIBLINGS = frozenset({"li", "option", "p", "tr", "td", "th", "dt", "dd"})


class Element:
    """An element of the render: its tag, its attributes (the first of a repeated name wins) and its children."""

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: Element | None) -> None:
        self.tag = tag
        self.attrs: dict[str, str] = {}
        for name, value in attrs:
            self.attrs.setdefault(name, "" if value is None else value)
        self.parent = parent
        self.children: list[Element | str] = []

    # -- tree ---------------------------------------------------------------

    def ancestors(self) -> t.Iterator[Element]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def closest(self, predicate: t.Callable[[Element], bool]) -> Element | None:
        """The element itself or its nearest ancestor that ``predicate`` holds for (``Element.closest``)."""
        if predicate(self):
            return self
        return next((a for a in self.ancestors() if predicate(a)), None)

    def descendants(self) -> t.Iterator[Element]:
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.descendants()

    def contains(self, other: Element) -> bool:
        """``Node.contains``: true for the element itself too."""
        return other is self or self in other.ancestors()

    @property
    def text(self) -> str:
        """``textContent``."""
        return "".join(c if isinstance(c, str) else c.text for c in self.children)

    def has(self, name: str) -> bool:
        return name in self.attrs

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    # -- display ------------------------------------------------------------

    def describe(self) -> str:
        """The element's start tag, shortened: what an error names."""
        parts = [self.tag]
        for name, value in self.attrs.items():
            if name in ("data-state", "style"):
                continue
            parts.append(f'{name}="{value}"' if value != "" else name)
        tag = "<" + " ".join(parts) + ">"
        if len(tag) > 160:
            tag = tag[:157] + "...>"
        label = " ".join(self.text.split())
        if label:
            tag += f" ({label[:40]}{'...' if len(label) > 40 else ''})"
        return tag

    def __repr__(self) -> str:
        return f"Element({self.describe()})"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", [], None)
        self._open: list[Element] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SELF_CLOSING_SIBLINGS and self._open[-1].tag == tag:
            self._open.pop()
        parent = self._open[-1]
        element = Element(tag, attrs, parent)
        parent.children.append(element)
        if tag not in VOID:
            self._open.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # ``<div/>`` is not self-closing in HTML; ``<input/>`` is void anyway
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        # Close up to the matching open element; a stray end tag is dropped, as a browser drops it
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        parent = self._open[-1]
        # The parser drops a newline right after <textarea>, <pre> and <listing>
        if parent.tag in ("textarea", "pre", "listing") and not parent.children and data.startswith("\n"):
            data = data[1:]
        if data:
            parent.children.append(data)


def parse(html: str) -> Element:
    """The document a browser builds from ``html``, for the well-formed markup a template renders."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def parse_item(html: str) -> Element | None:
    """``_parseHtml`` in wireview.js: the first element of ``html`` parsed in a ``<template>``, or ``None``."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    first = next((c for c in builder.root.children if isinstance(c, Element)), None)
    if first is not None:
        first.parent = None
    return first


def element_children(el: Element) -> list[Element]:
    """``el.children``: its elements, without the text between them."""
    return [c for c in el.children if isinstance(c, Element)]


# ============================================================================
# Streams: _handleStreamOp in wireview.js, and streams.mjs
# ============================================================================


def plan_insert(*, exists: bool, at: int, child_count: int, update_only: bool = False, placed: int = 0) -> dict:
    """``planInsert`` in streams.mjs: where an incoming item goes."""
    if exists:
        return {"mode": "replace"}
    if update_only:
        return {"mode": "skip"}
    if at == -1:
        return {"mode": "append"}
    index = at + placed
    if index == 0:
        return {"mode": "prepend"}
    if index >= child_count:
        return {"mode": "append"}
    return {"mode": "before", "index": index}


def plan_trim(*, child_count: int, limit: int, at: int) -> dict:
    """``planTrim`` in streams.mjs: how many items to drop once a limit is exceeded, and from which end."""
    if not limit or limit <= 0 or child_count <= limit:
        return {"count": 0, "fromEnd": False}
    return {"count": child_count - limit, "fromEnd": at == 0}


def stream_container(root: Element, stream: str, owner: str | None) -> Element | None:
    """``_streamContainer``: the owner's own ``wire-stream`` container of that name, else the first inside it."""

    def named(el: Element) -> bool:
        return el.get("wire-stream") == stream

    if owner is None:
        return next((d for d in root.descendants() if named(d)), None)
    owner_el = next((d for d in root.descendants() if d.get("id") == owner), None)
    if owner_el is None:
        return None
    if named(owner_el):
        return owner_el
    inside = [d for d in owner_el.descendants() if named(d)]
    return next((c for c in inside if c.closest(is_component) is owner_el), inside[0] if inside else None)


def _adopt(parent: Element, el: Element, index: int | None = None) -> None:
    if el.parent is not None:
        el.parent.children.remove(el)
    el.parent = parent
    if index is None:
        parent.children.append(el)
    else:
        parent.children.insert(index, el)


def _remove(el: Element) -> None:
    if el.parent is not None:
        el.parent.children.remove(el)
        el.parent = None


def _by_id_inside(container: Element, id_: str) -> Element | None:
    """``container.querySelector(#id)``: a descendant, not the container itself."""
    return next((d for d in container.descendants() if d.get("id") == id_), None)


def apply_stream_op(container: Element, op: t.Mapping[str, t.Any]) -> None:
    """``_applyStreamOp``: put one stream operation's items into ``container`` as the client does."""
    kind = op.get("op")
    items = op.get("items") or []
    at = op.get("at", -1)
    limit = op.get("limit") or 0
    if kind == "reset":
        container.children = []
        for item in items:
            if (el := parse_item(item.get("html", ""))) is not None:
                el.attrs["id"] = item["id"]
                _adopt(container, el)
    elif kind == "insert":
        placed = 0
        for item in items:
            el = parse_item(item.get("html", ""))
            if el is None:
                continue
            el.attrs["id"] = item["id"]
            existing = _by_id_inside(container, item["id"])
            plan = plan_insert(
                exists=existing is not None,
                at=at,
                child_count=len(element_children(container)),
                update_only=op.get("update_only") is True,
                placed=placed,
            )
            mode = plan["mode"]
            if mode not in ("replace", "skip"):
                placed += 1
            if mode == "replace":
                assert existing is not None and existing.parent is not None
                parent = existing.parent
                _adopt(parent, el, parent.children.index(existing))
                _remove(existing)
            elif mode == "prepend":
                _adopt(container, el, 0)
            elif mode == "append":
                _adopt(container, el)
            elif mode == "before":
                sibling = element_children(container)[plan["index"]]
                _adopt(container, el, container.children.index(sibling))
    elif kind == "delete":
        for item in items:
            if (found := _by_id_inside(container, item["id"])) is not None:
                _remove(found)
    trim = plan_trim(child_count=len(element_children(container)), limit=limit, at=at)
    for _ in range(trim["count"]):
        children = element_children(container)
        if children:
            _remove(children[-1] if trim["fromEnd"] else children[0])


def apply_stream_ops(root: Element, ops: t.Iterable[t.Mapping[str, t.Any]]) -> None:
    """Every stream operation sent so far, in order, on the containers of ``root``.

    One whose container is not on the page is dropped, as the client drops it
    once its wait for the container ends. That wait (``TargetQueue``) holds the
    same component's later operations for a frame, while another component's
    go on: of several messages that arrive in one frame the client may apply
    them in another order. The helpers keep the order sent.
    """
    for op in ops:
        container = stream_container(root, op.get("stream", ""), op.get("id"))
        if container is not None:
            apply_stream_op(container, op)


# ============================================================================
# Selectors
# ============================================================================

# Non-ASCII letters, as CSS allows in a name (regex escapes, not the characters)
_NON_ASCII = r"\u00a0-\uffff"
_IDENT = rf"-?[A-Za-z_{_NON_ASCII}][\w{_NON_ASCII}-]*"
_ATTR_NAME = r"[A-Za-z_:][\w:.-]*"
_TOKEN = re.compile(
    rf"""
    (?P<space>\s*(?P<comb>[>,])\s*|\s+)
  | (?P<tag>{_IDENT}|\*)
  | \#(?P<id>[\w{_NON_ASCII}-]+)
  | \.(?P<cls>{_IDENT})
  | \[\s*(?P<attr>{_ATTR_NAME})\s*
       (?:(?P<op>[~^$*|]?=)\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s\]]+))\s*)?
    \]
    """,
    re.VERBOSE,
)


@dataclasses.dataclass
class _Compound:
    tag: str | None = None
    ids: list[str] = dataclasses.field(default_factory=list)
    classes: list[str] = dataclasses.field(default_factory=list)
    attrs: list[tuple[str, str | None, str | None]] = dataclasses.field(default_factory=list)

    def matches(self, el: Element) -> bool:
        if self.tag not in (None, "*") and el.tag != self.tag:
            return False
        if any(el.get("id") != i for i in self.ids):
            return False
        classes = (el.get("class") or "").split()
        if any(c not in classes for c in self.classes):
            return False
        for name, op, expected in self.attrs:
            value = el.get(name)
            if value is None:
                return False
            if op is None:
                continue
            assert expected is not None
            if op == "=" and value != expected:
                return False
            if op == "~=" and expected not in value.split():
                return False
            if op == "^=" and not (expected and value.startswith(expected)):
                return False
            if op == "$=" and not (expected and value.endswith(expected)):
                return False
            if op == "*=" and not (expected and expected in value):
                return False
            if op == "|=" and not (value == expected or value.startswith(expected + "-")):
                return False
        return True


# A complex selector: compounds and the combinators between them, read right to left
_Complex = list[tuple[str, _Compound]]  # (combinator to the left: "" | " " | ">", compound)


class Selector:
    """The part of CSS a test needs to name an element: see ``docs/features/testing.md``."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._groups: list[_Complex] = []
        complex_: _Complex = []
        compound: _Compound | None = None
        combinator = ""
        position = 0
        text = source.strip()
        if not text:
            raise ValueError("an empty selector selects nothing")
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None:
                raise ValueError(
                    f"{source!r}: the test helpers read tags, #id, .class, [attr], [attr=value] (also ~= ^= $= *= |=), "
                    f"descendant and child (>) combinators and commas; {text[position:]!r} is not among them"
                )
            position = match.end()
            if match.group("space") is not None:
                comb = match.group("comb") or " "
                if compound is None:
                    if comb == " " and not complex_:
                        continue
                    raise ValueError(f"{source!r}: {comb!r} has nothing on its left")
                complex_.append((combinator, compound))
                compound = None
                if comb == ",":
                    self._groups.append(complex_)
                    complex_, combinator = [], ""
                else:
                    combinator = comb
                continue
            if compound is None:
                compound = _Compound()
            if match.group("tag") is not None:
                if compound.tag is not None or compound.ids or compound.classes or compound.attrs:
                    raise ValueError(f"{source!r}: a tag name comes first in a compound selector")
                compound.tag = match.group("tag").lower()
            elif match.group("id") is not None:
                compound.ids.append(match.group("id"))
            elif match.group("cls") is not None:
                compound.classes.append(match.group("cls"))
            else:
                op = match.group("op")
                value = next(
                    (v for v in (match.group("dq"), match.group("sq"), match.group("bare")) if v is not None), None
                )
                compound.attrs.append((match.group("attr").lower(), op, value))
        if compound is None:
            raise ValueError(f"{source!r} ends with a combinator")
        complex_.append((combinator, compound))
        self._groups.append(complex_)

    def matches(self, el: Element) -> bool:
        return any(self._matches(group, len(group) - 1, el) for group in self._groups)

    def _matches(self, group: _Complex, index: int, el: Element) -> bool:
        combinator, compound = group[index]
        if not compound.matches(el):
            return False
        if index == 0:
            return True
        if combinator == ">":
            return el.parent is not None and el.parent.tag != "#document" and self._matches(group, index - 1, el.parent)
        return any(self._matches(group, index - 1, a) for a in el.ancestors() if a.tag != "#document")

    def select(self, root: Element) -> list[Element]:
        """Every element under ``root`` it matches, in document order (``querySelectorAll``)."""
        return [el for el in root.descendants() if self.matches(el)]


def normalized_text(el: Element) -> str:
    return " ".join(el.text.split())


# ============================================================================
# Bindings: events.mjs, line for line
# ============================================================================

BINDING_PREFIX = "wire-on-"
#: Bindings that listen beyond their element (``SCOPE_PREFIXES`` in events.mjs, #234)
SCOPE_PREFIXES = {"window": "wire-window-", "away": "wire-away-"}

#: The ``wire-auto-recover`` value that turns a form's recovery off (#245). The
#: client's is ``RECOVERY_OFF`` in static/wireview/recovery.mjs; ``ignore`` stays a handler's name.
RECOVERY_OFF = "false"
#: Modifiers that take the next token as their argument (``ARITY`` in events.mjs)
ARITY = frozenset({"debounce", "throttle", "key", "key_code"})
#: Key shortcuts and the ``event.key`` (lowercased) each stands for (``KEYS`` in events.mjs)
KEYS = {
    "enter": "enter",
    "tab": "tab",
    "delete": "delete",
    "backspace": "backspace",
    "esc": "escape",
    "space": " ",
    "up": "arrowup",
    "down": "arrowdown",
    "left": "arrowleft",
    "right": "arrowright",
}


@dataclasses.dataclass(frozen=True)
class Step:
    name: str
    arg: str | None = None


@dataclasses.dataclass(frozen=True)
class Binding:
    type: str
    steps: tuple[Step, ...]
    #: ``element``, ``window`` or ``away``
    scope: str = "element"


def _prefix_of(scope: str) -> str:
    return BINDING_PREFIX if scope == "element" else SCOPE_PREFIXES[scope]


def is_binding_name(name: str) -> bool:
    """``isBindingName``: a binding of any scope."""
    return name.startswith(BINDING_PREFIX) or any(name.startswith(p) for p in SCOPE_PREFIXES.values())


def belongs_to_ime(event: t.Mapping[str, t.Any]) -> bool:
    """``belongsToIme``: a key an IME composes with, or the one that starts a composition (keyCode 229)."""
    return bool(event.get("isComposing")) or event.get("keyCode") == 229


def parse_binding(attribute: str) -> Binding | None:
    """``parseBinding``: the event and the modifiers a binding attribute's name says."""
    if attribute.startswith(BINDING_PREFIX):
        scope = "element"
    elif attribute.startswith(SCOPE_PREFIXES["window"]):
        scope = "window"
    elif attribute.startswith(SCOPE_PREFIXES["away"]):
        scope = "away"
    else:
        return None
    type_, *tokens = attribute[len(_prefix_of(scope)) :].split(".")
    if not type_:
        return None
    steps: list[Step] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in KEYS:
            steps.append(Step("key", KEYS[token]))
        elif token in ARITY:
            i += 1
            # tokens[++i] is undefined past the end
            steps.append(Step(token, tokens[i] if i < len(tokens) else None))
        else:
            steps.append(Step(token))
        i += 1
    return Binding(type_, tuple(steps), scope)


def bindings_for(names: t.Iterable[str], type_: str, scope: str = "element") -> list[str]:
    """``bindingsFor``: the attributes among ``names`` that answer to an event ``type_``, in order."""
    exact = _prefix_of(scope) + type_
    return [name for name in names if name == exact or name.startswith(exact + ".")]


class StepOps(t.Protocol):
    def prevent(self) -> None: ...
    def stop(self) -> None: ...
    def debounce(self, delay: float, rest: t.Callable[[], Step | None]) -> None: ...
    def throttle(self, delay: float) -> bool: ...
    def fire(self) -> None: ...


def _number(arg: str | None) -> float:
    # Number(arg): NaN for what is not a number, undefined included
    try:
        return float(arg) if arg is not None and arg.strip() else (0.0 if arg is not None else float("nan"))
    except ValueError:
        return float("nan")


def _js_string(value: t.Any) -> str:
    # String(x) for what an event field holds: undefined, a string or a number
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def run_steps(steps: t.Sequence[Step], event: t.Mapping[str, t.Any], ops: StepOps, start: int = 0) -> Step | None:
    """``runSteps``: run a binding's modifiers against an event, then fire it.

    Returns the condition that stopped the run, if one did -- what the
    client does not say and an error here does.
    """
    for i in range(start, len(steps)):
        step = steps[i]
        name, arg = step.name, step.arg
        if name == "prevent":
            ops.prevent()
        elif name == "stop":
            ops.stop()
        elif name in ("ctrl", "alt", "shift", "meta"):
            if not event.get(f"{name}Key"):
                return step
        elif name == "key":
            if event.get("isComposing"):
                return step
            if _js_string(event.get("key")).lower() != _js_string(arg).lower():
                return step
        elif name == "key_code":
            if event.get("isComposing"):
                return step
            if _js_string(event.get("keyCode")) != _js_string(arg):
                return step
        elif name == "debounce":
            ops.debounce(_number(arg), lambda i=i: run_steps(steps, event, ops, i + 1))
            return None
        elif name == "throttle":
            if not ops.throttle(_number(arg)):
                return step
        # An unknown modifier is skipped, as the client skips it
    ops.fire()
    return None


# ============================================================================
# Fields: Component.serialize in wireview.js
# ============================================================================

_TEXT_TYPES_WITHOUT_VALUE = frozenset({"checkbox", "radio"})
_INPUT_TYPES = frozenset(
    {
        "hidden", "text", "search", "tel", "url", "email", "password", "date", "month", "week", "time",
        "datetime-local", "number", "range", "color", "checkbox", "radio", "file", "submit", "image",
        "reset", "button",
    }
)  # fmt: skip


def input_type(el: Element) -> str:
    """``HTMLInputElement.type``: the attribute lowercased, ``text`` when missing or unknown."""
    value = (el.get("type") or "").lower()
    return value if value in _INPUT_TYPES else "text"


def field_type(el: Element) -> str:
    """``el.type?.toLowerCase() || ""`` for the elements ``serialize`` meets."""
    if el.tag == "input":
        return input_type(el)
    if el.tag == "select":
        return "select-multiple" if el.has("multiple") else "select-one"
    if el.tag == "textarea":
        return "textarea"
    if el.tag == "button":
        value = (el.get("type") or "").lower()
        return value if value in ("submit", "reset", "button") else "submit"
    if el.tag in ("fieldset", "output"):
        return el.tag
    return ""


def option_value(option: Element) -> str:
    """``HTMLOptionElement.value``: its attribute, or its text with whitespace collapsed."""
    value = option.get("value")
    return value if value is not None else " ".join(option.text.split())


def options_of(select: Element) -> list[Element]:
    return [el for el in select.descendants() if el.tag == "option"]


class Fields:
    """The fields' state as the user left it: the render's, with what the test typed and ticked on top."""

    def __init__(self) -> None:
        self._values: dict[int, str] = {}
        self._checked: dict[int, bool] = {}
        self._selected: dict[int, list[str]] = {}

    def value(self, el: Element) -> str:
        """``.value`` of an input, textarea or select-one."""
        if id(el) in self._values:
            return self._values[id(el)]
        if el.tag == "textarea":
            return el.text
        if el.tag == "select":
            selected = self.selected(el)
            return selected[0] if selected else ""
        value = el.get("value")
        if value is None:
            return "on" if el.tag == "input" and input_type(el) in _TEXT_TYPES_WITHOUT_VALUE else ""
        return value

    def checked(self, el: Element) -> bool:
        if id(el) in self._checked:
            return self._checked[id(el)]
        return el.has("checked")

    def selected(self, select: Element) -> list[str]:
        """The values of the selected options, in order."""
        return [option_value(option) for option in self.selected_options(select)]

    def selected_options(self, select: Element) -> list[Element]:
        """Keep option identity and tree order, including options with equal values."""
        options = options_of(select)
        multiple = select.has("multiple")
        if id(select) in self._selected:
            requested = set(self._selected[id(select)])
            matching = [
                option
                for option in options
                if option_value(option) in requested
                and not option.has("disabled")
                and not _in_disabled_optgroup(option)
            ]
            return matching if multiple else matching[:1]
        chosen = [option for option in options if option.has("selected")]
        if multiple or chosen:
            return chosen if multiple else chosen[-1:]
        return [option for option in options if not option.has("disabled") and not _in_disabled_optgroup(option)][:1]

    def typed(self, el: Element) -> bool:
        """Whether the test set the value of ``el``, rather than the render."""
        return id(el) in self._values

    def set_value(self, el: Element, value: str) -> None:
        self._values[id(el)] = value

    def set_checked(self, el: Element, checked: bool, root: Element) -> None:
        self._checked[id(el)] = checked
        if checked and el.tag == "input" and input_type(el) == "radio" and (name := el.get("name")):
            # Checking a radio unchecks the others of its group (same form, same name)
            form = form_of(el, root)
            for other in root.descendants():
                if (
                    other is not el
                    and other.tag == "input"
                    and input_type(other) == "radio"
                    and other.get("name") == name
                    and form_of(other, root) is form
                ):
                    self._checked[id(other)] = False

    def set_selected(self, select: Element, values: list[str]) -> None:
        self._selected[id(select)] = values
        self._values.pop(id(select), None)

    def forget(self, el: Element) -> None:
        """A form reset: the field holds what the render drew again."""
        for held in (self._values, self._checked, self._selected):
            held.pop(id(el), None)


def _in_disabled_optgroup(option: Element) -> bool:
    return option.parent is not None and option.parent.tag == "optgroup" and option.parent.has("disabled")


def _non_field_value(el: Element) -> t.Any:
    """``el.value`` of an element that is not an input, select, textarea or button: what JSON makes of it."""
    if el.tag == "output":
        return el.text
    if el.tag == "data":
        return el.get("value") or ""
    if el.tag == "option":
        return option_value(el)
    if el.tag == "li":
        try:
            return int(el.get("value") or 0)
        except ValueError:
            return 0
    if el.tag in ("meter", "progress"):
        try:
            number = float(el.get("value") or 0)
        except ValueError:
            number = 0.0
        return int(number) if number.is_integer() else number
    # undefined, which JSON.stringify writes as null in an array
    return None


def is_component(el: Element) -> bool:
    return el.has("wireview-component")


def serialize(
    scope: Element, component: Element, fields: Fields, root: Element, submitter: Element | None = None
) -> dict[str, list[t.Any]]:
    """``Component.serialize(scope, submitter)`` of the component whose element is ``component`` (#244).

    What a browser submits of a form, and the other named elements in it: the
    controls the form owns -- a ``form="id"`` one outside it included, one in it
    that names another form left out -- not a disabled one nor one in a disabled
    fieldset, a button only when it is ``submitter``. A component outside a form
    sends what is in it by the same rules. A nested component's fields are its
    own. Checkboxes and radios go when checked (their value, or ``true`` when it
    is empty), a select-multiple's chosen values as one list.
    """
    result: dict[str, list[t.Any]] = {}
    for el in _named_in(scope, root):
        if el.closest(is_component) is not component:
            continue
        if el.tag in _DISABLEABLE and disabled_by(el) is not None:
            continue
        key = el.get("name")
        if not key:
            continue
        kind = field_type(el)
        value: t.Any
        if _is_button_control(el):
            if el is not submitter:
                continue
            if kind == "image":
                # A key or a script clicks at 0, 0
                result.setdefault(f"{key}.x", []).append("0")
                result.setdefault(f"{key}.y", []).append("0")
                continue
            value = el.get("value") or ""
        elif kind in ("checkbox", "radio"):
            value = (fields.value(el) or True) if fields.checked(el) else None
            if value is None:
                continue
        elif kind == "select-multiple":
            value = fields.selected(el)
        elif el.tag in ("input", "select", "textarea"):
            value = "" if _bad_input(el, fields) else fields.value(el)
        else:
            value = _non_field_value(el)
        result.setdefault(key, []).append(value)
    return result


#: HTML's valid floating-point number: what a number field's value can be
_FLOAT = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")


def _bad_input(el: Element, fields: Fields) -> bool:
    """``el.validity.badInput``: a number field the test typed something into that is not a number.

    The browser keeps the text on screen and gives ``""`` as the value, so the
    form sends ``""`` (#244). Only what was typed: the render's own ``value``
    attribute is sanitized away without a flag.
    """
    if not (el.tag == "input" and input_type(el) == "number" and fields.typed(el)):
        return False
    value = fields.value(el)
    return value != "" and _FLOAT.fullmatch(value) is None


def event_meta(
    scope: Element, component: Element, fields: Fields, root: Element, touched: Touched, source: Element | None
) -> dict[str, t.Any]:
    """``Component.eventMeta(scope, source)`` in wireview.js (#244): what an event says beyond its fields.

    The fields are the named inputs, selects and textareas the event sends --
    not a hidden input nor a button, not a disabled one, not a nested
    component's. ``target`` is ``source``'s name when it is one of them;
    ``unused`` the names nobody touched in their form, ``target`` aside;
    ``bad_input`` the number fields holding what is not a number.
    """
    named = [
        el
        for el in _named_in(scope, root)
        if el.closest(is_component) is component
        and el.tag in ("input", "select", "textarea")
        and not _is_button_control(el)
        and not (el.tag == "input" and input_type(el) == "hidden")
        and disabled_by(el) is None
        and el.get("name")
    ]
    target = source.get("name") if source is not None and any(el is source for el in named) else None
    unused: dict[str, None] = {}
    bad: dict[str, None] = {}
    for el in named:
        name = el.get("name") or ""
        if name != target and not touched.has(name, form_of(el, root), root):
            unused[name] = None
        if _bad_input(el, fields):
            bad[name] = None
    return {"target": target, "unused": list(unused), "bad_input": list(bad)}


class Touched:
    """``FeedbackManager.touched``: the names touched in each form, kept from one action to the next (#244).

    A form is known by its ``id``, or by its place among the page's forms: the
    helpers parse each render anew, where the browser's morph keeps the element.
    """

    def __init__(self) -> None:
        self._names: dict[t.Any, set[str]] = {}

    @staticmethod
    def _key(form: Element | None, root: Element) -> t.Any:
        if form is None:
            return None
        if form.get("id"):
            return ("id", form.get("id"))
        forms = [d for d in root.descendants() if d.tag == "form"]
        return ("at", next(i for i, f in enumerate(forms) if f is form))

    @staticmethod
    def _field_name(el: Element) -> str | None:
        # FeedbackManager.isFormInput and getFieldName
        if el.tag not in ("input", "select", "textarea"):
            return None
        return el.get("name") or el.get("id") or None

    def touch(self, el: Element, root: Element) -> None:
        if (name := self._field_name(el)) is not None:
            self._names.setdefault(self._key(form_of(el, root), root), set()).add(name)

    def touch_form(self, form: Element, root: Element) -> None:
        for el in root.descendants():
            if el.tag in _LISTED and form_of(el, root) is form and (name := self._field_name(el)) is not None:
                self._names.setdefault(self._key(form, root), set()).add(name)

    def reset(self, form: Element, root: Element) -> None:
        self._names.pop(self._key(form, root), None)

    def has(self, name: str, form: Element | None, root: Element) -> bool:
        return name in self._names.get(self._key(form, root), ())


#: Elements with a form owner (HTML's listed elements)
_LISTED = ("button", "fieldset", "input", "object", "output", "select", "textarea")
#: Elements ``:disabled`` can match among the named ones a form sends
_DISABLEABLE = ("button", "fieldset", "input", "select", "textarea")


def _named_in(scope: Element, root: Element) -> list[Element]:
    """``namedIn`` in wireview.js: the named elements ``scope`` sends, in document order."""
    if scope.tag != "form":
        return [d for d in scope.descendants() if d.has("name")]
    return [
        d
        for d in root.descendants()
        if d is not scope and d.has("name") and (form_of(d, root) is scope if d.tag in _LISTED else scope.contains(d))
    ]


def _is_button_control(el: Element) -> bool:
    if el.tag == "button":
        return True
    return el.tag == "input" and input_type(el) in ("submit", "image", "reset", "button")


def form_of(el: Element, root: Element) -> Element | None:
    """``el.form`` of a form control: its ``form`` attribute's form, or the form it is in."""
    if (form_id := el.get("form")) is not None:
        return next((f for f in root.descendants() if f.tag == "form" and f.get("id") == form_id), None)
    return el.closest(lambda a: a.tag == "form")


def disabled_by(el: Element) -> Element | None:
    """What makes ``el`` disabled for the user: its own ``disabled``, a disabled button it is in, or a disabled
    fieldset it is in outside that fieldset's first legend. ``None`` when nothing does."""
    if el.tag in ("button", "input", "select", "textarea", "option", "optgroup") and el.has("disabled"):
        return el
    for ancestor in el.ancestors():
        if ancestor.tag == "button" and ancestor.has("disabled"):
            return ancestor
        if ancestor.tag == "fieldset" and ancestor.has("disabled"):
            legend = next((c for c in ancestor.children if isinstance(c, Element) and c.tag == "legend"), None)
            if legend is None or not legend.contains(el):
                return ancestor
    return None


_DISPLAY_NONE = re.compile(r"(?:^|;)\s*display\s*:\s*none\s*(?:!important\s*)?(?:;|$)", re.I)


def is_shown(el: Element) -> bool:
    """``isShown`` in wireview.js, as far as the markup says: no ``hidden`` and no inline ``display: none`` on
    ``el`` or an ancestor, not in a closed ``<dialog>`` or (but for its summary) a closed ``<details>``, not a
    hidden input or a ``<template>``'s content. A stylesheet that hides it is not seen here; the browser's
    ``checkVisibility()`` sees it."""
    if el.tag == "input" and input_type(el) == "hidden":
        return False
    child = None
    for node in [el, *el.ancestors()]:
        if node.tag == "template" or node.has("hidden") or _DISPLAY_NONE.search(node.get("style") or ""):
            return False
        # The browser's own stylesheet hides a closed dialog, and a closed details but for its summary
        if node.tag == "dialog" and not node.has("open"):
            return False
        if node.tag == "details" and not node.has("open") and child is not None:
            summary = next((c for c in node.children if isinstance(c, Element) and c.tag == "summary"), None)
            if child is not summary:
                return False
        child = node
    return True


def is_reset_button(el: Element, root: Element) -> bool:
    """A reset button with a form: its click resets that form."""
    return el.tag in ("button", "input") and field_type(el) == "reset" and form_of(el, root) is not None


def is_submitter(el: Element, root: Element) -> bool:
    """``isSubmitter`` in wireview.js: a submit button with a form."""
    if el.tag == "button":
        return field_type(el) == "submit" and form_of(el, root) is not None
    if el.tag == "input":
        return input_type(el) in ("submit", "image") and form_of(el, root) is not None
    return False


# ============================================================================
# Dispatch
# ============================================================================


@dataclasses.dataclass
class ServerEvent:
    """What the client sends for a binding that fired: ``user_event``'s fields."""

    component_id: str
    handler: str
    implicit_args: dict[str, list[t.Any]]
    explicit_args: dict[str, t.Any]
    event_type: str
    element: Element
    #: ``Component.eventMeta``: the field the event came from, the unused fields, the unreadable ones (#244)
    meta: dict[str, t.Any] = dataclasses.field(default_factory=dict)


#: The events the helpers dispatch that do not bubble
NON_BUBBLING = frozenset({"focus", "blur"})

#: ``KeyboardEvent.keyCode`` of the keys that have one apart from their character
KEY_CODES = {
    "Backspace": 8, "Tab": 9, "Enter": 13, "Shift": 16, "Control": 17, "Alt": 18, "Pause": 19, "CapsLock": 20,
    "Escape": 27, " ": 32, "PageUp": 33, "PageDown": 34, "End": 35, "Home": 36, "ArrowLeft": 37, "ArrowUp": 38,
    "ArrowRight": 39, "ArrowDown": 40, "Insert": 45, "Delete": 46, "Meta": 91,
    **{f"F{n}": 111 + n for n in range(1, 13)},
}  # fmt: skip


def key_event(
    key: str, *, ctrl: bool = False, alt: bool = False, shift: bool = False, meta: bool = False, composing: bool = False
) -> dict[str, t.Any]:
    """The fields of a ``KeyboardEvent`` ``runSteps`` reads, as a browser fills them for ``key``.

    ``keyCode`` is the legacy code: a letter's is its capital's, a digit's its
    own, a named key's from :data:`KEY_CODES`, 0 for the rest -- and 229 while
    an IME composes, as Chromium gives it.
    """
    if composing:
        code = 229
    elif len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    else:
        code = KEY_CODES.get(key, 0)
    return {
        "key": key,
        "keyCode": code,
        "isComposing": composing,
        **modifier_keys(ctrl=ctrl, alt=alt, shift=shift, meta=meta),
    }


def modifier_keys(*, ctrl: bool, alt: bool, shift: bool, meta: bool) -> dict[str, bool]:
    return {"ctrlKey": ctrl, "altKey": alt, "shiftKey": shift, "metaKey": meta}


class DomError(AssertionError):
    """The test asked for what a user cannot do on this page, or what reaches no handler."""


@dataclasses.dataclass
class _Skipped:
    element: Element
    attribute: str
    why: str


@dataclasses.dataclass
class _EventState:
    """One dispatched event: whether its default was prevented, and its propagation stopped at this element."""

    prevented: bool = False
    stopped: bool = False
    #: A debounced run goes on after the dispatch, when prevent and stop no longer matter
    late: bool = False
    #: The submit's ``event.submitter``
    submitter: Element | None = None
    #: ``event.target``: where the event was dispatched
    source: Element | None = None


class _BindingRun:
    """What ``runSteps``' callbacks do for one binding in ``EventBindings.run``."""

    def __init__(
        self,
        action: _Dispatch,
        state: _EventState,
        el: Element,
        attribute: str,
        value: dict[str, t.Any],
        type_: str,
        beyond: bool = False,
    ) -> None:
        self.action, self.state, self.el, self.attribute, self.value, self.type = (
            action,
            state,
            el,
            attribute,
            value,
            type_,
        )
        #: A ``window`` or ``away`` binding: the event happened elsewhere, and it submits nothing of its own
        self.beyond = beyond

    def prevent(self) -> None:
        if not self.state.late:
            self.state.prevented = True

    def stop(self) -> None:
        if not self.state.late:
            self.state.stopped = True

    def debounce(self, delay: float, rest: t.Callable[[], Step | None]) -> None:
        def later() -> None:
            self.state.late = True
            if (failed := rest()) is not None:
                self.action.skip(self.el, self.attribute, failed)

        timers = self.action.timers
        timers.append((delay if delay == delay else 0.0, len(timers), later))

    def throttle(self, delay: float) -> bool:
        # Each action here comes after any throttle window has passed
        return True

    def fire(self) -> None:
        if self.beyond:
            self.action.fire(self.el, self.value, self.type, beyond=True)
            return
        # The button that submitted: a submit's, or the submit button clicked (EventBindings.run, #244)
        submitter = self.state.submitter if self.type == "submit" else None
        if self.type == "click" and is_submitter(self.el, self.action.page.root):
            submitter = self.el
        self.action.fire(self.el, self.value, self.type, submitter, self.state.source)


class _Dispatch:
    """One user action: the events it dispatches and what the server gets from each."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.sent: list[ServerEvent] = []
        self.skipped: list[_Skipped] = []
        # The events dispatched, with the element each was dispatched at
        self.dispatched: list[tuple[str, Element]] = []
        # Debounced runs, in the order their timers end (delay, then when they were set)
        self.timers: list[tuple[float, int, t.Callable[[], None]]] = []

    def dispatch(
        self,
        target: Element | None,
        type_: str,
        event: t.Mapping[str, t.Any] | None = None,
        submitter: Element | None = None,
        own: bool = False,
    ) -> bool:
        """Dispatch ``type_`` at ``target``, bubbling (``EventBindings.bubble``). Whether its default was prevented.

        An event that does not bubble (``focus``, ``blur``) runs its target's
        bindings alone: the client's capture listener on the root. ``submitter``
        is a submit's ``event.submitter``. A ``target`` of None is the page
        outside the render: a key with nothing focused.

        The ``away`` bindings outside ``target`` hear it first (the window's
        capture phase), and the ``window`` bindings last, unless an element
        binding stopped it or it does not bubble (#234). ``own`` is an event
        wireview dispatches for a form (``dispatchOwn``): those hear none of it.
        """
        event = event or {}
        state = _EventState(submitter=submitter, source=target)
        if target is not None:
            self.dispatched.append((type_, target))
        beyond = not own and not belongs_to_ime(event)
        if beyond:
            away = [el for el in self._carrying("away", type_) if (target is None or not el.contains(target))]
            for el in [el for el in away if is_shown(el)]:
                self._run(el, type_, event, state, "away")
        if target is not None:
            # FeedbackManager's capture listeners on the document hear it after the window's
            self.touch(type_, target)
        if target is None:
            path: list[Element] = []
        elif type_ in NON_BUBBLING:
            path = [target]
        else:
            path = [target, *(a for a in target.ancestors() if a.tag != "#document")]
        stopped = False
        for el in path:
            state.stopped = False
            self._run(el, type_, event, state, "element")
            if state.stopped:
                stopped = True
                break
        if beyond and not stopped and type_ not in NON_BUBBLING:
            for el in self._carrying("window", type_):
                self._run(el, type_, event, state, "window")
        return state.prevented

    def _carrying(self, scope: str, type_: str) -> list[Element]:
        """The elements of the page with a ``scope`` binding for ``type_``, in document order."""
        root = self.page.root
        if root is None:
            return []
        # querySelectorAll does not look into a <template>'s content
        return [
            el
            for el in root.descendants()
            if bindings_for(el.attrs, type_, scope) and el.closest(lambda a: a.tag == "template") is None
        ]

    def _run(self, el: Element, type_: str, event: t.Mapping[str, t.Any], state: _EventState, scope: str) -> None:
        """``EventBindings.run``: ``el``'s ``scope`` bindings for ``type_``."""
        for attribute in bindings_for(el.attrs, type_, scope):
            binding = parse_binding(attribute)
            if binding is None:
                continue
            try:
                value = json.loads(el.attrs[attribute] or "{}")
            except ValueError:
                # The client logs it and goes on
                self.skipped.append(_Skipped(el, attribute, "its value is not JSON"))
                continue
            if isinstance(value, dict):
                run = _BindingRun(self, state, el, attribute, value, type_, beyond=scope != "element")
                if (failed := run_steps(binding.steps, event, run)) is not None:
                    self.skip(el, attribute, failed)

    def touch(self, type_: str, target: Element) -> None:
        """What ``type_`` at ``target`` touches (FeedbackManager.init): a field left or changed, a submit's form."""
        page = self.page
        if type_ in ("blur", "change"):
            page.touched.touch(target, page.root)
        elif type_ == "submit" and target.tag == "form":
            page.touched.touch_form(target, page.root)

    def run_timers(self) -> None:
        while self.timers:
            self.timers.sort(key=lambda timer: (timer[0], timer[1]))
            _, _, later = self.timers.pop(0)
            later()

    def skip(self, el: Element, attribute: str, failed: Step) -> None:
        modifier = failed.name if failed.arg is None else f"{failed.name} {failed.arg!r}"
        self.skipped.append(_Skipped(el, attribute, f"its {modifier} modifier does not hold for this event"))

    def fire(
        self,
        el: Element,
        value: dict[str, t.Any],
        type_: str,
        submitter: Element | None = None,
        source: Element | None = None,
        beyond: bool = False,
    ) -> None:
        if "js" in value:
            pushes = [c for c in value["js"] or [] if isinstance(c, dict) and c.get("cmd") == "push"]
            if pushes:
                raise DomError(
                    f"{el.describe()} runs a JS() chain on {type_} that pushes {pushes[0].get('event')!r}: "
                    "the test helpers do not run JavaScript, so they cannot say what that push sends. Test the "
                    "handler with call(), and the chain in a browser."
                )
            self.skipped.append(
                _Skipped(el, f"{BINDING_PREFIX}{type_}", "it runs a JS() chain, which the helpers do not run")
            )
            return
        handler = value.get("h")
        if handler is None:
            return
        self.sent.append(
            self.page.server_event(el, handler, value.get("a") or {}, value.get("t"), type_, submitter, source, beyond)
        )


class Page:
    """The mounted component's render, as the browser holds it, and what each user action sends from it."""

    def __init__(
        self,
        html: str,
        component_id: str,
        stream_ops: t.Iterable[t.Mapping[str, t.Any]] = (),
        touched: Touched | None = None,
    ) -> None:
        self.html = html
        self.root = parse(html)
        # The render carries its stream containers empty; the page has the items the operations put there
        apply_stream_ops(self.root, stream_ops)
        self.component_id = component_id
        self.fields = Fields()
        # What the user touched outlives the render, as it does in the browser
        self.touched = touched if touched is not None else Touched()

    # -- finding ------------------------------------------------------------

    def find(self, selector: str, text: str | re.Pattern[str] | None = None, *, what: str = "element") -> Element:
        matches = Selector(selector).select(self.root)
        found = matches
        if text is not None:
            found = [el for el in matches if _text_matches(el, text)]
        if len(found) == 1:
            return found[0]
        label = f"{selector!r}" + (
            f" with text {text.pattern if isinstance(text, re.Pattern) else text!r}" if text is not None else ""
        )
        if not found:
            if matches:
                listed = "\n".join(f"  {el.describe()}" for el in matches[:10])
                raise DomError(f"No {what} matches {label}. These match the selector without the text:\n{listed}")
            raise DomError(f"No {what} matches {label} in the render of {self.component_id}.")
        listed = "\n".join(f"  {el.describe()}" for el in found[:10])
        more = f"\n  ...and {len(found) - 10} more" if len(found) > 10 else ""
        raise DomError(
            f"{len(found)} elements match {label}; the helpers act on one. Narrow the selector "
            f"(an id, [data-testid=...], a parent) or pass text=:\n{listed}{more}"
        )

    # -- what an event sends ------------------------------------------------

    def server_event(
        self,
        el: Element,
        handler: str,
        args: dict[str, t.Any],
        target: str | None,
        type_: str,
        submitter: Element | None = None,
        source: Element | None = None,
        beyond: bool = False,
    ) -> ServerEvent:
        """``wireview.send(el, handler, args, {target})``: the component it goes to and the fields it carries.

        ``source`` is where the event was dispatched (``el`` when not given): the
        field it came from, for ``meta``.
        """
        owner = el.closest(is_component)
        if owner is None:
            raise DomError(f"{el.describe()} is in no component's element: the client sends nothing for it")
        if target:
            target_el = next((d for d in self.root.descendants() if d.get("id") == target), None)
            if target_el is None:
                raise DomError(f"The {type_} binding of {el.describe()} targets {target!r}, which is not on the page")
        else:
            target_el = owner
        component_id = target_el.get("id") or ""
        form = el.closest(lambda a: a.tag == "form")
        scope = form if form is not None and target_el.contains(form) else target_el
        return ServerEvent(
            component_id=component_id,
            handler=handler,
            implicit_args=serialize(scope, target_el, self.fields, self.root, submitter),
            explicit_args=dict(args),
            event_type=type_,
            element=el,
            # A window or away binding's event happened elsewhere: no field of its own (#234)
            meta=event_meta(scope, target_el, self.fields, self.root, self.touched, None if beyond else (source or el)),
        )

    def hook_id(self, el: Element, hook: str | None) -> tuple[str, str]:
        """The component whose hook ``hook`` of ``el`` is, and the id the client gives it
        (``generateHookId``, with a fixed suffix)."""
        names = (el.get("wire-hook") or "").split()
        if not names:
            raise DomError(f"{el.describe()} has no wire-hook: no hook on it can push an event")
        if hook is None:
            if len(names) > 1:
                raise DomError(f"{el.describe()} has the hooks {names}: name the one that pushes with hook=")
            hook = names[0]
        elif hook not in names:
            raise DomError(f"{el.describe()} has no hook {hook!r}; its wire-hook names {names}")
        owner = el.closest(is_component)
        if owner is None:
            raise DomError(f"{el.describe()} is in no component's element: its hook belongs to none")
        owner_id = owner.get("id") or ""
        return owner_id, f"{owner_id}:{hook}:test"

    # -- actions ------------------------------------------------------------

    def click(self, el: Element, event: t.Mapping[str, t.Any] | None = None) -> list[ServerEvent]:
        self._refuse_disabled(el, "click")
        action = _Dispatch(self)
        self._click(action, el, event or {})
        return self._finish(action, el, "click")

    def _click(self, action: _Dispatch, el: Element, event: t.Mapping[str, t.Any]) -> None:
        """A click and what a browser does after it: tick a checkbox or radio, submit a form."""
        toggled = None
        if el.tag == "input" and input_type(el) in ("checkbox", "radio"):
            # The click toggles it before its listeners run, and is undone if one prevents it
            toggled = (el, self.fields.checked(el))
            if input_type(el) == "checkbox":
                self.fields.set_checked(el, not toggled[1], self.root)
            else:
                self.fields.set_checked(el, True, self.root)
        prevented = action.dispatch(el, "click", event)
        if toggled is not None:
            box, before = toggled
            if prevented:
                self.fields.set_checked(box, before, self.root)
            elif input_type(box) == "checkbox" or not before:
                action.dispatch(box, "input")
                action.dispatch(box, "change")
        elif not prevented and is_submitter(el, self.root):
            form = form_of(el, self.root)
            assert form is not None
            self._submit(action, form, el)
        elif not prevented and is_reset_button(el, self.root):
            form = form_of(el, self.root)
            assert form is not None
            self._reset(action, form)
        if not prevented and (control := self._labelled_by_click(el)) is not None:
            # The label's activation: a click of its own on the field, which ticks and submits as one does
            self._click(action, control, {})

    def _labelled_by_click(self, el: Element) -> Element | None:
        """The field a click on ``el`` goes on to through a ``<label>``: none when ``el`` is the field, or is
        inside something interactive in the label (a link, a button, another field)."""
        label = None
        for node in [el, *el.ancestors()]:
            if node.tag == "label":
                label = node
                break
            if is_interactive(node):
                return None
        if label is None:
            return None
        if (target := label.get("for")) is not None:
            control = next((d for d in self.root.descendants() if d.get("id") == target), None)
            if control is None or not is_labelable(control):
                return None
        else:
            control = next((d for d in label.descendants() if is_labelable(d)), None)
        if control is None or control is el or disabled_by(control) is not None:
            return None
        return control

    def key(self, el: Element | None, type_: str, event: t.Mapping[str, t.Any]) -> list[ServerEvent]:
        """``keydown`` or ``keyup`` at ``el``, and Enter's implicit submission of a form after a keydown.

        ``el`` None is a key with nothing focused: only ``window`` and ``away`` bindings hear it (#234).
        """
        if el is not None:
            self._refuse_disabled(el, type_)
        action = _Dispatch(self)
        prevented = action.dispatch(el, type_, event)
        if el is None:
            return self._finish(action, el, type_)
        if type_ == "keydown" and event.get("key") == "Enter" and not event.get("isComposing") and not prevented:
            self._submit_implicitly(action, el)
        return self._finish(action, el, type_)

    def _submit_implicitly(self, action: _Dispatch, el: Element) -> None:
        """Enter in a form's text field: a click on the form's default button, or a submit when it has none
        and this is its only text field (HTML's implicit submission)."""
        if el.tag != "input" or input_type(el) not in _IMPLICIT_SUBMISSION_TYPES:
            return
        form = form_of(el, self.root)
        if form is None:
            return
        owned = [d for d in self.root.descendants() if form_of(d, self.root) is form]
        default = next((d for d in owned if is_submitter(d, self.root)), None)
        if default is not None:
            if disabled_by(default) is None:
                self._click(action, default, {})
            return
        typed = [d for d in owned if d.tag == "input" and input_type(d) in _IMPLICIT_SUBMISSION_TYPES]
        if len(typed) == 1:
            self._submit(action, form)

    def focus(self, el: Element, type_: str) -> list[ServerEvent]:
        """``focus`` then ``focusin``, or ``blur`` then ``focusout``."""
        self._refuse_disabled(el, type_)
        if not is_focusable(el):
            raise DomError(
                f"Cannot {type_} {el.describe()}: it cannot take focus. A field, a button, a link with an href, "
                "or an element with tabindex or contenteditable can."
            )
        action = _Dispatch(self)
        action.dispatch(el, type_)
        action.dispatch(el, "focusin" if type_ == "focus" else "focusout")
        return self._finish(action, el, type_)

    def change(self, el: Element, value: t.Any, type_: str) -> list[ServerEvent]:
        self._refuse_disabled(el, type_)
        if el.tag not in ("input", "select", "textarea"):
            if value is not _UNSET:
                raise DomError(f"{el.describe()} is not a field: a user cannot give it a value")
        else:
            self._refuse_readonly(el, type_)
            if value is not _UNSET:
                self._set(el, value)
        action = _Dispatch(self)
        action.dispatch(el, type_)
        return self._finish(action, el, type_)

    def submit(
        self, form: Element, values: t.Mapping[str, t.Any] | None, submitter: Element | None
    ) -> list[ServerEvent]:
        if form.tag != "form":
            raise DomError(f"{form.describe()} is not a form: submit() submits a form")
        if submitter is not None:
            if not is_submitter(submitter, self.root) or form_of(submitter, self.root) is not form:
                raise DomError(f"{submitter.describe()} is not a submit button of {form.describe()}")
            self._refuse_disabled(submitter, "submit with")
        for name, value in (values or {}).items():
            self._set_named(form, name, value)
        action = _Dispatch(self)
        self._submit(action, form, submitter)
        return self._finish(action, form, "submit")

    def http_submission(
        self, form: Element, values: t.Mapping[str, t.Any] | None, submitter: Element | None, page_url: str | None
    ) -> Submission:
        """What the browser sends when ``form`` is submitted without script: ``submit_form()``.

        ``values`` and ``submitter`` as :meth:`submit` takes them. No submit event
        is dispatched: this is the request the page makes when nothing prevents
        the submit, or when its code calls ``form.submit()``.
        """
        if form.tag != "form":
            raise DomError(f"{form.describe()} is not a form: submit_form() submits a form")
        if submitter is not None:
            if not is_submitter(submitter, self.root) or form_of(submitter, self.root) is not form:
                raise DomError(f"{submitter.describe()} is not a submit button of {form.describe()}")
            self._refuse_disabled(submitter, "submit with")
        for name, value in (values or {}).items():
            self._set_named(form, name, value)
        return submission(self, form, submitter, page_url)

    def _submit(self, action: _Dispatch, form: Element, submitter: Element | None = None) -> None:
        # Constraint validation (required, pattern) is the browser's and is not checked here
        prevented = action.dispatch(form, "submit", submitter=submitter)
        if not prevented:
            raise DomError(
                f"Nothing prevents the submit of {form.describe()}: the browser would also send the form to its "
                'action and load that page. Bind it with {% on "submit.prevent" ... %}; a submit button\'s '
                'click is the same with {% on "click.prevent" ... %}.'
            )

    def _reset(self, action: _Dispatch, form: Element) -> None:
        """A form reset, as wireview.js answers it: the fields hold what the render drew again, and the form's
        bindings hear ``input`` then ``change`` with those values (#244). A ``reset`` binding that prevents it
        stops it."""
        if action.dispatch(form, "reset"):
            return
        self.touched.reset(form, self.root)
        for el in self.root.descendants():
            if el.tag in ("input", "select", "textarea") and form_of(el, self.root) is form:
                self.fields.forget(el)
        action.dispatch(form, "input", own=True)
        action.dispatch(form, "change", own=True)

    def _finish(self, action: _Dispatch, el: Element | None, type_: str) -> list[ServerEvent]:
        action.run_timers()
        if action.sent:
            return action.sent
        reasons = "".join(f"\n  {s.element.describe()} {s.attribute}: {s.why}" for s in action.skipped)
        if el is None:
            raise DomError(
                f"The {type_} with nothing focused reaches no handler: only a window binding "
                f'({{% on "{type_}.window" ... %}}) or an away one hears it, and none fired.'
                f"{' Skipped:' + reasons if reasons else ''}"
            )
        events = " then ".join(dict.fromkeys(name for name, _ in action.dispatched))
        raise DomError(
            f"The {type_} on {el.describe()} reaches no handler: it dispatched {events}, and no binding of a "
            f"server handler for {'it' if len(action.dispatched) == 1 else 'them'} fired on the element or its "
            f"ancestors.{' Skipped:' + reasons if reasons else ''}"
        )

    # -- fields -------------------------------------------------------------

    def _refuse_disabled(self, el: Element, doing: str) -> None:
        if (by := disabled_by(el)) is not None:
            where = "it is disabled" if by is el else f"it is inside the disabled {by.describe()}"
            raise DomError(f"Cannot {doing} {el.describe()}: {where}, and a browser sends no events for it")

    def _refuse_readonly(self, el: Element, doing: str) -> None:
        if el.tag == "input" and input_type(el) == "hidden":
            raise DomError(f"Cannot {doing} {el.describe()}: a hidden input takes no input from a user")
        if el.tag in ("input", "textarea") and el.has("readonly") and input_type(el) not in ("checkbox", "radio"):
            raise DomError(f"Cannot {doing} {el.describe()}: it is readonly")

    def _set(self, el: Element, value: t.Any) -> None:
        """Give a field the value a user would: type it, pick it or tick it."""
        if el.tag == "select":
            options = [option_value(o) for o in options_of(el) if disabled_by(o) is None]
            if el.has("multiple"):
                if isinstance(value, str) or not isinstance(value, (list, tuple)):
                    raise DomError(f"{el.describe()} is a select multiple: give the values to choose as a list")
                chosen = [str(v) for v in value]
            else:
                if not isinstance(value, str):
                    raise DomError(f"{el.describe()} is a select: give the value of the option to choose as a str")
                chosen = [value]
            if missing := [v for v in chosen if v not in options]:
                raise DomError(f"{el.describe()} has no option {missing[0]!r} a user can choose; it has {options}")
            self.fields.set_selected(el, chosen)
            return
        kind = input_type(el) if el.tag == "input" else el.tag
        if kind == "checkbox":
            if not isinstance(value, bool):
                raise DomError(f"{el.describe()} is a checkbox: give True to check it or False to uncheck it")
            self.fields.set_checked(el, value, self.root)
        elif kind == "radio":
            if value is not True:
                raise DomError(
                    f"{el.describe()} is a radio button: give True to choose it (a user cannot unchoose one)"
                )
            self.fields.set_checked(el, True, self.root)
        elif kind in ("submit", "image", "reset", "button", "file"):
            raise DomError(
                f"{el.describe()} takes no typed value"
                + (" (uploads are not in these helpers yet)" if kind == "file" else "")
            )
        else:
            if not isinstance(value, str):
                raise DomError(f"{el.describe()} takes text: give the value as a str, as the browser holds it")
            self.fields.set_value(el, value)

    def _set_named(self, form: Element, name: str, value: t.Any) -> None:
        """``submit(values=)``: what the user did with the fields called ``name`` in ``form``."""
        named = [
            el
            for el in self.root.descendants()
            if el.get("name") == name and el.tag in ("input", "select", "textarea") and form_of(el, self.root) is form
        ]
        if not named:
            raise DomError(f"{form.describe()} has no field called {name!r}")
        for el in named:
            self._refuse_disabled(el, "fill")
            self._refuse_readonly(el, "fill")
        kinds = {input_type(el) if el.tag == "input" else el.tag for el in named}
        if kinds == {"radio"}:
            if not isinstance(value, str):
                raise DomError(f"{name!r} in {form.describe()} is a group of radio buttons: give the value to choose")
            chosen = [el for el in named if self.fields.value(el) == value]
            if not chosen:
                raise DomError(f"No radio button {name!r} in {form.describe()} has the value {value!r}")
            self.fields.set_checked(chosen[0], True, self.root)
        elif kinds == {"checkbox"} and (len(named) > 1 or not isinstance(value, bool)):
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise DomError(
                    f"{name!r} in {form.describe()} is a group of checkboxes: give the values to check as a list"
                )
            wanted = [str(v) for v in value]
            have = [self.fields.value(el) for el in named]
            if missing := [v for v in wanted if v not in have]:
                raise DomError(f"No checkbox {name!r} in {form.describe()} has the value {missing[0]!r}")
            for el in named:
                self.fields.set_checked(el, self.fields.value(el) in wanted, self.root)
        elif len(named) == 1:
            self._set(named[0], value)
        elif isinstance(value, (list, tuple)) and len(value) == len(named) and kinds <= _TYPED_KINDS:
            # Text fields sharing a name take their values in order, as the browser lists them
            for el, each in zip(named, value, strict=True):
                self._set(el, each)
        else:
            raise DomError(
                f"{len(named)} fields are called {name!r} in {form.describe()}: give a list of {len(named)} "
                "values, one for each in the order of the page"
            )


# ============================================================================
# A form's HTTP submission (submit_form)
# ============================================================================


@dataclasses.dataclass(frozen=True)
class Submission:
    """What the browser's own submission of a form sends: where, how, and the entry list in order.

    ``entries`` holds ``(name, value)`` in the order HTML's "constructing the
    entry list" gives them; a file input with no file chosen is ``(name, None)``.
    """

    method: str
    action: str
    enctype: str
    entries: list[tuple[str, str | None]]


#: The enctypes a form takes; anything else is the default
ENCTYPES = ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")


def form_entries(
    form: Element, root: Element, fields: Fields, submitter: Element | None
) -> list[tuple[str, str | None]]:
    """HTML's entry list of ``form``: its submittable controls in tree order, ``form=`` ones included.

    Not a disabled one, a button only when it is ``submitter`` (an image button
    as ``name.x`` and ``name.y``), a checkbox or radio only when checked (its
    value, ``on`` without one), each selected option of a select, a file input
    with no file. Unlike ``serialize``, every name keeps its place: two fields
    of one name with another between them go in that order.
    """
    entries: list[tuple[str, str | None]] = []
    for el in root.descendants():
        if el.tag not in ("button", "input", "select", "textarea") or form_of(el, root) is not form:
            continue
        if disabled_by(el) is not None:
            continue
        kind = field_type(el)
        name = el.get("name") or ""
        if _is_button_control(el):
            if el is not submitter:
                continue
            if kind == "image":
                prefix = f"{name}." if name else ""
                entries += [(f"{prefix}x", "0"), (f"{prefix}y", "0")]
                continue
        if not name:
            continue
        if kind in ("checkbox", "radio"):
            if fields.checked(el):
                entries.append((name, fields.value(el)))
        elif kind == "file":
            entries.append((name, None))
        elif el.tag == "select":
            entries.extend(
                (name, option_value(option))
                for option in fields.selected_options(el)
                if disabled_by(option) is None and not _in_disabled_optgroup(option)
            )
        elif _is_button_control(el):
            entries.append((name, el.get("value") or ""))
        else:
            entries.append((name, "" if _bad_input(el, fields) else fields.value(el)))
    return entries


def _crlf(value: str) -> str:
    """HTML normalizes the line breaks of a submitted name or value to CRLF."""
    return re.sub(r"\r\n|\r|\n", "\r\n", value)


def submission(page: Page, form: Element, submitter: Element | None, page_url: str | None) -> Submission:
    """The request the browser makes when ``form`` is submitted (``submitter`` the button pressed).

    ``page_url`` is the page's address, which a relative or missing ``action``
    resolves against; the submitter's ``formaction``, ``formmethod`` and
    ``formenctype`` win over the form's.
    """
    from urllib.parse import urljoin, urlsplit

    def chosen(attribute: str, override: str) -> str | None:
        if submitter is not None and submitter.has(override):
            return submitter.get(override) or ""
        return form.get(attribute)

    method = (chosen("method", "formmethod") or "get").strip().lower()
    if method == "dialog":
        raise DomError(f"{form.describe()} has method=dialog: it closes a dialog and sends no request")
    if method not in ("get", "post"):
        method = "get"
    enctype = (chosen("enctype", "formenctype") or "").strip().lower()
    if enctype not in ENCTYPES:
        enctype = ENCTYPES[0]
    action = (chosen("action", "formaction") or "").strip()
    if page_url is None and not action.startswith(("/", "http://", "https://")):
        raise DomError(
            f"{form.describe()} sends to {action or 'the page it is on'!r}, which depends on the page's address: "
            "say where the component is with mount(..., path=...)"
        )
    url = urljoin(page_url or "http://testserver/", action)
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != "testserver" or parts.port not in (None, 80):
        raise DomError(
            f"{form.describe()} sends to {url!r}, not to this site: the test client reaches only the project's views"
        )
    return Submission(
        method=method,
        action=parts._replace(scheme="", netloc="", fragment="").geturl() or "/",
        enctype=enctype,
        entries=[
            (_crlf(name), None if value is None else _crlf(value))
            for name, value in form_entries(form, page.root, page.fields, submitter)
        ],
    )


#: The fields whose Enter submits their form (HTML's "fields that block implicit submission")
_IMPLICIT_SUBMISSION_TYPES = frozenset(
    {"text", "search", "url", "tel", "email", "password", "date", "month", "week", "time", "datetime-local", "number"}
)


def is_labelable(el: Element) -> bool:
    """What a ``<label>`` can name: a form control, not a hidden input."""
    if el.tag == "input":
        return input_type(el) != "hidden"
    return el.tag in ("button", "meter", "output", "progress", "select", "textarea")


def is_interactive(el: Element) -> bool:
    """HTML's interactive content, which takes a click in a label for itself."""
    if el.tag == "input":
        return input_type(el) != "hidden"
    if el.tag == "a":
        return el.has("href")
    return el.tag in ("button", "select", "textarea", "details", "embed", "iframe", "label") or (
        el.tag in ("audio", "video") and el.has("controls")
    )


def is_focusable(el: Element) -> bool:
    """Whether a user can give ``el`` focus (a click or Tab): what ``focus()`` and ``blur()`` act on."""
    if el.tag in ("input", "select", "textarea", "button"):
        return not (el.tag == "input" and input_type(el) == "hidden")
    if el.tag in ("a", "area") and el.has("href"):
        return True
    if el.tag in ("iframe", "summary"):
        return True
    if el.has("tabindex"):
        return True
    editable = el.get("contenteditable")
    return editable is not None and editable.lower() in ("", "true", "plaintext-only")


_TYPED_KINDS = frozenset(
    _INPUT_TYPES - {"checkbox", "radio", "submit", "image", "reset", "button", "file", "hidden"} | {"textarea"}
)


class _Unset:
    def __repr__(self) -> str:
        return "<unchanged>"


#: ``change()`` and ``input()`` without a value: the field keeps the one it has
_UNSET: t.Any = _Unset()


def _text_matches(el: Element, text: str | re.Pattern[str]) -> bool:
    content = normalized_text(el)
    if isinstance(text, re.Pattern):
        return text.search(content) is not None
    return " ".join(text.split()) in content


# ============================================================================
# Page checks: settings.TEST_PAGE_CHECKS
# ============================================================================


class _Reaching(_Dispatch):
    """A dispatch that only asks whether a binding that talks to the server fires, whoever's it is."""

    def touch(self, type_: str, target: Element) -> None:
        # Asking is not doing: the page keeps what was touched
        pass

    def fire(
        self,
        el: Element,
        value: dict[str, t.Any],
        type_: str,
        submitter: Element | None = None,
        source: Element | None = None,
        beyond: bool = False,
    ) -> None:
        if value.get("h") is not None or "js" in value:
            self.sent.append(t.cast(ServerEvent, None))


#: The checks of ``TEST_PAGE_CHECKS``, each one kind of mistake
PAGE_CHECKS = ("duplicate_id", "duplicate_component", "recover_form")


def page_problems(
    page: Page,
    callable_handler: t.Callable[[str, str], bool | None],
    skip_forms: t.Sequence[Selector] = (),
) -> list[tuple[str, str]]:
    """What is wrong with the structure of ``page``: what ``TEST_PAGE_CHECKS`` reports, as ``(check, message)``.

    - ``duplicate_id``: two elements with one id (``getElementById`` finds the first);
    - ``duplicate_component``: two components with one id (the client holds one per id);
    - ``recover_form``: a ``wire-auto-recover`` form that recovers nothing after a
      reconnect or an error's rollback: one naming a handler its component does
      not let a client call, or a bare one whose ``change`` reaches no server
      binding. One turned off (``wire-auto-recover="false"``), or that one of
      ``skip_forms`` matches, is not checked.

    ``callable_handler(component_id, name)`` says whether the component under
    that id takes the handler name from a client, ``None`` when it cannot say.
    """
    problems: list[tuple[str, str]] = []
    by_id: dict[str, list[Element]] = {}
    for el in page.root.descendants():
        if (id_ := el.get("id")) is not None and id_ != "":
            by_id.setdefault(id_, []).append(el)
    for id_, elements in by_id.items():
        if len(elements) < 2:
            continue
        listed = "".join(f"\n  {el.describe()}" for el in elements[:5])
        components = [el for el in elements if is_component(el)]
        if len(components) > 1:
            names = ", ".join(el.get("data-name") or el.tag for el in components)
            problems.append(
                (
                    "duplicate_component",
                    f"{len(components)} components have the id {id_!r} ({names}): the page holds one component per "
                    f"id, so their joins, events and renders are mixed up. Give each {{% component %}} its own "
                    f"id.{listed}",
                )
            )
        else:
            problems.append(
                (
                    "duplicate_id",
                    f"{len(elements)} elements have the id {id_!r}: the page finds only the first by it (a label's "
                    f"for, a myself target, a stream item's place).{listed}",
                )
            )
    for form in page.root.descendants():
        if form.tag != "form" or not form.has("wire-auto-recover"):
            continue
        if any(selector.matches(form) for selector in skip_forms):
            continue
        handler = form.get("wire-auto-recover") or ""
        if handler == RECOVERY_OFF:
            # Turned off: recovering nothing is what it asks for (#245)
            continue
        owner = form.closest(is_component)
        if owner is None:
            problems.append(("recover_form", f"{form.describe()} is in no component's element: nothing recovers it"))
        elif handler and handler != "true":
            if callable_handler(owner.get("id") or "", handler) is False:
                problems.append(
                    (
                        "recover_form",
                        f"{form.describe()} recovers through {handler!r}, which a client cannot call on "
                        f"{owner.get('data-name') or 'the component'}: after a reconnect its values reach nothing",
                    )
                )
        else:
            probe = _Reaching(page)
            # The recovery dispatches it as its own: window and away bindings do not hear it (#234)
            probe.dispatch(form, "change", own=True)
            if not probe.sent:
                problems.append(
                    (
                        "recover_form",
                        f"{form.describe()} has a bare wire-auto-recover, which replays the form's change after a "
                        'reconnect, and no {% on "change" %} binding on the form or above it takes that change: '
                        'nothing is recovered. Bind its change, or name a handler: wire-auto-recover="restore".',
                    )
                )
    return problems
