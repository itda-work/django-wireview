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
  (``handling``), and only for the same class, component id, fields (all of
  them but ``user``, ``wire`` and ``session``, those left out of the signed state
  too), active language and time zone. Anything else renders on its own.

The promise cannot be checked in full: a property may read anything. What can
be is checked where it is cheap. ``wireview.W019`` names a class whose Meta or
template puts it out of scope, and a render of such a class does not share.
With ``VERIFY_SHARED_RENDER`` (``DEBUG`` by default, and ``wireview.testing``),
a declared class's render that reads ``user``, ``session``, ``request``,
``perms``, ``csrf_token`` or ``messages`` raises, and every connection that took
a render from another renders on its own as well and raises if the two differ.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import secrets
import time
import typing as t
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone, translation
from django.utils.html import escape

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

#: How long, and for how many messages, a process keeps the renders of one. A
#: connection that reaches a message later than this renders on its own.
KEEP_SECONDS = 10.0
KEEP_MESSAGES = 64

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
    for expression in _expressions(node):
        var = getattr(expression, "var", None)
        lookups = getattr(var, "lookups", None)
        if not lookups:
            continue
        if lookups[0] in WATCHED_FIELDS or lookups[0] in WATCHED_CONTEXT:
            yield lookups[0]
        elif lookups[0] == "this" and len(lookups) > 1 and lookups[1] in WATCHED_FIELDS:
            yield f"this.{lookups[1]}"


def _expressions(node: Node) -> t.Iterator[t.Any]:
    kind = type(node).__name__
    if kind == "VariableNode":
        yield node.filter_expression  # type: ignore[attr-defined]
    elif kind == "IfNode":
        for condition, _branch in node.conditions_nodelists:  # type: ignore[attr-defined]
            yield from _condition_expressions(condition)
    elif kind == "ForNode":
        yield node.sequence  # type: ignore[attr-defined]
    elif kind == "WithNode":
        yield from node.extra_context.values()  # type: ignore[attr-defined]
    else:
        yield from getattr(node, "args", ()) or ()
        yield from (getattr(node, "kwargs", None) or {}).values()


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


def key(component: Component) -> tuple[t.Any, ...] | None:
    """What two renders of a declared class must agree on to be the same render; None if it cannot be told."""
    from .component import ALWAYS_EXCLUDED

    try:
        fields = component.model_dump_json(exclude=set(ALWAYS_EXCLUDED))
    except Exception:
        return None
    return (
        type(component)._fqn,
        component.id,
        fields,
        translation.get_language(),
        timezone.get_current_timezone_name(),
    )


#: What a leader whose render raised or was cancelled leaves its key with: the next one to come renders
_AGAIN: t.Final = object()


class _Store:
    """The renders of the messages one event loop handles: message id -> key -> future render.

    A future's result is the ``Shared`` render, None when the leader's render
    cannot be taken (nothing drawn, or not shareable), or ``_AGAIN``.
    """

    def __init__(self) -> None:
        self.messages: OrderedDict[str, tuple[float, dict[tuple[t.Any, ...], asyncio.Future[t.Any]]]] = OrderedDict()

    def claim(self, message: str, key: tuple[t.Any, ...]) -> tuple[asyncio.Future[t.Any], bool]:
        """The future render for ``key`` in ``message``, and whether the caller is to render it."""
        now = time.monotonic()
        while self.messages:
            oldest, (born, _renders) = next(iter(self.messages.items()))
            if len(self.messages) < KEEP_MESSAGES and now - born < KEEP_SECONDS:
                break
            del self.messages[oldest]
        entry = self.messages.get(message)
        if entry is None:
            entry = self.messages[message] = (now, {})
        renders = entry[1]
        future = renders.get(key)
        if future is not None and not (future.done() and future.result() is _AGAIN):
            return future, False
        future = renders[key] = asyncio.get_running_loop().create_future()
        return future, True


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
    """
    check = verifying()
    message = _message.get()
    found = key(component) if message is not None else None
    if message is None or found is None:
        shared = await _render_own(wire, component, repo, check)
    else:
        # Hold the component's background work from here to the token, as a render of its
        # own does: the state signed below is the state the key named (#138).
        with wire._render_gate.rendering():
            shared = await _render_for(message, found, wire, component, repo, check)
    if shared is None:
        return None
    if isinstance(shared, str):  # out of scope at render time: rendered and parsed as any other
        return Rendered.from_marked_html(shared), shared
    from ..templatetags.wireview import sign_state

    return shared.with_state(sign_state(component)), shared.html


async def _render_for(
    message: str, found: tuple[t.Any, ...], wire: WireviewMeta, component: Component, repo: Repo, check: bool
) -> Shared | str | None:
    """The render of ``found`` in ``message``: rendered here by the first to come, taken by the rest."""
    while True:
        future, lead = _store().claim(message, found)
        if lead:
            outcome: t.Any = _AGAIN
            try:
                shared = await _render_own(wire, component, repo, check)
                outcome = shared if isinstance(shared, Shared) else None
                return shared
            finally:
                # Raised or cancelled: whoever waits renders it again, one of them for the rest
                future.set_result(outcome)
        taken = await asyncio.shield(future)
        if taken is _AGAIN:
            continue
        if taken is None:
            return await _render_own(wire, component, repo, check)
        if check:
            _compare(component, taken, await _render_own(wire, component, repo, check))
        return taken


async def _render_own(wire: WireviewMeta, component: Component, repo: Repo, check: bool) -> Shared | str | None:
    """Render ``component`` with the slot for its token; the HTML alone when it cannot be shared."""
    with watching(component, check):
        html = await wire._render_marked(component, repo, state_slot=True, watch=check)
    if html is None:
        return None
    shared = Shared.parse(html)
    if shared is None:
        cls = type(component)
        _tell_once(cls, "its render draws another component or holds data-state more than once")
        _SCOPE[cls] = (cls._meta, False)
        # The slot is this connection's to fill: render again as any other component
        return await wire._render_marked(component, repo, state_slot=False, watch=False)
    return shared


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


class _Watched:
    """Stands in for a name a declared class must not read while it renders. Any use raises."""

    __slots__ = ("_name", "_owner")

    def __init__(self, name: str, owner: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_owner", owner)

    def _raise(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        name = object.__getattribute__(self, "_name")
        owner = object.__getattribute__(self, "_owner")
        raise SharedRenderError(
            f"{owner} declares Meta.shared_render, but its render read {name!r}, which differs from viewer to "
            f"viewer: every connection that handles a broadcast would show what the first one rendered. Read it "
            f"in a handler or joined() and keep the result in a field, or drop shared_render."
        )

    def __getattr__(self, attr: str) -> t.Any:
        self._raise()

    __getitem__ = __str__ = __bool__ = __iter__ = __len__ = __eq__ = __html__ = __contains__ = _raise  # type: ignore[assignment]
    __hash__ = None  # type: ignore[assignment]


@contextmanager
def watching(component: Component, check: bool) -> t.Iterator[None]:
    """While a declared class renders, its ``user`` and ``session`` raise on use, when ``check``.

    The fields are swapped on the instance and put back after, so a property
    reading ``self.user`` is caught as well as a template. Only the render's
    stretch is watched: handlers and ``joined()`` read them as ever.
    """
    if not check:
        yield
        return
    owner = type(component).__qualname__
    kept = {name: component.__dict__[name] for name in WATCHED_FIELDS if name in component.__dict__}
    for name in kept:
        component.__dict__[name] = _Watched(name, owner)
    try:
        yield
    finally:
        component.__dict__.update(kept)


def watched_context(component: Component) -> dict[str, t.Any]:
    """The request's names a render's context may not read, for the names the component does not have."""
    owner = type(component).__qualname__
    return {name: _Watched(name, owner) for name in WATCHED_CONTEXT}
