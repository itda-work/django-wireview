"""Django's ``{% querystring %}`` in a component's template (#210, wireview/core/page_request.py).

The tag (Django 5.1+) reads ``context.request.GET``. A component's template
renders with a context of its own, never the page's ``RequestContext``, so it
raised ``AttributeError`` on every render -- the HTTP one and every live one,
where a join that raises fails. These hold now:

- ``context.request`` is the page's query and nothing else, on both renders:
  ``GET``, and an ``AttributeError`` that says so for anything else; ``request``
  is still no name in the context, and ``{% url %}`` goes on without ``current_app``
- the HTTP render draws what Django draws for the request: every value of a
  repeated key, a ``.json`` key as it was written
- a live render draws what Django would for the page's query as the socket knows
  it: what the page told under ``params_changed`` (the first one, a ``push_to``,
  Back) or ``navigated`` (a boosted visit), and what a component set
- the same in a LiveComponent, a function component and a stream item
- a ``Meta.shared_render`` class whose template holds one that reads the query
  is out of scope (W019); one given its own mapping reads nothing of the page
- verified, a shared render that reads it raises; a ``Broadcast`` item that
  reads it raises always (tests/test_broadcast.py)
- on a real socket: the join, an event, and a ``push_to`` the page follows
- ``mount()`` draws its ``params``, and a ``follow_push()``'s

The browser side, a boosted visit and a ``push_to`` followed in the address bar,
is tests/test_history_e2e.py.
"""

import json
import re
import typing as t
from dataclasses import dataclass
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from testproj.outbound import RecordingOutbound
from testproj.wireview_setting import set_wireview

from wireview import Component, LiveComponent, function_component
from wireview import testing as wireview_testing
from wireview.consumer import WireviewConsumer
from wireview.core import shared_render
from wireview.core.meta import WireviewMeta
from wireview.core.page_request import PageRequest
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LINKS = '<a href="{% querystring page=2 %}">2</a><a href="{% querystring %}">here</a><b>{{ n }}</b>'
TEMPLATES = {
    "qs/links.html": "{% load wireview %}<div {% tag_header %}>" + LINKS + "</div>",
    "qs/page.html": '{% load wireview %}{% component "QsLinks" id="qs" %}',
    "qs/own.html": (
        '{% load wireview %}<div {% tag_header %}><a href="{% querystring mine page=2 %}">2</a>'
        "<i>{{ request }}</i></div>"
    ),
    "qs/url.html": '{% load wireview %}<div {% tag_header %}><a href="{% url "admin:index" %}">c</a></div>',
    "qs/path.html": "{% load wireview qs_tags %}<div {% tag_header %}>{% qs_path %}</div>",
    "qs/parent.html": (
        '{% load wireview %}<div {% tag_header %}>{% live_component "QsChild" id="kid" %}{% func "qs_fn" %}</div>'
    ),
    "qs/child.html": "{% load wireview %}<i {% live_tag_header %}>" + LINKS + "</i>",
    "qs/fn.html": LINKS,
    "qs/items.html": '{% load wireview %}<ul {% tag_header %} wire-stream="rows"></ul>',
    "qs/items_item.html": '<li id="rows-{{ item.id }}"><a href="{% querystring row=item.id %}">r</a></li>',
    "qs/shared.html": '{% load wireview %}<div {% tag_header %}>{{ n }}{% include "qs/shared_part.html" %}</div>',
    "qs/shared_part.html": '<a href="{% querystring page=2 %}">2</a>',
    "qs/shared_own.html": (
        '{% load wireview %}<div {% tag_header %}>{{ n }}<a href="{% querystring mine %}">m</a></div>'
    ),
}


class QsLinks(Component):
    class Meta:
        template_name = "qs/links.html"

    n: int = 0

    async def bump(self):
        self.n += 1

    async def go(self, to: str):
        await self.wire.push_to(to)

    async def set_page(self, page: str):
        # A component that changes the params changes the page's query: the server tells the page
        self.wire.params["page"] = page


class QsOwn(Component):
    """Gives the tag a mapping of its own: the page's query is not read."""

    class Meta:
        template_name = "qs/own.html"

    @property
    def mine(self) -> QueryDict:
        return QueryDict("sort=name")  # Django 5.2 takes a QueryDict only


class QsUrl(Component):
    class Meta:
        template_name = "qs/url.html"


class QsPath(Component):
    class Meta:
        template_name = "qs/path.html"


class QsChild(LiveComponent):
    class Meta:
        template_name = "qs/child.html"

    n: int = 0


@function_component(template="qs/fn.html")
def qs_fn():
    return {"n": 0}


class QsParent(Component):
    class Meta:
        template_name = "qs/parent.html"


@dataclass
class Row:
    id: int


class QsItems(Component):
    class Meta:
        template_name = "qs/items.html"


class QsShared(Component):
    """Declares a shared render, and reads the page's query in a template it includes: out of scope."""

    class Meta:
        template_name = "qs/shared.html"
        shared_render = True
        subscriptions = {"qs-shared"}

    n: int = 0

    async def notification(self, channel: str, **kwargs):
        pass


class QsSharedOwn(Component):
    """Declares a shared render and gives the tag its own mapping: in scope."""

    class Meta:
        template_name = "qs/shared_own.html"
        shared_render = True

    mine: dict[str, str] = {"sort": "name"}


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)],
                    "context_processors": ["django.template.context_processors.request"],
                    "libraries": {"qs_tags": "testproj.qs_tags"},
                },
            }
        ]
    ):
        shared_render._SCOPE.clear()
        yield
    shared_render._SCOPE.clear()


def django_draws(query: str, tag: str = "{% querystring page=2 %}") -> str:
    """What Django draws for ``tag`` on a page requested with ``query``: the HTTP render's."""
    request = RequestFactory().get(f"/?{query}" if query else "/")
    context = Context(autoescape=True)
    context.request = request  # type: ignore[attr-defined]
    return Template(tag).render(context)


def links(query: str) -> str:
    """``LINKS`` with ``n`` 0, as Django draws it for ``query``."""
    return f'<a href="{django_draws(query)}">2</a><a href="{django_draws(query, "{% querystring %}")}">here</a>'


#: Queries whose output differs from a dict's: a repeated key, a .json key, a blank value, an encoded one
QUERIES = ["", "q=a", "tag=x&tag=y&q=%ED%95%9C", 'f.json={"a":1}&q=', "page=7&sort=name"]


class Page:
    """One tab: a session started, the page's query told as the client tells it on open, a component joined."""

    def __init__(self, session: WireviewSession, outbound: RecordingOutbound, id: str) -> None:
        self.session = session
        self.outbound = outbound
        self.id = id

    @classmethod
    async def open(cls, component: type[Component] = QsLinks, query: str = "", id: str = "qs") -> "Page":
        outbound = RecordingOutbound()
        live = WireviewSession(outbound, user=AnonymousUser(), channel_name=f"qs-{uuid4().hex[:8]}")
        await live.start(session=SessionView({}), vsn=PROTOCOL_VERSION)
        page = cls(live, outbound, id)
        await page.tell(query)
        state = sign_state(component(id=id, user=AnonymousUser(), wire=WireviewMeta(params={})))
        await live.handle_message({"command": "join", "payload": {"name": component._name, "state": state}})
        assert outbound.renders(), f"{component._name} did not join"
        return page

    async def tell(self, query: str, command: str = "params_changed") -> None:
        """What the client sends: the address bar, and its params parsed as URLSearchParams parses them."""
        params = dict(QueryDict(query).items())
        payload: dict[str, t.Any] = {"uri": f"http://testserver/qs/?{query}", "params": params}
        if command == "navigated":
            payload["carried"] = [self.id]
        await self.session.handle_message({"command": command, "payload": payload})

    async def event(self, command: str, **args: t.Any) -> None:
        await self.session.handle_message(
            {
                "command": "user_event",
                "payload": {"id": self.id, "command": command, "implicit_args": {}, "explicit_args": args},
            }
        )

    def html(self, id: str | None = None) -> str:
        last = self.session.repo.get(id or self.id).wire._last_rendered
        assert last is not None
        return last.to_html()


# -- context.request ------------------------------------------------------------------------


@pytest.mark.unit
def test_the_request_is_the_query_and_says_so_for_anything_else():
    query = QueryDict("q=a")
    request = PageRequest(lambda: query)
    assert request.GET is query
    with pytest.raises(AttributeError, match="holds only GET, the page's query, not 'user'.*#210"):
        request.user  # noqa: B018
    assert not hasattr(request, "path"), "an AttributeError: a tag that asks with getattr goes on"


def test_http_the_render_draws_what_django_draws_for_the_request():
    for query in QUERIES:
        request = RequestFactory().get(f"/qs/?{query}")
        request.user = AnonymousUser()
        from django.template import engines

        html = engines["django"].get_template("qs/page.html").render({}, request)
        assert links(query) in html, query


def test_http_a_mapping_of_its_own_reads_no_query_and_request_is_no_name():
    request = RequestFactory().get("/qs/?q=a")
    request.user = AnonymousUser()
    view_html = QsOwn(id="own", user=AnonymousUser(), wire=WireviewMeta(params={"q": "a"}))
    from wireview.repository import ComponentRepository

    html = view_html._render(ComponentRepository(is_live=False, params={"q": "a"}, request=request)) or ""
    assert '<a href="?sort=name&amp;page=2">2</a>' in html
    assert "<i></i>" in html, "{{ request }} is empty, as it always was in a component's template"


def test_http_a_tag_that_reads_more_of_the_request_is_told():
    request = RequestFactory().get("/qs/?q=a")
    from wireview.repository import ComponentRepository

    component = QsPath(id="path", user=AnonymousUser(), wire=WireviewMeta(params={"q": "a"}))
    with pytest.raises(AttributeError, match="holds only GET, the page's query, not 'path'"):
        component._render(ComponentRepository(is_live=False, params={"q": "a"}, request=request))


# -- live renders ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("query", QUERIES)
async def test_the_join_draws_what_the_http_render_drew(query):
    page = await Page.open(query=query)
    assert links(query) in page.html()


@pytest.mark.asyncio
async def test_a_new_query_the_page_tells_is_drawn():
    """``push_to`` and Back come back as ``params_changed``; a boosted visit that carries it as ``navigated``."""
    page = await Page.open(query="q=a")
    await page.tell("q=b&tag=x&tag=y")
    assert links("q=b&tag=x&tag=y") in page.html()
    await page.tell("q=c", command="navigated")
    assert links("q=c") in page.html()
    await page.event("bump")
    assert links("q=c") + "<b>1</b>" in page.html(), "an event's render draws the same query"


@pytest.mark.asyncio
async def test_a_query_a_component_set_is_drawn_and_told():
    page = await Page.open(query="q=a&tag=x&tag=y")
    await page.event("set_page", page="5")
    assert links("q=a&tag=y&page=5") in page.html(), "the params, the server's: a repeated key keeps its last"
    assert ("set_query_string", {"qs": "q=a&tag=y&page=5"}) in page.outbound.commands
    await page.event("bump")
    assert links("q=a&tag=y&page=5") in page.html()


@pytest.mark.asyncio
async def test_url_goes_on_without_the_requests_namespace():
    page = await Page.open(QsUrl, query="q=a", id="url")
    assert '<a href="/admin/">c</a>' in page.html()


@pytest.mark.asyncio
async def test_a_tag_that_reads_more_of_the_request_fails_the_join_and_says_why(caplog):
    page = await Page.open(query="q=a")
    state = sign_state(QsPath(id="path", user=AnonymousUser(), wire=WireviewMeta(params={})))
    with caplog.at_level("ERROR", logger="wireview"):
        await page.session.handle_message({"command": "join", "payload": {"name": "QsPath", "state": state}})
    assert "holds only GET, the page's query, not 'path'" in caplog.text


@pytest.mark.asyncio
async def test_a_live_component_and_a_function_component_draw_it_too():
    page = await Page.open(QsParent, query="tag=x&tag=y", id="parent")
    assert links("tag=x&tag=y") in page.html(), "the function component's"
    assert links("tag=x&tag=y") in page.html("kid")
    await page.tell("q=b")
    assert links("q=b") in page.html()
    assert links("q=b") in page.html("kid")


@pytest.mark.asyncio
async def test_a_stream_item_draws_it():
    """An item renders with a context of its own: the component's wire has the page's query (#210)."""
    page = await Page.open(QsItems, query="tag=x&tag=y", id="items")
    component = page.session.repo.get("items")
    html = await component._render_stream_item("qs/items_item.html", Row(3))
    assert html == f'<li id="rows-3"><a href="{django_draws("tag=x&tag=y", "{% querystring row=3 %}")}">r</a></li>'
    await page.tell("q=b")
    html = await component._render_stream_item("qs/items_item.html", Row(3))
    assert html == f'<li id="rows-3"><a href="{django_draws("q=b", "{% querystring row=3 %}")}">r</a></li>'


# -- renders many viewers share -------------------------------------------------------------


@pytest.mark.unit
def test_a_shared_render_that_reads_the_query_is_out_of_scope():
    assert any("request (through qs/shared_part.html)" in why for why in shared_render.out_of_scope(QsShared))
    assert shared_render.out_of_scope(QsSharedOwn) == [], "its own mapping reads nothing of the page"


@pytest.mark.asyncio
async def test_out_of_scope_each_viewer_draws_its_own_query(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    pages = [await Page.open(QsShared, query="q=a"), await Page.open(QsShared, query="q=b")]
    data = {"channel": "qs-shared", "kwargs": {}, "message_id": uuid4().hex}
    for page in pages:
        page.session.repo.get("qs").n = 1
    for page in pages:
        await page.session.notification(dict(data))
    for page, query in zip(pages, ["q=a", "q=b"], strict=True):
        assert f'1<a href="{django_draws(query)}">2</a>' in page.html()


@pytest.mark.asyncio
async def test_a_verified_shared_render_that_reads_the_query_raises(monkeypatch):
    monkeypatch.setattr(shared_render, "_template_reasons", lambda cls: [])  # the check missed it
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=True)
    mounted = await wireview_testing.mount(QsShared, id="qs", params={"q": "a"})
    with pytest.raises(shared_render.SharedRenderError, match="its render read 'request'"):
        await mounted.render_diff()


# -- a socket -------------------------------------------------------------------------------


def _hrefs(message: dict[str, t.Any]) -> list[str]:
    return re.findall(r'href=\\?"([^"\\]*)', json.dumps(message["payload"]["diff"]))


@pytest.mark.asyncio
async def test_on_a_socket_the_join_an_event_and_a_push_to():
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), f"/__wireview__?vsn={PROTOCOL_VERSION}")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:

        async def receive(command: str) -> dict[str, t.Any]:
            while (message := await communicator.receive_json_from(timeout=5))["command"] != command:
                pass
            return message

        # The client tells the address bar on open, before its joins
        query = "q=a&tag=x&tag=y"
        await communicator.send_json_to(
            {
                "command": "params_changed",
                "payload": {"uri": f"http://testserver/qs/?{query}", "params": dict(QueryDict(query).items())},
            }
        )
        state = await sync_to_async(sign_state)(QsLinks(id="qs", user=AnonymousUser(), wire=WireviewMeta(params={})))
        await communicator.send_json_to({"command": "join", "payload": {"name": "QsLinks", "state": state}})
        render = await receive("render")
        page_two = django_draws(query).replace("&amp;", "&")
        assert page_two in [href.replace("&amp;", "&") for href in _hrefs(render)], render

        # An event's render draws the same: only n moved
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {"id": "qs", "command": "bump", "implicit_args": {}, "explicit_args": {}},
            }
        )
        render = await receive("render")
        assert "s" not in render["payload"]["diff"], "the statics stay: the links did not move"

        # A push_to: the page puts it in the address bar and tells it back
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {"id": "qs", "command": "go", "implicit_args": {}, "explicit_args": {"to": "?q=b"}},
            }
        )
        change = await receive("url_change")
        assert change["payload"]["url"] == "?q=b"
        await communicator.send_json_to(
            {"command": "params_changed", "payload": {"uri": "http://testserver/qs/?q=b", "params": {"q": "b"}}}
        )
        render = await receive("render")
        assert "?q=b&amp;page=2" in json.dumps(render["payload"]["diff"]), render
    finally:
        await communicator.disconnect()


# -- a mounted component --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_mounted_component_draws_its_params_and_a_followed_push():
    view = await wireview_testing.mount(QsLinks, id="qs", params={"q": "a"})
    assert links("q=a") in view.render()
    await view.call("go", to='?q=b&tag=x&tag=y&f.json={"a":1}')
    await view.follow_push()
    assert links('q=b&tag=x&tag=y&f.json={"a":1}') in view.render(), "as the address bar has it"
