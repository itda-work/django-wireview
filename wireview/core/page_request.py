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

A render many connections may take -- a ``Meta.shared_render`` class's live
render (``wire._state_slot``), and the function components it draws -- has no
``context.request``, as in 1.5: the first connection's query would go to every
viewer, and a tag reading ``getattr(context, "request", None)`` goes on without
one. Being verified, and in a ``Broadcast`` item, ``request`` is watched
(``core/watched.py``): there ``GET`` raises the render's own error. A
``Meta.shared_render`` class whose template holds a ``{% querystring %}`` that
reads the request is out of scope (``wireview.W019``).

A component's template is a Django template (``django_template``): wireview's
tags and the render's markers are Django's, so another backend's template never
rendered live. A page's template may be any backend's.
"""

from __future__ import annotations

import typing as t

from django.core.exceptions import ImproperlyConfigured
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


def page_request(context: dict[str, t.Any]) -> PageRequest | None:
    """What ``context.request`` is in a render of this component context, or None when it names no page.

    The context's names are read as ``dict.get``: a ``Meta.lazy_properties``
    context computes a pending property on ``get`` (#187), and a component's
    own ``request`` property is the template's to read or not.
    """
    request: t.Any = dict.get(context, "request")
    if name_of(request) == "request":  # made once for many viewers: reading the query raises
        return PageRequest(lambda: request.GET)
    drawer: t.Any = dict.get(context, "this")
    if drawer is None:
        drawer = dict.get(context, _DRAWER)
    wire = getattr(drawer, "wire", None)
    if getattr(wire, "_state_slot", False):
        # A render the connections handling one broadcast may share: whose query would it be
        return None
    query: t.Any = getattr(dict.get(context, "wireview_repository"), "page_query", None)
    if not callable(query):
        # A render with no context of the component's: a stream item, a function component's template
        query = getattr(wire, "query", None)
    if callable(query):
        return PageRequest(t.cast("t.Callable[[], QueryDict]", query))
    # A function component an HTTP page draws before any component made the repository
    page_query: t.Any = getattr(getattr(dict.get(context, _PAGE), "request", None), "GET", None)
    if page_query is not None:
        return PageRequest(lambda: page_query)
    return None


def render(template: t.Any, context: dict[str, t.Any], make: type[Context] = Context) -> str:
    """Render a component's ``template`` -- a backend's wrapper or a ``Template`` -- with ``context``.

    As the backend renders a dict without a request (``make_context``: the dict
    itself, not a copy), with ``context.request`` the page's (``page_request``).
    ``make`` builds the ``Context``. ``template`` is a Django template
    (``django_template``).
    """
    from django.template.backends.django import reraise

    backend = getattr(template, "backend", None)
    inner = t.cast("Template", getattr(template, "template", template))
    autoescape = (backend.engine if backend is not None else inner.engine).autoescape
    built = make(context, autoescape=autoescape)
    if (request := page_request(context)) is not None:
        built.request = request  # type: ignore[attr-defined]
    try:
        return inner.render(built)
    except TemplateDoesNotExist as exc:
        if backend is None:
            raise
        reraise(exc, backend)


def is_django(template: t.Any) -> bool:
    """Whether ``template`` is Django's: a ``DjangoTemplates`` backend's wrapper, or a ``Template``."""
    from django.template.backends.django import DjangoTemplates
    from django.template.base import Template

    return isinstance(template, Template) or isinstance(getattr(template, "backend", None), DjangoTemplates)


def not_django(template: t.Any, owner: str, name: str) -> str:
    """Why ``template``, found for ``owner`` under ``name``, cannot render: it is another backend's."""
    backend = getattr(template, "backend", None)
    engine = type(backend) if backend is not None else type(template)
    alias = getattr(backend, "name", None)
    return (
        f"{owner}'s template {name!r} was found by the {alias!r} template engine "
        f"({engine.__module__}.{engine.__qualname__}), not by a DjangoTemplates one: wireview renders a "
        f"component's template with Django's template engine only -- its tags ({{% tag_header %}}, {{% on %}}, "
        f"{{% component %}}) and the markers of a live render are Django's. Put the template in a "
        f"DjangoTemplates engine's DIRS or app directories, ahead of the other engine in TEMPLATES. "
        f"A page's template may use any engine."
    )


def django_template(template: t.Any, owner: str, name: str) -> t.Any:
    """``template`` when it is Django's, as a component's template must be; ``ImproperlyConfigured`` if not."""
    if not is_django(template):
        raise ImproperlyConfigured(not_django(template, owner, name))
    return template
