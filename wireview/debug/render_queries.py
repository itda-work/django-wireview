"""Which part of a render ran which SQL, in development (#182).

The coarse boundaries -- a component's render, an async property, a handler, a
mount, ``joined()``, a background task, a state signature -- open a *scope* in a
``ContextVar``. The ``ContextVar`` crosses ``sync_to_async`` and ``async_to_sync``
and is copied into a task when it is created, so the SQL a connection runs on a
worker thread lands in that connection's scope and no other.

The template line is not a boundary. When a query runs, the wrapper looks up the
stack for the innermost template node -- a frame named ``render`` or
``render_annotated`` whose ``self`` is a ``Node`` -- and reports that node's own
``origin`` and ``token.lineno``. A sync property is read the same way, from the
frame that collects the context (its ``attr_name``). Neither costs anything
until a query runs.

A live render and the renders of the LiveComponents it names are one piece of
work: ``send_render`` opens a ``tree`` scope around them (#189), so siblings that
ran the same statement once each are a repeat like the rows of one loop.

A scope that ends hands its rows to the scope around it, if that one is still
open; the outermost one logs them to ``wireview.queries``: ``DEBUG``, or
``WARNING`` when the same SQL ran ``REPEAT_THRESHOLD`` times from one place. The
format is not a promise. ``wireview.testing``'s ``queries()`` block collects the
same rows whatever the setting says.

The wrapper sits at the bottom of each connection's ``execute_wrappers``, put
there once and never taken out: ``execute_wrapper()`` pops the end of the list
when it ends, so another tool's wrapper above ours goes and ours stays. It is
installed when ``DEBUG_RENDER_QUERIES`` (``None`` follows ``DEBUG``) is on, when
``wireview.testing`` is imported, and when a ``queries()`` block is entered. A
process with the setting off that never imported ``wireview.testing`` has none.

Collecting is on while the setting is on, or while the context has a
``queries()`` block that is still open. A context whose blocks have all ended --
a task a block created, running on after it -- collects nothing more, with the
setting off: no new scope, no row in the scopes it opened, no log. An installed
wrapper with nothing collecting reads the ``ContextVar``; where it holds nothing,
it also asks whether a block is open anywhere and what the setting says, to count
the statement as unattributed when collecting is on. The boundaries ask the
setting too, whatever is installed. Neither is free; neither was measured apart
from the render (docs/design/render-part-queries.md §4-4).

What it cannot see: a connection opened before the wrapper was installed, on a
thread no install reached (a ``thread_sensitive=False`` pool thread, another
request's worker, a thread of the user's), runs its SQL unseen.

In development the outermost scope also goes to a file the editor reads
(``render_queries_file``, #188). A scope that will be written knows it when it
opens (``Scope.sink``): only then does a statement look up the source its
template was compiled from, and a render scope that ends tell the one around it
that it ran, rows or none.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
import typing as t
from collections import Counter

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.backends.signals import connection_created
from django.template.base import Node, TokenType

from .. import settings
from . import render_queries_file

if t.TYPE_CHECKING:
    from types import CodeType, FrameType

    from ..core.component import Component

log = logging.getLogger("wireview.queries")

#: The same SQL from the same place this many times in one scope is logged as a warning
REPEAT_THRESHOLD = 3
#: How much of a statement a log line shows
_SQL_WIDTH = 160


class Scope:
    """A boundary that was open while SQL ran: a render, a handler, a task, a signature."""

    __slots__ = ("kind", "cls", "name", "id", "detail", "outer", "blocks", "rows", "open", "sink", "renders")

    def __init__(
        self,
        kind: str,
        component: Component | None,
        detail: str | None,
        outer: Scope | None,
        blocks: tuple[Queries, ...] = (),
        sink: bool = False,
    ) -> None:
        self.kind = kind
        self.cls = type(component) if component is not None else None
        self.name = component._name if component is not None else None
        self.id = component.id if component is not None else None
        self.detail = detail
        self.outer = outer
        #: The ``queries()`` blocks around it: with the setting off, it collects while one is open
        self.blocks = blocks
        self.rows: list[Row] = []
        self.open = True
        #: Whether the record of the work this is part of goes to the editor's file (#188)
        self.sink = sink
        #: The render scopes that ended inside it, rows or none: each is a snapshot in the record
        self.renders: list[Scope] = []

    def describe(self) -> str:
        who = f"{self.name}#{self.id}" if self.name is not None else ""
        if self.kind == "render" or self.kind == "tree":
            return f"render {who} ({self.detail})"
        if self.kind == "handler":
            return f"handler {who}.{self.detail}"
        if self.kind == "task":
            return f"task {who} {self.detail}"
        if self.kind == "property":
            return f"property {self.detail} (async)"
        if self.kind == "sign":
            return f"state signing {who}"
        if self.kind == "broadcast":
            return f"broadcast {self.detail}"
        return f"{self.kind} {who}"

    def __repr__(self) -> str:
        return f"<Scope {self.describe()}>"


class Row:
    """One statement: where it ran from and in which scope."""

    __slots__ = (
        "sql",
        "template",
        "path",
        "line",
        "node",
        "text",
        "approximate",
        "source",
        "prop",
        "owner",
        "owner_cls",
        "prop_async",
        "scope",
        "render",
    )

    def __init__(self, sql: str, scope: Scope | None) -> None:
        self.sql = sql
        self.scope = scope
        #: The template's name as it was asked for (``origin.template_name``), and its path (``origin.name``)
        self.template: str | None = None
        self.path: str | None = None
        self.line: int | None = None
        #: ``{{ }}``, or the block tag's name
        self.node: str | None = None
        #: The tag's text as written, ``token.contents``
        self.text: str | None = None
        #: The node that ran it was made while rendering and has no place of its own: the one around it is named
        self.approximate = False
        #: The digest of the source the running template was compiled from (written records only)
        self.source: str | None = None
        #: A property the render's context read (sync), or the async property scope it ran in
        self.prop: str | None = None
        #: The class that defines that property (``module.Qualname``): two classes' ``total`` are two places
        self.owner: str | None = None
        self.owner_cls: type | None = None
        self.prop_async = False
        #: The render scope that ran it (written records only)
        self.render: Scope | None = None

    @property
    def where(self) -> str:
        if self.template is not None:
            return f"{self.template}:{self.line}" + (" (approx.)" if self.approximate else "")
        if self.prop is not None:
            return f"property {self.prop}"
        return "(outside a template)"

    @property
    def place(self) -> tuple[t.Any, ...]:
        """What makes two rows the same place, for repeats.

        A template line is the place whatever renders it: the same line drawn by
        every row's component is the N+1 this is for. A property is its defining
        class and name. Anything else -- a handler, a task, ``joined()``, a
        signature -- is the work: its kind, its component's class and its name.
        """
        if self.path is not None:
            return ("template", self.path, self.line)
        if self.prop is not None:
            return ("property", self.owner, self.prop)
        scope = self.scope
        if scope is None:
            return ("none",)
        return ("work", scope.kind, _qualified(scope.cls), scope.detail)

    def __repr__(self) -> str:
        return f"<Row {self.where} {self.sql[:40]!r}>"


class Queries:
    """The SQL a block of code ran, through the scopes it opened and the tasks it created.

    Entered, it collects whatever the setting says. Blocks nest: the inner one's
    rows are the outer one's too. Rows that arrive after the block ended -- a task
    it created finishing late -- are dropped.
    """

    def __init__(self) -> None:
        self.rows: list[Row] = []
        self.open = False
        self._token: contextvars.Token[_State | None] | None = None

    @property
    def count(self) -> int:
        """How many statements ran."""
        return len(self.rows)

    def assert_no_repeats(self, threshold: int = 2) -> None:
        """Fail when one statement ran ``threshold`` times or more from the same place."""
        repeated = _repeats(self.rows, threshold)
        if repeated:
            raise AssertionError(
                f"{len(repeated)} statement(s) ran {threshold} times or more from one place:\n"
                + _table(self.rows, repeated)
            )

    def _enter(self) -> None:
        global _open_blocks
        state = _state.get()
        # The work this block opens in is a test's now: none of it goes to the editor's file
        outer = state.scope if state is not None else None
        while outer is not None:
            outer.sink = False
            outer = outer.outer
        self.open = True
        _open_blocks += 1
        if state is None:
            new = _State(None, (self,), None)
        else:
            new = _State(state.scope, (*state.blocks, self), state.reason)
        self._token = _state.set(new)

    def _exit(self) -> None:
        global _open_blocks
        self.open = False
        _open_blocks -= 1
        if self._token is not None:
            _state.reset(self._token)
            self._token = None

    def __enter__(self) -> Queries:
        install()
        self._enter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._exit()

    async def __aenter__(self) -> Queries:
        from asgiref.sync import sync_to_async

        install()
        # The worker the async ORM and wireview's renders use has connections of its own
        await sync_to_async(_install_here, thread_sensitive=True)()
        self._enter()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._exit()


class _State:
    """What the ``ContextVar`` holds: the innermost scope, the open blocks, and why the next render runs."""

    __slots__ = ("scope", "blocks", "reason")

    def __init__(self, scope: Scope | None, blocks: tuple[Queries, ...], reason: str | None) -> None:
        self.scope = scope
        self.blocks = blocks
        self.reason = reason


_state: contextvars.ContextVar[_State | None] = contextvars.ContextVar("wireview_render_queries", default=None)
#: ``queries()`` blocks open anywhere in the process
_open_blocks = 0
#: Statements that ran while collecting was on and no scope or block was there to take them
_unattributed = 0


def _qualified(cls: type | None) -> str | None:
    return f"{cls.__module__}.{cls.__qualname__}" if cls is not None else None


def _owner_class(cls: type, name: str) -> type | None:
    """The class in ``cls``'s MRO that defines ``name``."""
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass
    return None


def _active(state: _State | None) -> bool:
    """Whether this context collects: the setting is on, or one of its blocks is still open."""
    if state is not None:
        for block in state.blocks:
            if block.open:
                return True
    return enabled()


def enabled() -> bool:
    """Whether the boundaries open scopes outside a ``queries()`` block: ``DEBUG_RENDER_QUERIES``."""
    setting = settings.DEBUG_RENDER_QUERIES
    return bool(settings.DEBUG if setting is None else setting)


def unattributed() -> int:
    """How many statements ran with collecting on and nothing to take them (no scope, no block)."""
    return _unattributed


# Boundaries


class _Null:
    """The boundary when nothing collects: enters and leaves."""

    __slots__ = ()

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


_NULL = _Null()


class _Open:
    __slots__ = ("state", "scope", "token")

    def __init__(self, state: _State | None, scope: Scope) -> None:
        self.state = state
        self.scope = scope
        self.token: contextvars.Token[_State | None] | None = None

    def __enter__(self) -> Scope:
        state = self.state
        if state is None:
            new = _State(self.scope, (), None)
        else:
            new = _State(self.scope, state.blocks, state.reason)
        self.token = _state.set(new)
        return self.scope

    def __exit__(self, *exc: object) -> None:
        if self.token is not None:
            _state.reset(self.token)
        _close(self.scope)


def scope(kind: str, component: Component | None = None, detail: str | t.Callable[[], str] | None = None) -> t.Any:
    """A boundary: while it is open, the SQL this context runs is ``kind``'s.

    Nothing is made when nothing collects: off, and no ``queries()`` block of
    this context still open. ``detail`` may be a function, called only then --
    a name built from user objects is not built when nothing collects.
    """
    state = _state.get()
    if not _active(state):
        return _NULL
    if state is None and not _connected:
        install()
    if callable(detail):
        detail = detail()
    outer = state.scope if state is not None else None
    if outer is not None and not outer.open:
        outer = None
    blocks = state.blocks if state is not None else ()
    if outer is not None:
        sink = outer.sink
    else:
        # A block's collecting is the test's, not the editor's
        sink = not any(block.open for block in blocks) and enabled() and render_queries_file.wanted()
    return _Open(state, Scope(kind, component, detail, outer, blocks, sink))


def render_scope(component: Component, default: str) -> t.Any:
    """A render's boundary, named after why it runs (``reason()``), else ``default``."""
    state = _state.get()
    if not _active(state):
        return _NULL
    return scope("render", component, (state.reason if state is not None else None) or default)


def nested_render_scope(component: Component) -> t.Any:
    """The boundary of a render inside another one's pass, or of an HTTP render when none is open."""
    state = _state.get()
    if not _active(state):
        return _NULL
    inside = state is not None and state.scope is not None and state.scope.open
    return scope("render", component, "nested" if inside else "http")


def tree_scope(component: Component, default: str) -> t.Any:
    """The boundary around one ``send_render``: the component's render and its LiveComponents' (#189).

    The renders of the LiveComponents a render names run after it, each in a
    render scope of its own. Without a scope around them all, each was the
    outermost: siblings drawing the same line once each were never one repeat.
    Their ``joined()``, ``update()`` and ``leaving()`` land in it too. It is
    named like the component's own render, so the log reads as it did.
    """
    return scope("tree", component, lambda: _reason_or(default))


def _reason_or(default: str) -> str:
    """Why the renders inside run (``reason()``), else ``default``."""
    state = _state.get()
    return (state.reason if state is not None else None) or default


class _Reason:
    __slots__ = ("reason", "token")

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.token: contextvars.Token[_State | None] | None = None

    def __enter__(self) -> None:
        state = _state.get()
        if state is None:
            new = _State(None, (), self.reason)
        else:
            new = _State(state.scope, state.blocks, self.reason)
        self.token = _state.set(new)

    def __exit__(self, *exc: object) -> None:
        if self.token is not None:
            _state.reset(self.token)


def reason(text: str) -> t.Any:
    """Name why the renders inside run (``event save``): the render's line in the log says it."""
    if not _active(_state.get()):
        return _NULL
    return _Reason(text)


def detach() -> None:
    """Begin a task of its own: the scopes around its creation are not its.

    ``create_task`` copies the context the task was made in, so without this a
    task that a still-running handler waits for was told as part of that handler,
    and its render's reason was the handler's. The ``queries()`` blocks stay: what
    the task runs before they end is theirs.
    """
    state = _state.get()
    if state is not None and (state.scope is not None or state.reason is not None or state.blocks):
        # Blocks that have ended are left behind: they no longer count, and with the setting off they kept it on
        blocks = tuple(block for block in state.blocks if block.open)
        _state.set(_State(None, blocks, None) if blocks else None)


def capture() -> _State | None:
    """This context's collecting state, for work another task does on its behalf (``restored()``)."""
    return _state.get()


class _Restored:
    __slots__ = ("state", "token")

    def __init__(self, state: _State | None) -> None:
        self.state = state
        self.token: contextvars.Token[_State | None] | None = None

    def __enter__(self) -> None:
        self.token = _state.set(self.state)

    def __exit__(self, *exc: object) -> None:
        if self.token is not None:
            _state.reset(self.token)


def restored(state: _State | None) -> t.Any:
    """Run as the context ``capture()`` was taken in: its scope, its blocks.

    A task that does many contexts' work at once -- the batch that signs the
    connections' state tokens -- restores each one's around its own part, or the
    SQL of all of them goes to whoever started the task.
    """
    if state is None and _state.get() is None:
        return _NULL
    # None as well: an asker that collected nothing must not land in the batch's starter's scope
    return _Restored(state)


def _close(scope: Scope) -> None:
    scope.open = False
    outer = scope.outer
    inside = outer is not None and outer.open
    if scope.sink and inside:
        # A render that ends is a snapshot of the work around it, rows or none (#188)
        assert outer is not None
        if scope.kind == "render":
            outer.renders.append(scope)
        outer.renders.extend(scope.renders)
        scope.renders = []
    rows = scope.rows
    if rows:
        if not any(block.open for block in scope.blocks) and not enabled():
            # Its blocks ended and the setting is off: what it has is nobody's any more
            scope.rows = []
            return
        if inside:
            assert outer is not None
            outer.rows.extend(rows)
            return
        _log(scope)
    # Asked again at the end: suppression, DEBUG or the setting may have turned off since it began
    if scope.sink and not inside and enabled() and render_queries_file.wanted():
        render_queries_file.emit(scope)


# Logging


def _repeats(rows: t.Iterable[Row], threshold: int) -> set[tuple[t.Any, ...]]:
    counts = Counter((row.place, row.sql) for row in rows)
    return {key for key, count in counts.items() if count >= threshold}


def _owner_key(scope: Scope | None) -> t.Any:
    """What makes two rows' scopes one in the log: renders of one class for one reason are one.

    Sibling LiveComponents, or the rows a loop drew as components, are each a
    render of their own; the statement they all ran is one line of the table.
    """
    if scope is not None and scope.kind == "render":
        return ("render", scope.cls, scope.detail)
    return scope


def _is_top(scope: Scope, top: Scope | None) -> bool:
    """Whether ``scope`` is the work the log is headed with: ``top``, or the render a tree is named after."""
    if scope is top:
        return True
    return (
        top is not None
        and top.kind == "tree"
        and scope.kind == "render"
        and scope.outer is top
        and scope.cls is top.cls
        and scope.id == top.id
    )


def _table(rows: list[Row], repeated: set[tuple[t.Any, ...]], top: Scope | None = None) -> str:
    groups: dict[tuple[t.Any, ...], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.place, row.sql, _owner_key(row.scope)), []).append(row)
    lines = []
    for (place, sql, _owner), same in groups.items():
        row = same[0]
        where = row.where
        if row.node is not None:
            where = f"{where}  {row.node}"
        owners = list(dict.fromkeys(other.scope for other in same if other.scope is not None))
        if owners and not all(_is_top(owner, top) for owner in owners) and owners[0].kind != "property":
            if len(owners) == 1:
                where = f"{where}  [{owners[0].describe()}]"
            else:
                where = f"{where}  [render {owners[0].name} ×{len(owners)} ({owners[0].detail})]"
        count = f"{len(same)}×" if len(same) > 1 else "1"
        text = " ".join(sql.split())
        if len(text) > _SQL_WIDTH:
            text = text[: _SQL_WIDTH - 1] + "…"
        mark = "  <- repeated" if (place, sql) in repeated else ""
        lines.append(f"  {where:<48} {count:>4}  {text}{mark}")
    return "\n".join(lines)


def _log(scope: Scope) -> None:
    rows = scope.rows
    repeated = _repeats(rows, REPEAT_THRESHOLD)
    level = logging.WARNING if repeated else logging.DEBUG
    if not log.isEnabledFor(level):
        return
    head = f"{scope.describe()}: {len(rows)} quer{'y' if len(rows) == 1 else 'ies'}"
    if repeated:
        head += f", {len(repeated)} repeated"
    log.log(level, "%s\n%s", head, _table(rows, repeated, scope))


# The wrapper


def _wrapper(execute: t.Callable[..., t.Any], sql: str, params: t.Any, many: bool, context: t.Any) -> t.Any:
    state = _state.get()
    if state is None:
        if _open_blocks or enabled():
            _count_unattributed()
    else:
        _record(state, sql)
    return execute(sql, params, many, context)


def _count_unattributed() -> None:
    global _unattributed
    _unattributed += 1


def _record(state: _State, sql: str) -> None:
    scope = state.scope
    if scope is not None and not scope.open:
        scope = None  # ended: nothing of it is told any more
    blocks = [block for block in state.blocks if block.open]
    if not blocks and not enabled():
        return  # the blocks this context had have ended, and the setting is off
    if scope is None and not blocks:
        _count_unattributed()
        return
    row = Row(sql, scope)
    sink = scope is not None and scope.sink
    _locate(row, sys._getframe(2), sink)
    if row.template is None and row.prop is None and scope is not None and scope.kind == "property":
        row.prop = scope.detail
        row.prop_async = True
        if scope.cls is not None and scope.detail is not None:
            row.owner_cls = _owner_class(scope.cls, scope.detail)
            row.owner = _qualified(row.owner_cls)
    if sink:
        render = scope
        while render is not None and render.kind != "render":
            render = render.outer
        row.render = render
    if scope is not None:
        scope.rows.append(row)
    for block in blocks:
        block.rows.append(row)


#: The code of the frames that read a render's context, whose ``attr_name`` is the property being read
_collecting: frozenset[CodeType] | None = None
#: Nodes wireview puts around a loop's items: they are not a place in a template
_transparent: tuple[type, ...] = ()


def _collecting_codes() -> frozenset[CodeType]:
    global _collecting, _transparent
    if _collecting is None:
        from ..core.lazy_context import LazyContext
        from ..core.meta import WireviewMeta
        from ..template_engine import ComprehensionItemNode

        _transparent = (ComprehensionItemNode,)
        _collecting = frozenset(
            {
                WireviewMeta._collect_context.__code__,
                WireviewMeta._get_context.__code__,
                WireviewMeta._read.__code__,
                LazyContext._compute.__code__,
            }
        )
    return _collecting


def _unwrap(node: t.Any) -> t.Any:
    """Django's node under wireview's markers: ``{{ }}``, ``{% for %}``, ``{% if %}``, ``{% include %}``."""
    while True:
        inner = getattr(node, "original_node", None) or getattr(node, "for_node", None) or getattr(node, "inner", None)
        if inner is None or not isinstance(inner, Node):
            return node
        node = inner


def _locate(row: Row, frame: FrameType | None, sink: bool = False) -> None:
    """Put on ``row`` the innermost template node on the stack, or the property being read.

    For a record that goes to the editor's file (``sink``), also the digest of the
    source the node's template was compiled from (``render_queries_file.source_of``).
    """
    collecting = _collecting_codes()
    skipped = False
    while frame is not None:
        code = frame.f_code
        name = code.co_name
        if name == "render" or name == "render_annotated":
            node = frame.f_locals.get("self")
            if isinstance(node, Node):
                node = _unwrap(node)
                origin = getattr(node, "origin", None)
                token = getattr(node, "token", None)
                if origin is not None and token is not None and getattr(token, "lineno", None) is not None:
                    row.template = origin.template_name or origin.name
                    row.path = origin.name
                    row.line = token.lineno
                    if token.token_type == TokenType.VAR:
                        row.node = "{{ }}"
                    else:
                        row.node = token.contents.split()[0] if token.contents else None
                    row.text = token.contents
                    row.approximate = skipped
                    if sink:
                        row.source = render_queries_file.source_of(node, frame)
                    return
                if not isinstance(node, _transparent):
                    skipped = True
        elif code in collecting:
            local = frame.f_locals
            attr = local.get("attr_name")
            if isinstance(attr, str):
                row.prop = attr
                component = local.get("component")
                if component is not None:
                    row.owner_cls = _owner_class(type(component), attr)
                    row.owner = _qualified(row.owner_cls)
                return
        frame = frame.f_back


# Installing


#: Whether ``connection_created`` puts the wrapper on every new connection
_connected = False
_lock = threading.Lock()


def install() -> None:
    """Put the wrapper on every connection this thread holds, and on every one opened from now on, in any thread."""
    global _connected
    if not _connected:
        with _lock:
            if not _connected:
                connection_created.connect(_on_connection_created, dispatch_uid="wireview.render_queries")
                _connected = True
    _install_here()


def installed() -> bool:
    """Whether new connections get the wrapper."""
    return _connected


def _install_here() -> None:
    try:
        held = connections.all(initialized_only=True)
    except ImproperlyConfigured:  # imported before the settings: the signal covers what opens later
        return
    for connection in held:
        _add(connection)


def _add(connection: t.Any) -> None:
    wrappers = connection.execute_wrappers
    if _wrapper not in wrappers:
        # At the bottom: ``execute_wrapper()`` pops the end of the list, and a wrapper
        # appended inside one would be the one it popped
        wrappers.insert(0, _wrapper)


def _on_connection_created(sender: t.Any, connection: t.Any, **kwargs: t.Any) -> None:
    _add(connection)


def _wrapped(connection: t.Any) -> bool:
    """Whether ``connection`` has the wrapper (tests)."""
    return _wrapper in connection.execute_wrappers


def _installed_wrapper() -> t.Callable[..., t.Any]:
    """The wrapper itself (tests)."""
    return _wrapper


__all__ = [
    "REPEAT_THRESHOLD",
    "Queries",
    "Row",
    "Scope",
    "capture",
    "detach",
    "enabled",
    "install",
    "installed",
    "nested_render_scope",
    "reason",
    "render_scope",
    "restored",
    "scope",
    "tree_scope",
    "unattributed",
]
