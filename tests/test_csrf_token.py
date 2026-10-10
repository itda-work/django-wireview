"""``{% csrf_token %}`` in a component's template (#206, wireview/core/csrf.py).

A component's template renders with its own context, not the page's
``RequestContext``: Django's ``csrf`` context processor never reached it, and a
plain POST form in a component posted an empty token. These hold now:

- the HTTP render gives the page's token, the context processor's lazy value:
  a page whose templates never read it sets no CSRF cookie
- a live render gives a token of the secret the connection's handshake carried
  -- the cookie, or the session under ``CSRF_USE_SESSIONS`` -- one per
  connection, the same in every render, so the statics do not change
- a token is valid for its connection's secret and for no other
- a connection without a secret Django would accept gets none, and says so once
- a render shared between connections gets none; verified, reading it raises
- LiveComponents, function components and stream items get it as well

The browser side, a form submitted to a view that checks the token, is
tests/test_csrf_token_e2e.py.
"""

import logging
import re
import typing as t
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.middleware.csrf import CSRF_SESSION_KEY, CsrfViewMiddleware
from django.test import Client, RequestFactory, override_settings
from testproj.outbound import RecordingOutbound
from testproj.wireview_setting import set_wireview

from wireview import Component, LiveComponent, function_component
from wireview import testing as wireview_testing
from wireview.consumer import WireviewConsumer
from wireview.core import csrf, shared_render
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SECRET = "A" * 16 + "b" * 16
OTHER = "Z" * 32
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([A-Za-z0-9]*)"')

FORM = '<form method="post">{% csrf_token %}<b>{{ n }}</b></form>'
TEMPLATES = {
    "csrf/form.html": "{% load wireview %}<div {% tag_header %}>" + FORM + "</div>",
    "csrf/value.html": "{% load wireview %}<div {% tag_header %}>{{ csrf_token }}</div>",
    "csrf/parent.html": (
        '{% load wireview %}<div {% tag_header %}>{% live_component "CsrfLiveChild" id="kid" %}'
        '{% func "csrf_fn" %}</div>'
    ),
    "csrf/child.html": "{% load wireview %}<i {% live_tag_header %}>" + FORM + "</i>",
    "csrf/fn.html": FORM,
    "csrf/shared.html": '{% load wireview %}<div {% tag_header %}>{{ n }}{% include "csrf/partial.html" %}</div>',
    "csrf/partial.html": FORM,
    "csrf/items.html": '{% load wireview %}<ul {% tag_header %} wire-stream="rows"></ul>',
    "csrf/items_item.html": "<li>" + FORM + "</li>",
    "csrf/page.html": '{% load wireview %}{% component "CsrfForm" id="form" %}',
}


class CsrfForm(Component):
    class Meta:
        template_name = "csrf/form.html"

    n: int = 0

    async def bump(self):
        self.n += 1


class CsrfValue(Component):
    class Meta:
        template_name = "csrf/value.html"


class CsrfOwnName(Component):
    """Has a field named like the token: the field wins, as a context processor's name does."""

    class Meta:
        template_name = "csrf/value.html"

    csrf_token: str = "mine"


class CsrfLiveChild(LiveComponent):
    class Meta:
        template_name = "csrf/child.html"

    n: int = 0


@function_component(template="csrf/fn.html")
def csrf_fn():
    return {"n": 0}


class CsrfParent(Component):
    class Meta:
        template_name = "csrf/parent.html"


class CsrfShared(Component):
    """In scope for the system check -- the token is read in an include it does not look into."""

    class Meta:
        template_name = "csrf/shared.html"
        shared_render = True
        subscriptions = {"csrf-shared"}

    n: int = 0

    async def notification(self, channel: str, **kwargs):
        pass


class CsrfItems(Component):
    class Meta:
        template_name = "csrf/items.html"


@pytest.fixture(autouse=True)
def _templates(request):
    if request.node.name.startswith("test_http_"):  # the test project's own pages and templates
        yield
        return
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)],
                    "context_processors": ["django.template.context_processors.request"],
                },
            }
        ]
    ):
        shared_render._SCOPE.clear()
        yield
    shared_render._SCOPE.clear()


def accepts(token: str, *, cookie: str | None = None, session: dict[str, t.Any] | None = None) -> bool:
    """Whether Django's CSRF check lets a POST with ``token`` through for this cookie or session."""
    request = RequestFactory().post("/", {"csrfmiddlewaretoken": token})
    request.COOKIES = {settings.CSRF_COOKIE_NAME: cookie} if cookie is not None else {}
    request.session = session if session is not None else {}  # type: ignore[assignment]
    middleware = CsrfViewMiddleware(lambda request: HttpResponse())
    middleware.process_request(request)
    return middleware.process_view(request, lambda request: HttpResponse(), (), {}) is None


def tokens(html: str) -> list[str]:
    return TOKEN.findall(html)


class Page:
    """One tab: a session started with these cookies and this session, a component joined."""

    def __init__(self, session: WireviewSession, outbound: RecordingOutbound) -> None:
        self.session = session
        self.outbound = outbound

    @classmethod
    async def open(
        cls,
        component: type[Component] = CsrfForm,
        *,
        cookie: str | None = SECRET,
        session: dict[str, t.Any] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> "Page":
        outbound = RecordingOutbound()
        live = WireviewSession(outbound, user=AnonymousUser(), channel_name=f"csrf-{uuid4().hex[:8]}")
        if cookies is None:
            cookies = {settings.CSRF_COOKIE_NAME: cookie} if cookie is not None else {}
        await live.start(session=SessionView(session or {}), vsn=PROTOCOL_VERSION, csrf_cookies=cookies)
        state = sign_state(component(id="form", user=AnonymousUser(), wire=WireviewMeta(params={})))
        await live.handle_message({"command": "join", "payload": {"name": component._name, "state": state}})
        assert outbound.renders(), f"{component._name} did not join"
        return cls(live, outbound)

    def html(self, id: str = "form") -> str:
        last = self.session.repo.get(id).wire._last_rendered
        assert last is not None
        return last.to_html()


# -- the secret -----------------------------------------------------------------------------


@pytest.mark.unit
def test_the_secret_is_read_as_django_reads_it():
    name = settings.CSRF_COOKIE_NAME
    assert csrf.secret({name: SECRET}, {}) == SECRET
    assert csrf.secret({}, {}) is None
    assert csrf.secret({name: "short"}, {}) is None, "Django would replace it, and the socket cannot"
    assert csrf.secret({name: "!" * 32}, {}) is None
    # Django before 4.0 kept the masked token in the cookie
    legacy = csrf.mask(SECRET)
    assert legacy is not None and len(legacy) == 64
    assert csrf.secret({name: legacy}, {}) == SECRET


@pytest.mark.unit
def test_under_csrf_use_sessions_the_secret_is_the_sessions(settings):
    settings.CSRF_USE_SESSIONS = True
    assert csrf.secret({settings.CSRF_COOKIE_NAME: OTHER}, {CSRF_SESSION_KEY: SECRET}) == SECRET
    assert csrf.secret({settings.CSRF_COOKIE_NAME: OTHER}, {}) is None


@pytest.mark.unit
def test_a_masked_token_unmasks_to_its_secret_only():
    token = csrf.mask(SECRET)
    assert token is not None and token != csrf.mask(SECRET), "masked afresh, as Django masks one"
    assert accepts(token, cookie=SECRET)
    assert not accepts(token, cookie=OTHER)


@pytest.mark.unit
def test_the_cookies_come_from_the_scope_or_its_header():
    assert csrf.cookies_of({"cookies": {"csrftoken": SECRET}}) == {"csrftoken": SECRET}
    header = [(b"host", b"x"), (b"cookie", f"sessionid=s; csrftoken={SECRET}".encode())]
    assert csrf.cookies_of({"headers": header}) == {"sessionid": "s", "csrftoken": SECRET}
    assert csrf.cookies_of({"headers": []}) == {}


# -- the HTTP render ------------------------------------------------------------------------


def test_http_the_render_gives_the_pages_token():
    response = Client(enforce_csrf_checks=True).get("/csrfprobe/")
    found = tokens(response.content.decode())
    assert len(found) == 4, "the page's form, the component's, its LiveComponent's and the function's"
    assert len(set(found)) == 1, "one lazy token for the page, as its own templates get"
    cookie = response.cookies[settings.CSRF_COOKIE_NAME].value
    assert accepts(found[0], cookie=cookie)


def test_http_a_page_that_draws_no_token_sets_no_cookie():
    """The token stays the context processor's lazy value: Set-Cookie only for a page that reads it."""
    response = Client().get("/formprobe/")
    assert response.status_code == 200
    assert settings.CSRF_COOKIE_NAME not in response.cookies


# -- live renders ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_join_renders_a_token_of_the_connections_secret():
    page = await Page.open()
    (token,) = tokens(page.html())
    assert accepts(token, cookie=SECRET)
    assert not accepts(token, cookie=OTHER)


@pytest.mark.asyncio
async def test_one_token_a_connection_so_the_statics_stay():
    page = await Page.open()
    first = page.html()
    page.outbound.commands.clear()
    await page.session.handle_message(
        {
            "command": "user_event",
            "payload": {"id": "form", "command": "bump", "implicit_args": {}, "explicit_args": {}},
        }
    )
    (render,) = page.outbound.renders()
    assert "s" not in render["diff"], "only the field moved: no statics sent again"
    assert tokens(page.html()) == tokens(first)


@pytest.mark.asyncio
async def test_two_connections_get_their_own():
    mine, theirs = await Page.open(cookie=SECRET), await Page.open(cookie=OTHER)
    (my_token,), (their_token,) = tokens(mine.html()), tokens(theirs.html())
    assert accepts(my_token, cookie=SECRET) and not accepts(my_token, cookie=OTHER)
    assert accepts(their_token, cookie=OTHER) and not accepts(their_token, cookie=SECRET)


@pytest.mark.asyncio
async def test_under_csrf_use_sessions_the_token_follows_the_session(settings):
    settings.CSRF_USE_SESSIONS = True
    page = await Page.open(cookie=OTHER, session={CSRF_SESSION_KEY: SECRET})
    (token,) = tokens(page.html())
    assert accepts(token, session={CSRF_SESSION_KEY: SECRET})
    # A boundary reads the session again (_reload_session): the next render follows it
    page.session.repo.session = SessionView({CSRF_SESSION_KEY: OTHER})
    page.session.repo.get("form").n = 5
    await page.session.send_render(page.session.repo.get("form"))
    (rotated,) = tokens(page.html())
    assert accepts(rotated, session={CSRF_SESSION_KEY: OTHER})


@pytest.mark.asyncio
async def test_a_connection_without_a_secret_renders_none_and_says_so_once(caplog, monkeypatch):
    monkeypatch.setattr(csrf, "_told", False)
    with caplog.at_level(logging.WARNING, logger="wireview"):
        page = await Page.open(cookie=None)
        await Page.open(cookie="not a secret")
    assert tokens(page.html()) == []
    told = [r for r in caplog.records if "carries no CSRF cookie" in r.getMessage()]
    assert len(told) == 1 and "ensure_csrf_cookie" in told[0].getMessage()


@pytest.mark.asyncio
async def test_a_session_started_without_cookies_renders_none():
    """A session driven without a handshake (a test, another transport) has nothing to read."""
    outbound = RecordingOutbound()
    live = WireviewSession(outbound, user=AnonymousUser(), channel_name="csrf-bare")
    await live.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    assert live.repo.csrf_token is None


@pytest.mark.asyncio
async def test_the_token_reads_as_a_variable_too_and_a_field_of_that_name_wins():
    value = await Page.open(CsrfValue)
    assert accepts(re.sub(r"<[^>]*>", "", value.html()).strip(), cookie=SECRET)
    own = await Page.open(CsrfOwnName)
    assert ">mine<" in own.html()


@pytest.mark.asyncio
async def test_a_live_component_and_a_function_component_get_it():
    page = await Page.open(CsrfParent)
    found = tokens(page.html()) + tokens(page.html("kid"))
    assert len(found) == 2, page.html()
    assert all(accepts(token, cookie=SECRET) for token in found)


@pytest.mark.asyncio
async def test_a_stream_item_gets_it():
    """Rendered apart from the component's context, for the stream operation the connection sends."""
    page = await Page.open(CsrfItems)
    items = page.session.repo.get("form")
    (token,) = tokens(await items._render_stream_item("csrf/items_item.html", {"id": 1}))
    assert accepts(token, cookie=SECRET)


@pytest.mark.asyncio
async def test_the_websocket_reads_the_handshakes_cookie():
    communicator = WebsocketCommunicator(
        WireviewConsumer.as_asgi(),
        f"/__wireview__?vsn={PROTOCOL_VERSION}",
        headers=[(b"cookie", f"{settings.CSRF_COOKIE_NAME}={SECRET}".encode())],
    )
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        state = await sync_to_async(sign_state)(CsrfForm(id="form", user=AnonymousUser(), wire=WireviewMeta(params={})))
        await communicator.send_json_to({"command": "join", "payload": {"name": "CsrfForm", "state": state}})
        message = await communicator.receive_json_from(timeout=5)
        assert message["command"] == "render"
        (token,) = tokens(str(message["payload"]["diff"]).replace("\\'", "'").replace("'", '"'))
        assert accepts(token, cookie=SECRET)
    finally:
        await communicator.disconnect()


# -- renders many viewers share -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_shared_render_gives_no_viewer_a_token(monkeypatch):
    """Unverified, the render one connection draws for all has no token: never another viewer's."""
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    pages = [await Page.open(CsrfShared, cookie=SECRET), await Page.open(CsrfShared, cookie=OTHER)]
    data = {"channel": "csrf-shared", "kwargs": {}, "message_id": uuid4().hex}
    for page in pages:
        page.session.repo.get("form").n = 1
    for page in pages:
        await page.session.notification(dict(data))
    for page in pages:
        assert ">1<" in page.html()
        assert "csrfmiddlewaretoken" not in page.html()


@pytest.mark.asyncio
async def test_a_verified_shared_render_that_reads_the_token_raises(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=True)
    mounted = await wireview_testing.mount(CsrfShared, id="form")
    with pytest.raises(shared_render.SharedRenderError, match="its render read 'csrf_token'"):
        await mounted.render_diff()
