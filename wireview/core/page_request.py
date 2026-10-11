"""What a component's template finds at ``context.request``: the page's query, and nothing else (#210).

A component's template renders with a context of its own, never the page's
``RequestContext`` -- on the HTTP render as on every live render, where there is
no request at all, only the socket. Django's ``{% querystring %}`` (5.1+) reads
``context.request.GET``, an attribute of the context rather than a name in it,
and raised ``AttributeError`` on every render of a component.

Each render of a component's template now has a ``PageRequest`` there: its
``GET`` is the page's query as the repository knows it (``page_query``) -- the
request's on the HTTP render; on a connection, what the page last told under
``params_changed`` or ``navigated`` (a boosted visit, ``push_to``,
``replace_to``, Back) or what the server set. Phoenix renders from the same
place: ``handle_params`` gets the uri. Nothing else of a request is there, on
either render, so a template behaves the same on both; anything else raises an
``AttributeError`` that says so. ``request`` is still no name in the context.

A render made once for many viewers -- a shared render being verified, a
``Broadcast`` item -- watches ``request`` (``core/watched.py``): there ``GET``
raises the render's own error. A ``Meta.shared_render`` class whose template
holds a ``{% querystring %}`` that reads the request is out of scope
(``wireview.W019``).
"""

from __future__ import annotations

import typing as t

from django.http import QueryDict
from django.template import Context
from django.template.exceptions import TemplateDoesNotExist

from .watched import name_of

if t.TYPE_CHECKING:
    from django.template import Template

#: Where a function component's template finds what drew it (``function_components``)
_DRAWER = "wireview_drawer"
_PAGE = "wireview_page"


class PageRequest:
    """``context.request`` in a component's template: ``GET``, the page's query, and nothing more."""

    __slots__ = ("_query",)

    def __init__(self, query: t.Callable[[], QueryDict]) -> None:
        self._query = query

    @property
    def GET(self) -> QueryDict:  # noqa: N802 -- the name Django's tags read
        return self._query()

    def __getattr__(self, name: str) -> t.NoReturn:
        # An AttributeError still: ``{% url %}`` asks for ``current_app`` and goes on without it
        raise AttributeError(
            f"context.request in a component's template holds only GET, the page's query, not {name!r}: "
            f"a component renders on its socket too, where there is no request (wireview #210). Read it "
            f"in the component -- self.user, self.wire.params, a field -- and give the template the value."
        )

    def __repr__(self) -> str:
        return "<PageRequest GET>"


def page_request(context: t.Mapping[str, t.Any]) -> PageRequest | None:
    """What ``context.request`` is in a render of this component context, or None when it names no page."""
    request: t.Any = context.get("request")
    if name_of(request) == "request":  # made once for many viewers: reading the query raises
        return PageRequest(lambda: request.GET)
    query: t.Any = getattr(context.get("wireview_repository"), "page_query", None)
    if not callable(query):
        # A render with no context of the component's: a stream item, a function component's template
        drawer = context.get("this")
        if drawer is None:
            drawer = context.get(_DRAWER)
        query = getattr(getattr(drawer, "wire", None), "query", None)
    if callable(query):
        return PageRequest(t.cast("t.Callable[[], QueryDict]", query))
    # A function component an HTTP page draws before any component made the repository
    page_query: t.Any = getattr(getattr(context.get(_PAGE), "request", None), "GET", None)
    if page_query is not None:
        return PageRequest(lambda: page_query)
    return None


def render(template: t.Any, context: dict[str, t.Any], make: type[Context] = Context) -> str:
    """Render a component's ``template`` -- a backend's wrapper or a ``Template`` -- with ``context``.

    As the backend renders a dict without a request (``make_context``: the dict
    itself, not a copy), with ``context.request`` the page's (``page_request``).
    ``make`` builds the ``Context``.
    """
    from django.template.backends.django import reraise

    backend = getattr(template, "backend", None)
    inner = t.cast("Template", getattr(template, "template", template))
    autoescape = backend.engine.autoescape if backend is not None else inner.engine.autoescape
    built = make(context, autoescape=autoescape)
    if (request := page_request(context)) is not None:
        built.request = request  # type: ignore[attr-defined]
    try:
        return inner.render(built)
    except TemplateDoesNotExist as exc:
        if backend is None:
            raise
        reraise(exc, backend)
