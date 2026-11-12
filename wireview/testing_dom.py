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


#: What a browser's parser ignores in a body outside a ``<table>``: a stream item
#: ``<tr>`` parses to its cells' contents, not to a row
TABLE_PARTS = frozenset({"tr", "td", "th", "tbody", "thead", "tfoot", "caption", "col", "colgroup"})


class _TreeBuilder(HTMLParser):
    def __init__(self, in_body: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", [], None)
        self._open: list[Element] = [self.root]
        self._in_body = in_body

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_body and tag in TABLE_PARTS and not any(e.tag == "table" for e in self._open):
            return
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
    """``_parseHtml`` in wireview.js: the first element of ``html`` parsed as a document's body, or ``None``."""
    builder = _TreeBuilder(in_body=True)
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
    """Every stream operation sent so far, in order, on the containers of ``root``. One whose container
    is not on the page is dropped, as the client drops it once its wait for the container ends."""
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


def parse_binding(attribute: str) -> Binding | None:
    """``parseBinding``: the event and the modifiers a binding attribute's name says."""
    if not attribute.startswith(BINDING_PREFIX):
        return None
    type_, *tokens = attribute[len(BINDING_PREFIX) :].split(".")
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
    return Binding(type_, tuple(steps))


def bindings_for(names: t.Iterable[str], type_: str) -> list[str]:
    """``bindingsFor``: the attributes among ``names`` that answer to an event ``type_``, in order."""
    exact = BINDING_PREFIX + type_
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
        if id(select) in self._selected:
            return self._selected[id(select)]
        options = options_of(select)
        chosen = [o for o in options if o.has("selected")]
        if select.has("multiple"):
            return [option_value(o) for o in chosen]
        if chosen:
            # A select-one shows the last option marked selected
            return [option_value(chosen[-1])]
        # ...or the first that is not disabled
        first = next((o for o in options if not o.has("disabled") and not _in_disabled_optgroup(o)), None)
        return [option_value(first)] if first is not None else []

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


def serialize(scope: Element, component: Element, fields: Fields) -> dict[str, list[t.Any]]:
    """``Component.serialize(scope)`` of the component whose element is ``component``.

    Every element with a ``name`` under ``scope`` that is not inside a nested
    component: checkboxes and radios when checked (their value, or ``true``
    when it is empty), a select-multiple's chosen values as one list, anything
    else its value -- named buttons and disabled fields included, as the client
    sends them.
    """
    result: dict[str, list[t.Any]] = {}
    for el in scope.descendants():
        if not el.has("name"):
            continue
        if el.closest(is_component) is not component:
            continue
        kind = field_type(el)
        value: t.Any
        if kind in ("checkbox", "radio"):
            value = (fields.value(el) or True) if fields.checked(el) else None
            if value is None:
                continue
        elif kind == "select-multiple":
            value = fields.selected(el)
        elif el.tag in ("input", "select", "textarea"):
            value = fields.value(el)
        elif el.tag == "button":
            value = el.get("value") or ""
        else:
            value = _non_field_value(el)
        key = el.get("name")
        if key:
            result.setdefault(key, []).append(value)
    return result


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


class _BindingRun:
    """What ``runSteps``' callbacks do for one binding in ``EventBindings.run``."""

    def __init__(
        self, action: _Dispatch, state: _EventState, el: Element, attribute: str, value: dict[str, t.Any], type_: str
    ) -> None:
        self.action, self.state, self.el, self.attribute, self.value, self.type = (
            action,
            state,
            el,
            attribute,
            value,
            type_,
        )

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
        self.action.fire(self.el, self.value, self.type)


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

    def dispatch(self, target: Element, type_: str, event: t.Mapping[str, t.Any] | None = None) -> bool:
        """Dispatch ``type_`` at ``target``, bubbling (``EventBindings.bubble``). Whether its default was prevented.

        An event that does not bubble (``focus``, ``blur``) runs its target's
        bindings alone: the client's capture listener on the root.
        """
        state = _EventState()
        self.dispatched.append((type_, target))
        path = [target] if type_ in NON_BUBBLING else [target, *(a for a in target.ancestors() if a.tag != "#document")]
        for el in path:
            state.stopped = False
            for attribute in bindings_for(el.attrs, type_):
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
                    run = _BindingRun(self, state, el, attribute, value, type_)
                    if (failed := run_steps(binding.steps, event or {}, run)) is not None:
                        self.skip(el, attribute, failed)
            if state.stopped:
                break
        return state.prevented

    def run_timers(self) -> None:
        while self.timers:
            self.timers.sort(key=lambda timer: (timer[0], timer[1]))
            _, _, later = self.timers.pop(0)
            later()

    def skip(self, el: Element, attribute: str, failed: Step) -> None:
        modifier = failed.name if failed.arg is None else f"{failed.name} {failed.arg!r}"
        self.skipped.append(_Skipped(el, attribute, f"its {modifier} modifier does not hold for this event"))

    def fire(self, el: Element, value: dict[str, t.Any], type_: str) -> None:
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
        self.sent.append(self.page.server_event(el, handler, value.get("a") or {}, value.get("t"), type_))


class Page:
    """The mounted component's render, as the browser holds it, and what each user action sends from it."""

    def __init__(self, html: str, component_id: str, stream_ops: t.Iterable[t.Mapping[str, t.Any]] = ()) -> None:
        self.html = html
        self.root = parse(html)
        # The render carries its stream containers empty; the page has the items the operations put there
        apply_stream_ops(self.root, stream_ops)
        self.component_id = component_id
        self.fields = Fields()

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
        self, el: Element, handler: str, args: dict[str, t.Any], target: str | None, type_: str
    ) -> ServerEvent:
        """``wireview.send(el, handler, args, {target})``: the component it goes to and the fields it carries."""
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
        if component_id != self.component_id:
            raise DomError(
                f"The {type_} binding of {el.describe()} calls {handler!r} on "
                f"{target_el.get('data-name') or 'a component'} {component_id!r}, not on the mounted "
                f"{self.component_id!r}: mount() runs the handlers of the component it mounted. "
                f"Mount {target_el.get('data-name') or 'that component'} to test its events."
            )
        form = el.closest(lambda a: a.tag == "form")
        scope = form if form is not None and target_el.contains(form) else target_el
        return ServerEvent(
            component_id=component_id,
            handler=handler,
            implicit_args=serialize(scope, target_el, self.fields),
            explicit_args=dict(args),
            event_type=type_,
            element=el,
        )

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
            self._submit(action, form)

    def key(self, el: Element, type_: str, event: t.Mapping[str, t.Any]) -> list[ServerEvent]:
        """``keydown`` or ``keyup`` at ``el``, and Enter's implicit submission of a form after a keydown."""
        self._refuse_disabled(el, type_)
        action = _Dispatch(self)
        prevented = action.dispatch(el, type_, event)
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
        self._submit(action, form)
        return self._finish(action, form, "submit")

    def _submit(self, action: _Dispatch, form: Element) -> None:
        # Constraint validation (required, pattern) is the browser's and is not checked here
        prevented = action.dispatch(form, "submit")
        if not prevented:
            raise DomError(
                f"Nothing prevents the submit of {form.describe()}: the browser would also send the form to its "
                'action and load that page. Bind it with {% on "submit.prevent" ... %}; a submit button\'s '
                'click is the same with {% on "click.prevent" ... %}.'
            )

    def _finish(self, action: _Dispatch, el: Element, type_: str) -> list[ServerEvent]:
        action.run_timers()
        if action.sent:
            return action.sent
        reasons = "".join(f"\n  {s.element.describe()} {s.attribute}: {s.why}" for s in action.skipped)
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


#: The fields whose Enter submits their form (HTML's "fields that block implicit submission")
_IMPLICIT_SUBMISSION_TYPES = frozenset(
    {"text", "search", "url", "tel", "email", "password", "date", "month", "week", "time", "datetime-local", "number"}
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
