"""One render for every connection that shows a component the same way (#176, stage 2).

A broadcast reaches every connection subscribed to its topic, and each one used
to render its component again: a thousand viewers of one board rendered the
same board a thousand times. A class that declares ``Meta.shared_render = True``
promises that its render reads nothing of the viewer -- no ``user``, no
``session``, no query, nothing a connection holds -- only its fields and data
every viewer shares. For such a class, the connections that handle the same
broadcast in one process render the component once and share the result:

- **What is shared.** The template's output, parsed (``Rendered``). One
  connection renders it (the leader); the others wait for it. Each connection
  still runs its own receiver (``notification()``, ``mutation()``), its own
  ``after_render`` hooks and its own diff against what its page shows.
- **What is not.** ``data-state``. The signed state names the page's boundary
  and its user's authentication (``state.issuing_context``), so a token is
  never shared: the shared render holds a placeholder where the token goes
  (``STATE_SLOT``) and every connection puts its own token there
  (``Shared.with_state``) before its diff.
- **When two renders are the same.** Only within one broadcast message
  (``handling``), and only for the same class, component id, fields (every one
  of ``model_fields`` but ``user``, ``wire`` and ``session``, read as they are:
  ``Field(exclude=True)`` and field serializers do not hide a value), active
  language and time zone. Anything else renders on its own.
- **When none is shared.** A field that holds a model instance or a QuerySet
  anywhere: the render reads the instance's attributes -- an unsaved edit, a
  per-user annotation, a row loaded at another time -- where a key could only
  name its pk. Such a render is the connection's own.

The promise cannot be checked in full: a property may read anything. What can
be is checked where it is cheap. ``wireview.W019`` names a class whose Meta or
template puts it out of scope, and a render of such a class does not share.
With ``VERIFY_SHARED_RENDER`` (``DEBUG`` by default, and ``wireview.testing``),
a declared class's render that reads ``user``, ``session``, ``request``,
``perms``, ``csrf_token`` or ``messages`` raises -- after the render too, when
the template swallowed the error (``{% if a and b %}``) -- and every connection
that took a render from another renders on its own as well and raises if the
two differ.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
import secrets
import typing as t
import weakref
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils import timezone, translation
from django.utils.html import escape
from pydantic import BaseModel
from pydantic_core import to_json

from ..utils import db
from .rendered import _NESTED_PREFIX, Rendered

if t.TYPE_CHECKING:
    from django.template.base import Node

    from .component import Component
    from .meta import Repo, WireviewMeta

log = logging.getLogger("wireview")

#: What a shared render writes where ``data-state``'s value goes. Random per
#: process, so no template output can hold it by chance.
STATE_SLOT = f"wireview-shared-state-{secrets.token_hex(12)}"

#: The broadcast message the current task handles (``handling``)
_message: contextvars.ContextVar[str | None] = contextvars.ContextVar("wireview_shared_message", default=None)
#: Set by ``wireview.testing``: its renders verify whatever the setting says, as ``DEBUG`` does
_testing: contextvars.ContextVar[bool] = contextvars.ContextVar("wireview_shared_testing", default=False)

#: How long a process keeps a message's renders, and how much of them at most:
#: the marked HTML of the renders kept, and their number. A connection that
#: reaches a message later, or after it was dropped, renders on its own. One
#: message reaches every connection of the process within a fan-out -- about
#: 0.1 s for a thousand (docs/PERFORMANCE.md) -- so a second covers a busy loop.
KEEP_SECONDS = 1.0
KEEP_BYTES = 16 * 1024 * 1024
KEEP_RENDERS = 4096

#: Names a viewer-independent render has no business reading. ``user`` and
#: ``session`` are the component's own; the rest are what context processors
#: give a request's templates. A component's template never gets the latter --
#: it renders without the request -- so reading one is a sign the template
#: was written for a page.
WATCHED_FIELDS = ("user", "session")
WATCHED_CONTEXT = ("request", "perms", "csrf_token", "messages")

#: Template tags that draw another component, a slot or an upload into the
#: render: what they draw is the connection's own (its repository, its
#: uploads), so a template that holds one does not share.
_COMPONENT_TAGS = frozenset(
    {
        "component",
        "live_component",
        "render_slot",
        "wireview_toasts",
        "upload_input",
        "upload_drop_zone",
        "upload_button",
        "upload_preview",
    }
)
_COMPONENT_NODES = frozenset({"ComponentBlockNode", "LiveComponentBlockNode", "FillNode"})


class SharedRenderError(ImproperlyConfigured):
    """A class that declares ``Meta.shared_render`` rendered something of the viewer."""


@contextmanager
def handling(message_id: str | None) -> t.Iterator[None]:
    """Renders in this block may share with other connections handling ``message_id``."""
    token = _message.set(message_id)
    try:
        yield
    finally:
        _message.reset(token)


@contextmanager
def testing() -> t.Iterator[None]:
    """Verify the renders in this block, unless ``VERIFY_SHARED_RENDER`` is ``False``."""
    token = _testing.set(True)
    try:
        yield
    finally:
        _testing.reset(token)


def verifying() -> bool:
    """Whether declared classes are watched and shared renders checked: ``VERIFY_SHARED_RENDER``."""
    from .. import settings

    setting = settings.VERIFY_SHARED_RENDER
    if setting is None:
        return bool(django_settings.DEBUG) or _testing.get()
    return bool(setting)


# -- scope ----------------------------------------------------------------------------------


def out_of_scope(cls: type[Component]) -> list[str]:
    """Why ``cls``'s renders cannot be shared although it declares ``shared_render``; empty if they can.

    What ``wireview.W019`` reports, and what keeps the class's renders its own.
    """
    from ..live_component import LiveComponent

    meta = cls._meta
    reasons = []
    if issubclass(cls, LiveComponent):
        reasons.append("it is a LiveComponent, rendered and updated by the component that draws it")
    if meta.temporary_assigns:
        reasons.append("it has Meta.temporary_assigns, which each connection settles against what its own page shows")
    if meta.live_sessions:
        reasons.append("it has Meta.live_sessions: a page boundary is about who is looking")
    if meta.slots:
        reasons.append("it has Meta.slots, filled by whichever page draws it")
    if rows := _row_fields(cls):
        reasons.append(
            f"its fields {', '.join(rows)} hold model instances or QuerySets, whose attributes differ from "
            "connection to connection under the same primary key"
        )
    if meta.template_name:
        try:
            reasons.extend(_template_reasons(cls))
        except Exception:  # a missing template is not this check's to report
            pass
    return reasons


def _template_reasons(cls: type[Component]) -> list[str]:
    from django.template.loader import get_template

    template = get_template(t.cast(str, cls._meta.template_name))
    nodelist = getattr(getattr(template, "template", template), "nodelist", None)
    if nodelist is None:
        return []
    tags: set[str] = set()
    names: set[str] = set()
    for node in _walk(nodelist):
        kind = type(node).__name__
        func = getattr(node, "func", None)
        if kind in _COMPONENT_NODES or (kind == "SimpleNode" and getattr(func, "__name__", "") in _COMPONENT_TAGS):
            tags.add(getattr(func, "__name__", None) or kind)
        if kind == "CsrfTokenNode":
            names.add("csrf_token")
        names.update(name for name in _watched_names(node) if not _owns(cls, name))
    reasons = []
    if tags:
        reasons.append(
            f"its template draws another component, a slot or an upload ({', '.join(sorted(tags))}), "
            "which each connection draws from its own repository"
        )
    if names:
        reasons.append(f"its template reads {', '.join(sorted(names))}, which differ from viewer to viewer")
    return reasons


def _row_fields(cls: type[Component]) -> list[str]:
    """The fields whose type names a model or a QuerySet anywhere: their render is never shared."""
    from .component import ALWAYS_EXCLUDED

    return sorted(
        name
        for name, field in cls.model_fields.items()
        if name not in ALWAYS_EXCLUDED and _mentions_rows(field.annotation)
    )


def _mentions_rows(annotation: t.Any) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, (models.Model, models.QuerySet)):
        return True
    return any(_mentions_rows(arg) for arg in t.get_args(annotation) if arg is not Ellipsis)


def _owns(cls: type[Component], name: str) -> bool:
    """Whether ``name`` is the component's own -- a field or property -- rather than the request's."""
    return name in WATCHED_CONTEXT and (name in cls.model_fields or hasattr(cls, name))


def _walk(nodelist: t.Iterable[Node]) -> t.Iterator[Node]:
    for node in nodelist:
        yield node
        for attr in getattr(node, "child_nodelists", ()):
            yield from _walk(getattr(node, attr, None) or ())
        if type(node).__name__ == "IfNode":
            for _condition, branch in node.conditions_nodelists:  # type: ignore[attr-defined]
                yield from _walk(branch)


def _watched_names(node: Node) -> t.Iterator[str]:
    """The viewer's names ``node`` reads as the first part of a variable: ``user.x``, ``this.session``."""
    for variable in _variables(node):
        lookups = getattr(variable, "lookups", None)
        if not lookups:
            continue
        if lookups[0] in WATCHED_FIELDS or lookups[0] in WATCHED_CONTEXT:
            yield lookups[0]
        elif lookups[0] == "this" and len(lookups) > 1 and lookups[1] in WATCHED_FIELDS:
            yield f"this.{lookups[1]}"


def _variables(node: Node) -> t.Iterator[t.Any]:
    """Every variable ``node`` resolves: its expressions' and their filters' arguments.

    The expressions are found among the node's attributes, so a tag that keeps
    them in a list or a dict of its own (``firstof``, ``cycle``, ``cache``'s
    ``vary_on``, ``blocktranslate``'s ``with``) is read as well as ``{{ }}``.
    """
    from django.template.base import FilterExpression, Variable

    def of(expression: t.Any) -> t.Iterator[t.Any]:
        if isinstance(expression, Variable):
            yield expression
        elif isinstance(expression, FilterExpression):
            if isinstance(expression.var, Variable):
                yield expression.var
            for _func, arguments in expression.filters:
                yield from (argument for lookup, argument in arguments if lookup)

    if type(node).__name__ == "IfNode":
        for condition, _branch in node.conditions_nodelists:  # type: ignore[attr-defined]
            for expression in _condition_expressions(condition):
                yield from of(expression)
        return
    for value in vars(node).values():
        if isinstance(value, Mapping):
            value = list(value.values())
        for expression in value if isinstance(value, (list, tuple)) else (value,):
            yield from of(expression)


def _condition_expressions(condition: t.Any) -> t.Iterator[t.Any]:
    if condition is None:
        return
    if hasattr(condition, "value") and hasattr(condition.value, "var"):  # a TemplateLiteral
        yield condition.value
    for side in ("first", "second"):
        if (part := getattr(condition, side, None)) is not None:
            yield from _condition_expressions(part)


_SCOPE: weakref.WeakKeyDictionary[type, tuple[t.Any, bool]] = weakref.WeakKeyDictionary()


def declared(component: Component) -> bool:
    """Whether ``component``'s live render goes the shared way: declared and in scope.

    A class out of scope is told once in the log and renders as any other.
    The scope is kept per class and Meta; the template is read once.
    """
    cls = type(component)
    meta = cls._meta
    if not meta.shared_render:
        return False
    kept = _SCOPE.get(cls)
    if kept is None or kept[0] is not meta:
        reasons = out_of_scope(cls)
        if reasons:
            log.warning(
                "%s declares Meta.shared_render, but its renders are not shared: %s (wireview.W019)",
                cls.__qualname__,
                "; ".join(reasons),
            )
        kept = _SCOPE[cls] = (meta, not reasons)
    return kept[1]


# -- the renders ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Shared:
    """A render many connections can take: the parse, and where the state token goes."""

    rendered: Rendered
    #: The marked HTML, ``STATE_SLOT`` in place of the token: what verification compares
    html: str
    #: The indices from the root to the dynamic that holds ``STATE_SLOT``, or None without one
    state_path: tuple[int, ...] | None

    @classmethod
    def parse(cls, html: str) -> Shared | None:
        """``html`` parsed, or None when it cannot be shared: it names a component, or holds the slot twice."""
        if _NESTED_PREFIX in html:
            return None
        slots = html.count(STATE_SLOT)
        if slots > 1:
            return None
        rendered = Rendered.from_marked_html(html)
        if rendered._names is not False:
            return None
        if not slots:
            return cls(rendered, html, None)
        path = _slot_path(rendered, ())
        # Not found outside the loops: inside one, where a path cannot name it
        return cls(rendered, html, path) if path is not None else None

    def with_state(self, token: str) -> Rendered:
        """The render with ``token`` where the slot is: a copy of the path to it, the rest shared."""
        if self.state_path is None:
            return self.rendered
        return _replaced(self.rendered, self.state_path, str(escape(token)))


def _slot_path(rendered: Rendered, path: tuple[int, ...]) -> tuple[int, ...] | None:
    """Where ``STATE_SLOT`` is in ``rendered``, looking into blocks but not loops."""
    for i, value in enumerate(rendered.dynamic):
        if value is STATE_SLOT or (isinstance(value, str) and value == STATE_SLOT):
            return (*path, i)
        if isinstance(value, Rendered) and (found := _slot_path(value, (*path, i))) is not None:
            return found
    return None


def _replaced(rendered: Rendered, path: tuple[int, ...], value: str) -> Rendered:
    dynamic = list(rendered.dynamic)
    head, *rest = path
    dynamic[head] = _replaced(dynamic[head], tuple(rest), value) if rest else value  # type: ignore[arg-type]
    copy = Rendered(rendered.static, dynamic, rendered._fingerprint)
    copy._names = rendered._names
    return copy


class _Rows(Exception):
    """A value holds a model instance or a QuerySet: its render is the connection's own."""


def key(component: Component) -> tuple[t.Any, ...] | None:
    """What two renders of a declared class must agree on to be the same render; None if it cannot be told.

    The fields are read off the instance, every one the render may read: not
    through ``model_dump_json``, which leaves out a ``Field(exclude=True)``, runs
    field serializers and computed fields, and writes a model instance as its
    pk. A value that holds a model instance or a QuerySet anywhere gives no key
    at all: two connections can hold the same row with different attributes.
    """
    from .component import ALWAYS_EXCLUDED

    try:
        fields = to_json(
            [_keyed(getattr(component, name)) for name in type(component).model_fields if name not in ALWAYS_EXCLUDED]
        )
    except Exception:  # _Rows, or a value with no JSON form: not to be told apart, so not shared
        return None
    return (
        type(component)._fqn,
        component.id,
        fields,
        translation.get_language(),
        timezone.get_current_timezone_name(),
    )


_SCALARS = (str, int, float, bool, type(None))


def _keyed(value: t.Any) -> t.Any:
    """``value`` as plain data for the key, its type named wherever JSON would lose it.

    ``1`` and ``True``, a list and a tuple, a date and its string render
    differently and must not share. A set's order may differ between equal
    sets: that only keeps two renders apart.
    """
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (models.Model, models.QuerySet)):
        raise _Rows
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    if isinstance(value, BaseModel):
        # The extras of an extra="allow" model render as its fields do: {{ prefs.theme }}
        extra = value.__pydantic_extra__ or {}
        fields = [[name, _keyed(getattr(value, name))] for name in type(value).model_fields]
        return [kind, fields, [[name, _keyed(item)] for name, item in extra.items()]]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [kind, [[field.name, _keyed(getattr(value, field.name))] for field in dataclasses.fields(value)]]
    if isinstance(value, Mapping):
        return [kind, [[_keyed(name), _keyed(item)] for name, item in value.items()]]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [kind, [_keyed(item) for item in value]]
    return [kind, value]


#: What a leader whose render raised or was cancelled leaves its key with: the next one to come renders
_AGAIN: t.Final = object()


class _Message:
    """The renders of one message: key -> future render, and the marked HTML they hold."""

    __slots__ = ("renders", "size")

    def __init__(self) -> None:
        self.renders: dict[tuple[t.Any, ...], asyncio.Future[t.Any]] = {}
        self.size = 0


class _Store:
    """The renders of the messages one event loop handles: message id -> key -> future render.

    A future's result is the ``Shared`` render, None when the leader's render
    cannot be taken (nothing drawn, or not shareable), or ``_AGAIN``.

    A message is dropped ``KEEP_SECONDS`` after its first claim, by a timer, so
    nothing is held once the traffic stops; and the oldest go first while the
    renders kept are more than ``KEEP_BYTES`` of HTML or ``KEEP_RENDERS``. Whether
    another connection will come for a render cannot be known -- each reaches
    the message when its loop gets to it -- so a render nobody takes is held for
    that while too, and no longer.
    """

    def __init__(self) -> None:
        self.messages: OrderedDict[str, _Message] = OrderedDict()
        self.size = 0
        self.renders = 0
        self.signer = _Signer()

    def claim(self, message: str, key: tuple[t.Any, ...]) -> tuple[_Message, asyncio.Future[t.Any], bool]:
        """The future render for ``key`` in ``message``, and whether the caller is to render it."""
        loop = asyncio.get_running_loop()
        entry = self.messages.get(message)
        if entry is None:
            entry = self.messages[message] = _Message()
            loop.call_later(KEEP_SECONDS, self.forget, message, entry)
        future = entry.renders.get(key)
        if future is not None and not (future.done() and future.result() is _AGAIN):
            return entry, future, False
        if future is None:
            self.renders += 1
        future = entry.renders[key] = loop.create_future()
        self._trim()
        return entry, future, True

    def settle(self, message: str, entry: _Message, future: asyncio.Future[t.Any], outcome: t.Any) -> None:
        """The leader's ``outcome`` for ``future``, counted while ``entry`` is kept."""
        future.set_result(outcome)
        if isinstance(outcome, Shared) and self.messages.get(message) is entry:
            entry.size += len(outcome.html)
            self.size += len(outcome.html)
            self._trim()

    def forget(self, message: str, entry: _Message) -> None:
        if self.messages.get(message) is entry:
            del self.messages[message]
            self.size -= entry.size
            self.renders -= len(entry.renders)

    def _trim(self) -> None:
        while self.messages and (self.size > KEEP_BYTES or self.renders > KEEP_RENDERS):
            oldest, entry = next(iter(self.messages.items()))
            self.forget(oldest, entry)


class _Signer:
    """Signs the tokens of the connections that take a render, off the loop, in as few trips as come.

    Each taker asks for its own component's token. One trip signs every token
    asked for meanwhile, and the next trip those asked for during it: a trip
    per taker cost more on the loop (scheduling it, waking the worker) than the
    signature itself.
    """

    def __init__(self) -> None:
        self.pending: list[tuple[Component, asyncio.Future[str]]] = []
        self.task: asyncio.Task[None] | None = None

    async def sign(self, component: Component) -> str:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.pending.append((component, future))
        if self.task is None:
            self.task = asyncio.get_running_loop().create_task(self._drain())
        return await future

    async def _drain(self) -> None:
        batch: list[tuple[Component, asyncio.Future[str]]] = []
        try:
            while self.pending:
                batch, self.pending = self.pending, []
                signed = await db(_sign_each)([component for component, _ in batch])
                for (_, future), (token, error) in zip(batch, signed, strict=True):
                    if not future.done():  # its connection went meanwhile
                        if error is None:
                            future.set_result(t.cast(str, token))
                        else:
                            future.set_exception(error)
                batch = []
        except BaseException as error:  # the trip failed, or the loop is closing: nobody is left waiting
            for _, future in [*batch, *self.pending]:
                if not future.done():
                    future.set_exception(error if isinstance(error, Exception) else asyncio.CancelledError())
            self.pending = []
            if not isinstance(error, Exception):  # the waiters have the error; nobody awaits this task
                raise
        finally:
            self.task = None


def _sign_each(components: list[Component]) -> list[tuple[str | None, Exception | None]]:
    """Each component's token, or what signing it raised: one connection's failure is its own."""
    from .state import sign_state

    signed: list[tuple[str | None, Exception | None]] = []
    for component in components:
        try:
            signed.append((sign_state(component), None))
        except Exception as error:
            signed.append((None, error))
    return signed


_stores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Store] = weakref.WeakKeyDictionary()


def _store() -> _Store:
    loop = asyncio.get_running_loop()
    store = _stores.get(loop)
    if store is None:
        store = _stores[loop] = _Store()
    return store


async def render(wire: WireviewMeta, component: Component, repo: Repo) -> tuple[Rendered, str] | None:
    """The live render of a declared class: shared within a broadcast, with this connection's state token.

    Returns the render and its marked HTML (the slot in place of the token), or
    None when nothing is to be drawn (the component is frozen or redirected).

    The token is signed off the event loop, as ``{% tag_header %}`` signs it in
    a render of its own: the state's JSON runs computed fields and dumps a
    QuerySet field's ids, either of which may query the database.
    """
    if wire._is_frozen or wire._redirected_to:
        # As a render of its own would find out in its trip: nothing more is drawn
        return None
    check = verifying()
    message = _message.get()
    found = key(component) if message is not None else None
    if message is None or found is None:
        drawn = await _render_own(wire, component, repo, check)
    else:
        # Hold the component's background work from here to the token, as a render of its
        # own does: the state signed is the state the key named (#138).
        with wire._render_gate.rendering():
            drawn = await _render_for(message, found, wire, component, repo, check)
    if drawn is None:
        return None
    shared, token = drawn
    if isinstance(shared, str):  # out of scope at render time: rendered and parsed as any other
        return Rendered.from_marked_html(shared), shared
    return (shared.with_state(token) if token is not None else shared.rendered), shared.html


_Drawn: t.TypeAlias = "tuple[Shared | str, str | None] | None"


async def _render_for(
    message: str, found: tuple[t.Any, ...], wire: WireviewMeta, component: Component, repo: Repo, check: bool
) -> _Drawn:
    """The render of ``found`` in ``message``: rendered here by the first to come, taken by the rest."""
    store = _store()
    while True:
        entry, future, lead = store.claim(message, found)
        if lead:
            outcome: t.Any = _AGAIN
            try:
                drawn = await _render_own(wire, component, repo, check)
                outcome = drawn[0] if drawn is not None and isinstance(drawn[0], Shared) else None
                return drawn
            finally:
                # Raised or cancelled: whoever waits renders it again, one of them for the rest
                store.settle(message, entry, future, outcome)
        taken = await asyncio.shield(future)
        if taken is _AGAIN:
            continue
        if taken is None:
            return await _render_own(wire, component, repo, check)
        token = None
        if check:
            own = await _render_own(wire, component, repo, check)
            _compare(component, taken, own[0] if own is not None else None)
            token = own[1] if own is not None else None
        elif taken.state_path is not None:
            token = await store.signer.sign(component)
        # Drawn as a render of its own would have: what reads this goes the same way
        wire.template_evaluated = True
        return taken, token


async def _render_own(wire: WireviewMeta, component: Component, repo: Repo, check: bool) -> _Drawn:
    """Render ``component`` with the slot for its token, and the token signed in the same trip.

    The render alone, its HTML, when it cannot be shared: rendered again as any
    other, the token in place.
    """
    with watching(component, check):
        html = await wire._render_marked(component, repo, state_slot=True, watch=check)
    if html is None:
        return None
    token, wire._slot_token = wire._slot_token, None
    shared = Shared.parse(html)
    if shared is None:
        cls = type(component)
        _tell_once(cls, "its render draws another component or holds data-state more than once")
        _SCOPE[cls] = (cls._meta, False)
        # The slot is this connection's to fill: render again as any other component
        html = await wire._render_marked(component, repo, state_slot=False, watch=False)
        return (html, None) if html is not None else None
    return shared, token


def _compare(component: Component, taken: Shared, own: Shared | str | None) -> None:
    mine = own.html if isinstance(own, Shared) else own
    if mine == taken.html:
        return
    at = next(
        (i for i, (a, b) in enumerate(zip(mine or "", taken.html)) if a != b), min(len(mine or ""), len(taken.html))
    )
    raise SharedRenderError(
        f"{type(component).__qualname__} ({component.id}) declares Meta.shared_render, but this connection "
        f"renders it otherwise than the connection whose render it would take, with the same fields: "
        f"...{(mine or '')[max(0, at - 40) : at + 40]!r} here, ...{taken.html[max(0, at - 40) : at + 40]!r} "
        f"there. Its render reads something of the viewer or of the connection; drop shared_render, or move "
        f"that into a field."
    )


_TOLD: weakref.WeakSet[type] = weakref.WeakSet()


def _tell_once(cls: type, why: str) -> None:
    if cls not in _TOLD:
        _TOLD.add(cls)
        log.warning("%s declares Meta.shared_render, but its renders are not shared: %s", cls.__qualname__, why)


# -- watching -------------------------------------------------------------------------------


def _read_error(owner: str, name: str) -> SharedRenderError:
    return SharedRenderError(
        f"{owner} declares Meta.shared_render, but its render read {name!r}, which differs from viewer to "
        f"viewer: every connection that handles a broadcast would show what the first one rendered. Read it "
        f"in a handler or joined() and keep the result in a field, or drop shared_render."
    )


class _Watched:
    """Stands in for a name a declared class must not read while it renders. Any use raises.

    The use is written down first, in the render's ``reads``: a template that
    swallows the error -- Django's ``{% if a and b %}`` makes any exception of
    an operand False -- still raises once the render is over (``watching``).
    """

    __slots__ = ("_name", "_owner", "_reads")

    def __init__(self, name: str, owner: str, reads: list[str]) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_reads", reads)

    def _raise(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        name = object.__getattribute__(self, "_name")
        object.__getattribute__(self, "_reads").append(name)
        raise _read_error(object.__getattribute__(self, "_owner"), name)

    def __getattr__(self, attr: str) -> t.Any:
        self._raise()

    __getitem__ = __str__ = __bool__ = __iter__ = __len__ = __eq__ = __html__ = __contains__ = _raise  # type: ignore[assignment]
    __hash__ = None  # type: ignore[assignment]


@contextmanager
def watching(component: Component, check: bool) -> t.Iterator[None]:
    """While a declared class renders, its ``user`` and ``session`` raise on use, when ``check``.

    The fields are swapped on the instance and put back after, so a property
    reading ``self.user`` is caught as well as a template. Only the render's
    stretch is watched: handlers and ``joined()`` read them as ever. A use the
    render swallowed raises here, when the render is over.
    """
    if not check:
        yield
        return
    owner = type(component).__qualname__
    reads: list[str] = []
    component.wire._watched_reads = reads
    kept = {name: component.__dict__[name] for name in WATCHED_FIELDS if name in component.__dict__}
    for name in kept:
        component.__dict__[name] = _Watched(name, owner, reads)
    try:
        yield
    finally:
        component.__dict__.update(kept)
        component.wire._watched_reads = None
    if reads:
        raise _read_error(owner, reads[0])


def watched_context(component: Component) -> dict[str, t.Any]:
    """The request's names a render's context may not read, for the names the component does not have."""
    owner = type(component).__qualname__
    reads = component.wire._watched_reads
    if reads is None:  # rendered outside ``watching``: written down where nothing reads it, raising all the same
        reads = []
    return {name: _Watched(name, owner, reads) for name in WATCHED_CONTEXT}
