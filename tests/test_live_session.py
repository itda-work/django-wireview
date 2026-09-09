"""Page boundaries: ``live_session`` (GAP-009, #58).

The design and its two adversarial review rounds are in
``docs/design/live-session.md``; §6-1 there lists, per acceptance condition, the
thing an implementation can skip while still passing a loosely written test.
This module is organised by those conditions, and each class says which one.

The short version of what is being defended: a page declares one boundary, the
predicate that guards it runs before the first byte *and* before the join, and
every signed state carries the boundary and the login it was issued under, so
two individually valid pieces cannot be combined into a third thing.
"""

import typing as t
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.cache import SessionStore as CacheSessionStore
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from wireview import Component, LiveComponent, live_session
from wireview.consumer import WireviewConsumer
from wireview.core import live_session as live_session_module
from wireview.core.live_session import (
    AUTH_GENERATION_KEY,
    LiveSession,
    auth_fingerprint,
    auth_topic,
    get_live_session,
)
from wireview.core.meta import WireviewMeta
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

#: Session backend for the tests that need a real store to flush mid-test. The db
#: backend would need a write from the bridge thread while the test transaction is
#: open, which sqlite answers with "database is locked".
CACHE_SESSIONS = "django.contrib.sessions.backends.cache"


def _make_session() -> str:
    """A saved session standing for one login. Returns its key."""
    store = CacheSessionStore()
    store["_auth_user_hash"] = "abc"
    store[AUTH_GENERATION_KEY] = "gen-1"
    store.save()
    return str(store.session_key)


TEMPLATES = {
    "lsx/guarded.html": "{% load wireview %}<p {% tag_header %}>secret {{ this.note }}</p>",
    "lsx/free.html": "{% load wireview %}<p {% tag_header %}>free {{ this.note }}</p>",
    "lsx/parent.html": ("{% load wireview %}<main {% tag_header %}>{% live_component 'LsxChild' id='kid' %}</main>"),
    "lsx/child.html": "{% load wireview %}<span {% tag_header %}>child {{ this.note }}</span>",
    "lsx/nest.html": ("{% load wireview %}<main {% tag_header %}>{% component 'LsxHooked' id='nested' %}</main>"),
    "lsx/boomparent.html": (
        "{% load wireview %}<main {% tag_header %}>{% live_component 'LsxBoomChild' id='boomkid' %}</main>"
    ),
}

#: Every hook call, in order: (component id, label).
CALLS: list[tuple[str, str]] = []


def marks(label: str):
    class Hook:
        @staticmethod
        async def on_mount(component, params, session):
            CALLS.append((component.id, label))
            return {"cont": True}

    Hook.__name__ = f"Mark{label}"
    return Hook


class HaltHook:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append((component.id, "halt"))
        return {"halt": True}


class LsxGuarded(Component):
    _template_name = "lsx/guarded.html"
    _live_sessions: t.ClassVar[set[str]] = {"lsx-admin"}
    note: str = "guarded"

    async def bump(self) -> None:
        self.note = "bumped"


class LsxFree(Component):
    """Declares nothing, so it mounts wherever it is put -- the pre-#58 default."""

    _template_name = "lsx/free.html"
    note: str = "free"


class LsxHooked(Component):
    _template_name = "lsx/free.html"
    _on_mount: t.ClassVar[list[t.Any]] = [marks("component")]
    note: str = "hooked"


class LsxChild(LiveComponent):
    _template_name = "lsx/child.html"
    _live_sessions: t.ClassVar[set[str]] = {"lsx-admin"}
    note: str = "kid"


class LsxParent(Component):
    _template_name = "lsx/parent.html"


class LsxNestParent(Component):
    """A live parent whose template names an ordinary nested ``{% component %}``."""

    _template_name = "lsx/nest.html"


class BoomHook:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append((component.id, "boom"))
        raise RuntimeError("the authorization query failed")


class LsxBoomChild(LiveComponent):
    _template_name = "lsx/child.html"
    _on_mount: t.ClassVar[list[t.Any]] = [BoomHook]
    note: str = "kid"


class LsxBoomParent(Component):
    _template_name = "lsx/boomparent.html"


class LsxBoom(Component):
    _template_name = "lsx/guarded.html"
    _on_mount: t.ClassVar[list[t.Any]] = [BoomHook]
    note: str = "boom"

    async def bump(self) -> None:
        self.note = "bumped"


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)

    def kinds(self) -> list[str]:
        return [command for command, _ in self.commands]

    def renders(self) -> list[dict[str, t.Any]]:
        return [payload for command, payload in self.commands if command == "render"]


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [
                        ("django.template.loaders.locmem.Loader", TEMPLATES),
                        # wireview_header.html ships with the app and these tests render it.
                        "django.template.loaders.app_directories.Loader",
                    ]
                },
            }
        ]
    ):
        yield


@pytest.fixture(autouse=True)
def _registry():
    """Give each test the registry it declares and nothing else."""
    CALLS.clear()
    saved = dict(live_session_module._REGISTRY)
    yield
    live_session_module._REGISTRY.clear()
    live_session_module._REGISTRY.update(saved)


@pytest.fixture
def admin_session() -> LiveSession:
    return live_session("lsx-admin", authorize=lambda ctx: ctx.user.is_staff, on_mount=[marks("session")])


def make_consumer(
    *,
    user=None,
    session: t.Any = None,
    live_session_obj: LiveSession | None = None,
) -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(
        is_live=True,
        user=user or AnonymousUser(),
        session=session,
        live_session=live_session_obj,
    )
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.auth_fingerprint = auth_fingerprint(consumer.repo.user, consumer.repo.session)
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def signed(component_class: type[Component], *, page: LiveSession | None = None, user=None, session=None, **state):
    """A ``data-state`` as the page that owned ``component_class`` would have issued it."""
    component = component_class(
        user=user or AnonymousUser(),
        session=SessionView.wrap(session),
        wire=WireviewMeta(params={}, live_session=page),
        **state,
    )
    return sign_state(component)


@pytest.fixture
def staff():
    """Created in a fixture, not in the test body.

    ``User.objects.create_user`` fires ``post_save``, wireview's auto-broadcast
    receiver publishes on it, and publishing from inside a running event loop is
    exactly what ``async_to_sync`` refuses. Fixtures run before the loop does.
    """
    return get_user_model().objects.create_user(f"lsx-staff-{uuid4().hex[:8]}", password="x", is_staff=True)


@pytest.fixture
def other_staff():
    return get_user_model().objects.create_user(f"lsx-other-{uuid4().hex[:8]}", password="x", is_staff=True)


@pytest.fixture
def plain():
    return get_user_model().objects.create_user(f"lsx-plain-{uuid4().hex[:8]}", password="x")


# --- declaring a boundary -----------------------------------------------------------------


class TestDeclaration:
    def test_a_session_registers_under_its_name(self, admin_session):
        assert get_live_session("lsx-admin") is admin_session

    def test_an_unknown_name_resolves_to_nothing(self):
        assert get_live_session("lsx-nope") is None
        assert get_live_session("") is None

    def test_a_name_cannot_be_declared_twice(self, admin_session):
        with pytest.raises(ImproperlyConfigured, match="already declared"):
            live_session("lsx-admin")

    def test_a_session_needs_a_name(self):
        with pytest.raises(ImproperlyConfigured, match="needs a name"):
            live_session("")


# --- AC2: the HTTP render is inside the boundary ------------------------------------------


class TestHttpBoundary:
    """AC2. The first HTML and the first ``data-state`` cannot be recalled by a join."""

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self, admin_session):
        request = RequestFactory().get("/panel/?next=1")
        request.user = AnonymousUser()
        request.session = {}

        @admin_session.view
        def page(req):
            raise AssertionError("the view must not run")

        response = page(request)

        assert response.status_code == 302
        assert "/panel/%3Fnext%3D1" in response["Location"] or "/panel/" in response["Location"]

    def test_a_logged_in_but_unauthorized_user_gets_a_403(self, admin_session, plain):
        request = RequestFactory().get("/panel/")
        request.user = plain
        request.session = {}

        @admin_session.view
        def page(req):
            raise AssertionError("the view must not run")

        with pytest.raises(PermissionDenied):
            page(request)

    def test_an_authorized_user_reaches_the_view_and_the_page_is_stamped(self, admin_session, staff):
        request = RequestFactory().get("/panel/")
        request.user = staff
        request.session = {}
        seen = {}

        @admin_session.view
        def page(req):
            seen["name"] = getattr(req, "wireview_live_session", None)
            return "rendered"

        assert page(request) == "rendered"
        assert seen["name"] == "lsx-admin"

    def test_an_async_view_stays_async(self, admin_session, staff):
        """Django decides how to call a view by inspecting the callable it was handed.

        A sync wrapper around an ``async def`` view hands back an un-awaited
        coroutine where a response belongs, and nothing says so until the
        request fails.
        """
        import asyncio

        @admin_session.view
        async def page(req):
            return "rendered"

        assert asyncio.iscoroutinefunction(page)

        request = RequestFactory().get("/panel/")
        request.user = staff
        request.session = {}
        assert asyncio.run(page(request)) == "rendered"

    def test_an_async_view_is_refused_the_same_way(self, admin_session, plain):
        import asyncio

        @admin_session.view
        async def page(req):
            raise AssertionError("the view must not run")

        request = RequestFactory().get("/panel/")
        request.user = plain
        request.session = {}

        with pytest.raises(PermissionDenied):
            asyncio.run(page(request))

    def test_an_async_class_based_view_is_refused_with_a_response(self, admin_session, plain):
        """The allowed path hides this one.

        An async CBV's ``dispatch`` is an ordinary function returning a
        coroutine, so a sync wrapper passes an allowed request straight through
        and looks correct. The refusal is where it breaks: a plain response
        arrives where Django is about to ``await``.
        """
        import asyncio

        from django.http import HttpResponse
        from django.views import View

        @admin_session.view
        class Page(View):
            async def get(self, request):
                return HttpResponse("ok")

        request = RequestFactory().get("/panel/")
        request.user = AnonymousUser()
        request.session = {}

        response = asyncio.run(Page.as_view()(request))

        assert response.status_code == 302

    def test_an_async_class_based_view_still_serves_an_allowed_user(self, admin_session, staff):
        import asyncio

        from django.http import HttpResponse
        from django.views import View

        @admin_session.view
        class Page(View):
            async def get(self, request):
                return HttpResponse("ok")

        request = RequestFactory().get("/panel/")
        request.user = staff
        request.session = {}

        assert asyncio.run(Page.as_view()(request)).status_code == 200

    def test_a_class_based_view_takes_the_decorator_too(self, admin_session, plain):
        from django.views import View

        request = RequestFactory().get("/panel/")
        request.user = plain
        request.session = {}

        @admin_session.view
        class Page(View):
            def get(self, req):
                raise AssertionError("the view must not run")

        with pytest.raises(PermissionDenied):
            Page.as_view()(request)

    def test_a_guarded_component_renders_nothing_outside_its_session(self):
        """The safe default: a page that declares no boundary is not the admin page."""
        html = Template("{% load wireview %}{% component 'LsxGuarded' id='g1' %}").render(Context({}))

        assert html == ""
        assert "data-state" not in html

    def test_a_guarded_component_renders_inside_its_session(self, admin_session):
        class Request:
            META = {"QUERY_STRING": ""}
            session: dict = {}
            wireview_live_session = "lsx-admin"

        html = Template("{% load wireview %}{% component 'LsxGuarded' id='g2' %}").render(
            Context({"request": Request()})
        )

        assert "secret guarded" in html
        assert [label for _id, label in CALLS] == ["session"]

    def test_the_header_publishes_the_page_boundary(self, admin_session):
        class Request:
            META = {"QUERY_STRING": ""}
            session: dict = {}
            wireview_live_session = "lsx-admin"

        html = Template("{% load wireview %}{% wireview_header %}").render(Context({"request": Request()}))

        assert '<meta name="wireview-live-session" content="lsx-admin" />' in html

    def test_a_page_outside_every_boundary_publishes_an_empty_name(self):
        html = Template("{% load wireview %}{% wireview_header %}").render(Context({}))

        assert '<meta name="wireview-live-session" content="" />' in html


# --- AC1: every path that produces a component --------------------------------------------


@pytest.mark.asyncio
class TestEveryMountPath:
    """AC1. Creation is not the only path: restore, nesting and re-join are too."""

    async def test_the_session_hooks_run_before_the_components_own(self, admin_session):
        consumer, _ = make_consumer(live_session_obj=admin_session)

        await consumer.repo.join("LsxHooked", {"id": "m1"})

        assert CALLS == [("m1", "session"), ("m1", "component")]

    async def test_a_live_component_child_runs_them_too(self, admin_session):
        consumer, _ = make_consumer(live_session_obj=admin_session)
        parent = await consumer.repo.join("LsxParent", {"id": "m2"})

        await consumer.send_render(parent)

        assert ("kid", "session") in CALLS

    async def test_a_child_is_refused_where_its_session_is_not(self):
        """The child is registered and rendered *during* the parent's pass (§3-5)."""
        consumer, outbound = make_consumer()
        parent = await consumer.repo.join("LsxParent", {"id": "m3"})

        await consumer.send_render(parent)

        assert consumer.repo.get("kid") is None
        children = [payload.get("children") or {} for payload in outbound.renders()]
        assert not any("kid" in c for c in children), "a refused child ships no diff"

    async def test_a_restored_child_state_is_refused_from_another_boundary(self, admin_session):
        consumer, _ = make_consumer(live_session_obj=admin_session)
        consumer.live_session_name = "lsx-admin"
        payload = unsign_envelope(signed(LsxChild, id="kid", note="from-elsewhere"), "LsxChild")

        assert consumer._child_boundary_refusal(payload) is not None

    async def test_a_rejoin_runs_the_boundary_again(self, admin_session):
        consumer, _ = make_consumer(live_session_obj=admin_session)
        await consumer.repo.join("LsxHooked", {"id": "m4"})
        consumer.repo.remove("m4")

        await consumer.repo.join("LsxHooked", {"id": "m4"})

        assert [label for _id, label in CALLS] == ["session", "component", "session", "component"]

    async def test_a_nested_ordinary_component_runs_the_session_hooks(self, admin_session):
        """A nested ``{% component %}`` renders inline, during its parent's pass.

        It used to skip the mount entirely on the live path, on the theory that
        the join had covered the page. The join had covered the *page*: a page
        hook meaning to refuse this one component never ran, and the component
        became an event target as well as markup.
        """
        consumer, _ = make_consumer(live_session_obj=admin_session)
        parent = await consumer.repo.join("LsxNestParent", {"id": "m6"})

        await consumer.send_render(parent)

        assert ("nested", "session") in CALLS

    async def test_a_refused_nested_ordinary_component_ships_nothing(self):
        """``_live_sessions`` is answered without a bridge, so it works here too."""
        consumer, outbound = make_consumer()
        Template("{% load wireview %}{% component 'LsxGuarded' id='m7' %}").render(
            Context({"wireview_repository": consumer.repo})
        )

        assert consumer.repo.get("m7") is None
        assert outbound.renders() == []

    async def test_a_guarded_component_is_refused_on_a_page_with_no_boundary(self):
        consumer, outbound = make_consumer()

        component = await consumer.repo.join("LsxGuarded", {"id": "m5"})

        assert component.wire.mount_halted
        await consumer.send_render(component)
        assert outbound.renders() == [], "nothing of a refused component goes out"


# --- AC3: a refused component is not an event target --------------------------------------


@pytest.mark.asyncio
class TestHaltLeavesNothingBehind:
    """AC3. ``component_remove()`` only speaks to the client; the repository has to forget."""

    async def test_the_id_stops_answering_events(self):
        consumer, _ = make_consumer()

        await consumer.repo.join("LsxGuarded", {"id": "h1"})

        assert consumer.repo.get("h1") is None
        assert await consumer.repo.dispatch_event("h1", "bump", (), {}) is None

    async def test_the_join_sends_remove_and_no_render(self):
        consumer, outbound = make_consumer()

        await consumer.command_join("LsxGuarded", signed(LsxGuarded, id="h2"))

        assert outbound.kinds() == ["remove"]

    async def test_params_changed_does_not_reach_it(self):
        consumer, outbound = make_consumer()
        await consumer.command_join("LsxGuarded", signed(LsxGuarded, id="h3"))
        outbound.commands.clear()

        await consumer.command_params_changed({"q": "1"}, "?q=1")

        assert outbound.commands == []

    async def test_a_crashing_hook_is_a_refusal_not_a_pass(self):
        """An authorization query that fails must not be safer to trigger than one that says no.

        The exception still travels -- a crashing hook is a bug and needs its
        traceback -- but the component is gone before it does.
        """
        consumer, _ = make_consumer()

        with pytest.raises(RuntimeError, match="authorization query failed"):
            await consumer.repo.join("LsxBoom", {"id": "h5"})

        assert consumer.repo.get("h5") is None
        assert await consumer.repo.dispatch_event("h5", "bump", (), {}) is None

    async def test_a_crashing_hook_on_a_child_keeps_it_out_of_the_render(self):
        """On this path the exception is logged rather than propagated, as ``joined()``'s is.

        Logging it must not mean rendering the child: the parent's frame would
        carry the markup of a component whose guard never finished.
        """
        consumer, outbound = make_consumer()
        parent = await consumer.repo.join("LsxBoomParent", {"id": "h6"})

        await consumer.send_render(parent)

        assert ("boomkid", "boom") in CALLS, "the hook did run"
        assert consumer.repo.get("boomkid") is None
        children = [payload.get("children") or {} for payload in outbound.renders()]
        assert not any("boomkid" in c for c in children)

    async def test_hook_events_do_not_reach_it(self):
        consumer, outbound = make_consumer()
        await consumer.command_join("LsxGuarded", signed(LsxGuarded, id="h4"))
        outbound.commands.clear()

        await consumer.command_hook_event("h4", "hook-1", "ping", {})

        assert "render" not in outbound.kinds()


# --- AC4: combinations of individually valid signatures ------------------------------------


@pytest.mark.asyncio
class TestValidSignaturesDoNotCombine:
    """AC4. The interesting attack forges nothing: it pairs two honest tokens."""

    async def test_a_state_from_another_boundary_is_refused(self, admin_session, staff):
        token = signed(LsxGuarded, page=admin_session, user=staff, id="v1")
        consumer, outbound = make_consumer()  # an anonymous connection

        await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["reload"]
        assert outbound.commands[0][1]["reason"] == "live_session"
        assert consumer.repo.get("v1") is None

    async def test_a_state_issued_under_another_login_is_refused(self, admin_session, staff, other_staff):
        token = signed(LsxGuarded, page=admin_session, user=staff, id="v2")
        consumer, outbound = make_consumer(user=other_staff)

        await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["reload"]

    async def test_a_second_boundary_on_one_connection_is_refused(self, admin_session, staff):
        consumer, outbound = make_consumer(user=staff)

        await consumer.command_join("LsxGuarded", signed(LsxGuarded, page=admin_session, user=staff, id="v3"))
        assert consumer.live_session_name == "lsx-admin"
        outbound.commands.clear()

        await consumer.command_join("LsxFree", signed(LsxFree, id="v4"))

        assert outbound.kinds() == ["reload"], "a page has one boundary and so does its socket"

    async def test_an_unknown_boundary_name_is_refused(self, admin_session, staff):
        token = signed(LsxGuarded, page=admin_session, user=staff, id="v5")
        live_session_module._REGISTRY.pop("lsx-admin")
        consumer, outbound = make_consumer(user=staff)

        await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["reload"], "a renamed session reloads rather than falling back"

    async def test_the_predicate_refuses_the_join_as_it_refuses_the_view(self, admin_session, plain):
        """AC2 again, from the other side: the two enforcement points are one predicate."""
        token = signed(LsxGuarded, page=admin_session, user=plain, id="v6")
        consumer, outbound = make_consumer(user=plain)

        await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["reload"]

    async def test_the_envelope_carries_the_boundary_and_the_login(self, admin_session, staff):
        payload = unsign_envelope(signed(LsxGuarded, page=admin_session, user=staff, id="v7"), "LsxGuarded")

        assert payload.live_session == "lsx-admin"
        assert payload.auth == auth_fingerprint(staff, None)

    async def test_a_page_outside_a_boundary_binds_nothing(self):
        payload = unsign_envelope(signed(LsxFree, id="v8"), "LsxFree")

        assert payload.live_session == ""
        assert payload.auth is None, "binding a public page to a login would reload it on every login"

    async def test_the_rollout_flag_stops_applying_once_a_boundary_exists(self, admin_session, staff, monkeypatch):
        """``STATE_ACCEPT_LEGACY`` and a live_session cannot both be open.

        An old token names no boundary. For a component that declares where it
        belongs that is harmless -- it refuses to mount. For one that declares
        nothing it is not: the connection settles on "no policy" and the page's
        own ``authorize`` and hooks never run, even though the view carries the
        decorator. Nothing in the token separates the two cases, so a project
        that has declared a boundary takes the reload instead.
        """
        from django.core.signing import BadSignature

        from wireview.core import state as state_module

        def v1_for(component_class, component_id):
            return state_module.get_signer(state_module.V1_SALT).sign_object(
                '{"v":1,"n":"%s","d":{"id":"%s"}}' % (component_class._fqn, component_id),
                serializer=state_module._JSONStringSerializer,
                compress=True,
            )

        unbound = v1_for(LsxFree, "v9")
        monkeypatch.setattr(state_module.settings, "STATE_ACCEPT_LEGACY", True)

        with pytest.raises(BadSignature):
            unsign_envelope(unbound, "LsxFree")

        consumer, outbound = make_consumer(user=staff)
        await consumer.command_join("LsxFree", unbound)

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("v9") is None, "a page-level policy cannot be skipped by an old token"

    async def test_the_rollout_flag_still_works_without_a_boundary(self, staff, monkeypatch):
        """The window is only closed by boundaries, not by the upgrade itself."""
        from wireview.core import state as state_module

        live_session_module._REGISTRY.clear()
        monkeypatch.setattr(state_module.settings, "STATE_ACCEPT_LEGACY", True)
        v1 = state_module.get_signer(state_module.V1_SALT).sign_object(
            '{"v":1,"n":"%s","d":{"id":"v10"}}' % LsxFree._fqn,
            serializer=state_module._JSONStringSerializer,
            compress=True,
        )

        payload = unsign_envelope(v1, "LsxFree")

        assert payload.live_session == ""
        assert payload.state["id"] == "v10"


# --- AC6: a logout retires what it authenticated -------------------------------------------


class TestLogoutInvalidation:
    """AC6. A full page load refreshes an honest client; it does not close a held socket."""

    def test_the_fingerprint_moves_when_the_login_does(self, staff):
        anonymous = auth_fingerprint(AnonymousUser(), {})
        logged_in = auth_fingerprint(staff, {AUTH_GENERATION_KEY: "gen-1", "_auth_user_hash": "abc"})

        assert anonymous != logged_in

    def test_two_logins_by_the_same_user_fingerprint_differently(self, staff):
        """The generation nonce is the only thing that separates them.

        A pk is the same person on both sides of a logout and ``_auth_user_hash``
        only moves on a password change, so without the nonce a state issued
        before a logout would still be valid after logging back in.
        """
        first = auth_fingerprint(staff, {AUTH_GENERATION_KEY: "gen-1", "_auth_user_hash": "abc"})
        second = auth_fingerprint(staff, {AUTH_GENERATION_KEY: "gen-2", "_auth_user_hash": "abc"})

        assert first != second

    def test_an_unrelated_session_write_does_not_move_the_fingerprint(self, staff):
        """The signed-cookie backend's session_key is the whole cookie.

        It changes whenever *anything* is written to the session, so a
        fingerprint built on it would move for reasons that have nothing to do
        with authentication: open pages would reload and a later logout would
        publish to a topic nobody is listening on. The nonce does not move.
        """
        before = SessionView({AUTH_GENERATION_KEY: "gen-1", "cart": 1}, session_key="cookie-v1")
        after = SessionView({AUTH_GENERATION_KEY: "gen-1", "cart": 2}, session_key="cookie-v2")

        assert auth_fingerprint(staff, before) == auth_fingerprint(staff, after)

    def test_the_same_login_fingerprints_the_same(self, staff):
        session = SessionView({AUTH_GENERATION_KEY: "gen-1", "_auth_user_hash": "abc"})

        assert auth_fingerprint(staff, session) == auth_fingerprint(staff, session)

    def test_login_stamps_a_generation_on_the_session(self, staff):
        """Django fires ``user_logged_in`` at the end of login(), after cycle_key()."""
        from django.contrib.auth import login

        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, staff, backend="django.contrib.auth.backends.ModelBackend")

        assert request.session.get(AUTH_GENERATION_KEY)

    def test_a_second_login_stamps_a_different_generation(self, staff):
        from django.contrib.auth import login

        first = RequestFactory().get("/")
        first.session = SessionStore()
        login(first, staff, backend="django.contrib.auth.backends.ModelBackend")
        second = RequestFactory().get("/")
        second.session = SessionStore()
        login(second, staff, backend="django.contrib.auth.backends.ModelBackend")

        assert first.session[AUTH_GENERATION_KEY] != second.session[AUTH_GENERATION_KEY]

    @pytest.mark.asyncio
    async def test_a_connection_inside_a_boundary_listens_for_its_logout(self, admin_session, staff):
        consumer, outbound = make_consumer(user=staff)

        await consumer.command_join("LsxGuarded", signed(LsxGuarded, page=admin_session, user=staff, id="l1"))

        assert outbound.subscribed == [auth_topic(consumer.auth_fingerprint)]

    @pytest.mark.asyncio
    async def test_a_connection_outside_one_does_not(self):
        consumer, outbound = make_consumer()

        await consumer.command_join("LsxFree", signed(LsxFree, id="l2"))

        assert outbound.subscribed == [], "an unrelated logout must not close a public page's socket"

    @pytest.mark.asyncio
    async def test_a_logout_before_the_first_join_is_not_missed(self, admin_session, staff):
        """The subscription starts at the first join, so it cannot be the only check.

        A client is free to hold an open socket and delay its join. Between
        connect and that join the session it authenticated with can be flushed,
        and a publish at that moment reaches nobody. So the boundary re-reads the
        session from its backend before it admits anything.
        """
        with override_settings(SESSION_ENGINE=CACHE_SESSIONS):
            key = _make_session()
            token = signed(LsxGuarded, page=admin_session, user=staff, session=CacheSessionStore(key), id="l3")
            # The socket read its own copy at connect; that snapshot is what it
            # keeps, and no logout can reach into it.
            consumer, outbound = make_consumer(user=staff, session=CacheSessionStore(key))

            # The logout happens on another request, on its own store instance.
            CacheSessionStore(key).flush()

            await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("l3") is None

    @pytest.mark.asyncio
    async def test_a_live_session_still_joins_when_the_login_stands(self, admin_session, staff):
        """The control for the re-read: a session that is still there is not refused."""
        with override_settings(SESSION_ENGINE=CACHE_SESSIONS):
            key = _make_session()
            token = signed(LsxGuarded, page=admin_session, user=staff, session=CacheSessionStore(key), id="l4")
            consumer, outbound = make_consumer(user=staff, session=CacheSessionStore(key))
            await consumer.command_join("LsxGuarded", token)

        assert outbound.kinds() == ["render"]

    @pytest.mark.asyncio
    async def test_a_policy_reading_session_data_sees_the_fresh_session(self, staff):
        """The re-read replaces the snapshot; comparing fingerprints alone would not.

        A logout moves the fingerprint, so a fingerprint comparison catches it.
        Anything else a policy reads out of the session does not move it at all,
        and would still be answered from data that is no longer there.
        """
        policy = live_session("lsx-mfa", authorize=lambda ctx: bool(ctx.session.get("mfa")))
        with override_settings(SESSION_ENGINE=CACHE_SESSIONS):
            store = CacheSessionStore()
            store["mfa"] = True
            store.save()
            key = str(store.session_key)
            token = signed(LsxFree, page=policy, user=staff, session=CacheSessionStore(key), id="l5")
            consumer, outbound = make_consumer(user=staff, session=CacheSessionStore(key))

            # The step-up is revoked on another request; the fingerprint does not move.
            revoked = CacheSessionStore(key)
            del revoked["mfa"]
            revoked.save()

            await consumer.command_join("LsxFree", token)

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("l5") is None

    def test_logging_in_again_retires_the_generation_it_replaces(self, staff):
        """A step-up or re-auth overwrites the nonce, and no logout will ever name the old one.

        The expected topic is taken from the session as it stood after the first
        login -- which is what a connection opened then would have subscribed to.
        Recomputing it from the nonce alone would repeat whatever the
        implementation forgot to include, and pass while publishing somewhere
        nobody is listening.
        """
        from django.contrib.auth import login

        from wireview.core.transport import set_broker

        published: list[str] = []

        class RecordingBroker:
            async def publish(self, topic, message):
                published.append(topic)

            async def send_to_session(self, session_id, message): ...

        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, staff, backend="django.contrib.auth.backends.ModelBackend")
        first = request.session[AUTH_GENERATION_KEY]
        listening_on = auth_topic(auth_fingerprint(staff, dict(request.session.items())))

        set_broker(RecordingBroker())
        try:
            login(request, staff, backend="django.contrib.auth.backends.ModelBackend")
        finally:
            set_broker(None)

        assert request.session[AUTH_GENERATION_KEY] != first
        assert published == [listening_on]

    @pytest.mark.asyncio
    async def test_the_invalidation_message_closes_the_socket(self):
        consumer, _ = make_consumer()
        closed: list[int] = []

        async def close(code=None):
            closed.append(code)

        consumer.close = close  # type: ignore[assignment]

        await consumer.session_invalidated({"reason": "logged out"})

        assert closed == [4001]

    def test_logging_out_publishes_to_the_topic_the_socket_is_on(self, staff):
        from wireview.core.live_session import invalidate_authentication
        from wireview.core.transport import set_broker

        published: list[tuple[str, dict[str, t.Any]]] = []

        class RecordingBroker:
            async def publish(self, topic, message):
                published.append((topic, message))

            async def send_to_session(self, session_id, message): ...

        session = SessionView({AUTH_GENERATION_KEY: "gen-1", "_auth_user_hash": "abc"})
        set_broker(RecordingBroker())
        try:
            invalidate_authentication(staff, session)
        finally:
            set_broker(None)

        assert published == [
            (auth_topic(auth_fingerprint(staff, session)), {"type": "session_invalidated", "reason": "logged out"})
        ]
