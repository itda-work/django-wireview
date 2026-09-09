"""컴포넌트가 Django 세션을 읽는다 (#68, GAP-029).

세 가지가 이 테스트의 관심사다.

- **읽기.** ``self.session``이 데이터와 세션 키를 준다. 예제 둘이 상상만 하고 쓰다가
  ``AttributeError``로 죽던 API가 이것이다.
- **읽기 전용.** WebSocket에는 ``Set-Cookie``를 실을 응답이 없어 쓰기는 조용히 사라진다.
  그래서 쓰기는 예외다.
- **클라이언트로 새지 않는다.** 서명 상태는 브라우저를 왕복한다. 세션 데이터가 거기 실리면
  안 된다.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.session import SessionView, load_session
from wireview.core.state import sign_state, unsign_state
from wireview.repository import ComponentRepository
from wireview.testing import mount

pytestmark = pytest.mark.unit

TEMPLATES = {
    "sess/probe.html": "{% load wireview %}<div {% tag_header %}>{{ this.session.cart }}</div>",
    "sess/parent.html": "{% load wireview %}<main {% tag_header %}>{% live_component 'SessChild' id='kid' %}</main>",
    "sess/child.html": "{% load wireview %}<p {% live_tag_header %}>{{ this.seen }}</p>",
}


class SessProbe(Component):
    _template_name = "sess/probe.html"

    seen: str = ""

    async def joined(self) -> None:
        self.seen = self.session.get("cart", "")

    async def read(self) -> None:
        self.seen = f"{self.session.session_key}:{self.session.get('cart')}"


class SessChild(LiveComponent):
    _template_name = "sess/child.html"

    seen: str = ""

    async def joined(self) -> None:
        self.seen = self.session.get("cart", "")


class SessParent(Component):
    _template_name = "sess/parent.html"


@pytest.fixture(autouse=True)
def templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


def store_with(**data: t.Any):
    """A real ``SessionStore``, saved so that it has a key to read back."""
    from django.contrib.sessions.backends.db import SessionStore

    store = SessionStore()
    for key, value in data.items():
        store[key] = value
    store.save()
    return SessionStore(session_key=store.session_key)


# --- the view itself ----------------------------------------------------------------------


class TestSessionView:
    def test_wrap_accepts_what_the_call_sites_hold(self):
        assert dict(SessionView.wrap(None)) == {}
        assert SessionView.wrap(None).session_key is None
        assert dict(SessionView.wrap({"a": 1})) == {"a": 1}
        assert SessionView.wrap({}, session_key="s1").session_key == "s1"

        view = SessionView({"a": 1})
        assert SessionView.wrap(view) is view

    def test_it_compares_equal_to_a_plain_dict(self):
        assert SessionView({"a": 1}) == {"a": 1}
        assert SessionView({"a": 1}) == SessionView({"a": 1})
        assert SessionView({"a": 1}) != {"a": 2}

    def test_the_repr_shows_keys_but_never_values(self):
        text = repr(SessionView({"cart": "S3CRET"}, session_key="s1"))

        assert "S3CRET" not in text
        assert "cart" in text and "s1" in text

    @pytest.mark.parametrize(
        "write",
        [
            lambda view: view.__setitem__("a", 1),
            lambda view: view.__delitem__("a"),
            lambda view: view.update({"a": 1}),
            lambda view: view.setdefault("a", 1),
            lambda view: view.pop("a"),
            lambda view: view.clear(),
            lambda view: view.flush(),
            lambda view: view.save(),
        ],
    )
    def test_every_write_raises(self, write):
        view = SessionView({"a": 0})

        with pytest.raises(TypeError, match="read-only"):
            write(view)

        assert view == {"a": 0}

    @pytest.mark.django_db
    def test_a_store_is_read_only_when_something_asks(self):
        view = SessionView.wrap(store_with(cart="abc"))

        assert not view.loaded
        assert view.session_key is not None  # the key is an attribute, not a query
        assert not view.loaded

        assert view["cart"] == "abc"
        assert view.loaded


# --- mount() ------------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMount:
    async def test_it_injects_session_data_and_the_key(self):
        view = await mount(SessProbe, session={"cart": "abc"}, session_key="s1")

        assert view.component.session["cart"] == "abc"
        assert view.component.session.session_key == "s1"
        assert view.component.seen == "abc"

    async def test_the_key_can_be_injected_on_its_own(self):
        view = await mount(SessProbe, session_key="s1")

        assert view.component.session.session_key == "s1"
        assert dict(view.component.session) == {}

    async def test_without_a_session_the_view_is_empty(self):
        view = await mount(SessProbe)

        assert view.component.session.session_key is None
        assert view.component.session.get("cart", "none") == "none"
        assert len(view.component.session) == 0

    async def test_the_on_mount_hooks_see_the_same_view(self):
        seen: list[t.Any] = []

        class Recorder:
            @staticmethod
            async def on_mount(component, params, session):
                seen.append(session)
                return {"cont": True}

        class Hooked(SessProbe):
            _on_mount = [Recorder]

        view = await mount(Hooked, session={"cart": "abc"}, session_key="s1")

        assert seen == [view.component.session]
        assert seen[0].session_key == "s1"


# --- the session does not reach the client -------------------------------------------------


@pytest.mark.asyncio
async def test_the_session_is_not_in_the_signed_state():
    view = await mount(SessProbe, session={"cart": "S3CRET"}, session_key="s1")

    state = sign_state(view.component)

    assert "S3CRET" not in state
    assert "session" not in unsign_state(state, "SessProbe")


def test_dropping_session_from_exclude_fields_fails_loudly():
    """A subclass that overrides ``_exclude_fields`` cannot leak the session in silence."""
    from pydantic_core import PydanticSerializationError

    class Leaky(SessProbe):
        _exclude_fields = {"user", "wire"}

    component = Leaky(
        user=AnonymousUser(),
        wire=WireviewMeta(params={}),
        session=SessionView({"cart": "S3CRET"}),
    )

    with pytest.raises(PydanticSerializationError):
        sign_state(component)


@pytest.mark.asyncio
async def test_the_session_is_not_in_the_rendered_state():
    view = await mount(SessProbe, session={"cart": "S3CRET"}, session_key="s1")

    html = view.render() or ""

    # The value the template prints is fine; the serialized state is what must not carry it.
    assert "data-state" in html
    assert "S3CRET" not in html.split("data-state=")[1].split(">")[0]


# --- the connection ------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_connect_snapshots_the_session_so_reads_cost_nothing():
    """The socket reads the session once, off the event loop.

    Left lazy, the first ``self.session[...]`` inside an async handler would run a
    query on the event loop and raise ``SynchronousOnlyOperation``.
    """
    from asgiref.sync import sync_to_async
    from django.db import connection

    store = await sync_to_async(store_with)(cart="abc")

    consumer = WireviewConsumer()
    consumer.scope = {"user": AnonymousUser(), "session": store}
    consumer.channel_name = "test-channel"
    consumer.channel_layer = None  # type: ignore[assignment]
    consumer.base_send = _swallow  # type: ignore[assignment]

    await consumer.connect()

    assert consumer.repo.session == {"cart": "abc"}
    assert consumer.repo.session.loaded

    component = consumer.repo.build("SessProbe", {"id": "probe"})
    with CaptureQueriesContext(connection) as queries:
        await component.read()
    assert component.seen == f"{store.session_key}:abc"
    assert len(queries) == 0


async def _swallow(message: dict[str, t.Any]) -> None:
    return None


@pytest.mark.asyncio
async def test_load_session_passes_through_what_it_is_given():
    assert dict(await load_session(None)) == {}
    assert dict(await load_session({"cart": "abc"})) == {"cart": "abc"}


@pytest.mark.asyncio
async def test_a_live_component_child_gets_the_connection_session():
    repo = ComponentRepository(is_live=True, session={"cart": "abc"})
    parent = repo.build("SessParent", {"id": "parent"})
    child = repo.build_live_component("SessChild", {"id": "kid"}, parent_id=parent.id)

    assert child.session["cart"] == "abc"


def test_the_dead_render_reads_the_request_session():
    class FakeRequest:
        META = {"QUERY_STRING": ""}
        session = {"cart": "abc"}

    html = Template("{% load wireview %}{% component 'SessProbe' id='p' %}").render(Context({"request": FakeRequest()}))

    assert "abc" in html


def test_a_page_without_a_session_still_renders():
    html = Template("{% load wireview %}{% component 'SessProbe' id='p' %}").render(Context({}))

    assert "data-state" in html
