"""``_on_mount`` hooks actually run, on every path that mounts a component (#75, GAP-021).

The hooks were defined and documented as an authentication boundary, but nothing called
them. That makes the boundary the interesting part of these tests: the first HTML a
browser gets is as much a leak as the WebSocket join, so the hooks have to cover the dead
render too, and they have to run exactly once per instance.
"""

import asyncio
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import override_settings

from wireview import Component, LiveComponent
from wireview import checks as wireview_checks
from wireview.checks import check_on_mount_hooks
from wireview.consumer import WireviewConsumer
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository
from wireview.testing import mount

pytestmark = pytest.mark.unit

TEMPLATES = {
    "lh/plain.html": "{% load wireview %}<div {% tag_header %}>{{ this.label }}</div>",
    "lh/page.html": ("{% load wireview %}<main {% tag_header %}>page{% component 'LhNested' id='nested' %}</main>"),
    "lh/twice.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component 'LhNested' id='nested' %}{% component 'LhNested' id='nested' %}</main>"
    ),
    "lh/nested.html": "{% load wireview %}<p {% tag_header %}>nested</p>",
    "lh/parent.html": "{% load wireview %}<main {% tag_header %}>{% live_component 'LhChild' id='child' %}</main>",
    "lh/halt_parent.html": (
        "{% load wireview %}<main {% tag_header %}>{% live_component 'LhHaltChild' id='child' %}</main>"
    ),
    "lh/child.html": "{% load wireview %}<p {% live_tag_header %}>child {{ this.loaded }}</p>",
}

#: ``(what, component id)`` in the order it happened.
CALLS: list[tuple[str, str]] = []

#: ``(params, session)`` as ``SeenHook`` received them.
SEEN: list[tuple[dict[str, t.Any], dict[str, t.Any]]] = []


# --- hooks --------------------------------------------------------------------------------


class HookOne:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append(("one", component.id))
        return {"cont": True}


class HookTwo:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append(("two", component.id))
        return {"cont": True}


class HaltHook:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append(("halt", component.id))
        return {"halt": True}


class RedirectHook:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append(("redirect", component.id))
        await component.wire.redirect_to("/login")
        return {"halt": True}


class SeenHook:
    @staticmethod
    async def on_mount(component, params, session):
        SEEN.append((dict(params), dict(session)))
        return {"cont": True}


# --- components ---------------------------------------------------------------------------


class LhOrdered(Component):
    _template_name = "lh/plain.html"
    _on_mount = [HookOne, HookTwo]
    label: str = "ordered"

    async def joined(self):
        CALLS.append(("joined", self.id))


class LhHalted(Component):
    _template_name = "lh/plain.html"
    _on_mount = [HaltHook, HookTwo]
    label: str = "halted"

    async def joined(self):
        CALLS.append(("joined", self.id))


class LhRedirect(Component):
    _template_name = "lh/plain.html"
    _on_mount = [RedirectHook]
    label: str = "redirect"

    async def joined(self):
        CALLS.append(("joined", self.id))


class LhSeen(Component):
    _template_name = "lh/plain.html"
    _on_mount = [SeenHook]
    label: str = "seen"


class LhPage(Component):
    _template_name = "lh/page.html"
    _on_mount = [HookOne]


class LhTwice(Component):
    _template_name = "lh/twice.html"


class LhNested(Component):
    _template_name = "lh/nested.html"
    _on_mount = [HookTwo]


class LhParent(Component):
    _template_name = "lh/parent.html"


class LhHaltParent(Component):
    _template_name = "lh/halt_parent.html"


class LhChild(LiveComponent):
    _template_name = "lh/child.html"
    _on_mount = [HookOne]
    loaded: str = ""

    async def joined(self):
        CALLS.append(("joined", self.id))
        self.loaded = "ready"


class LhHaltChild(LiveComponent):
    _template_name = "lh/child.html"
    _on_mount = [HaltHook, HookTwo]
    loaded: str = ""

    async def joined(self):
        CALLS.append(("joined", self.id))
        self.loaded = "ready"


# --- harness ------------------------------------------------------------------------------


class RecordingLayer:
    """Channel layer that keeps the session messages ``WireviewMeta.send`` produces."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, t.Any]]] = []

    async def send(self, channel: str, message: dict[str, t.Any]) -> None:
        self.sent.append((channel, message))

    async def group_send(self, group: str, message: dict[str, t.Any]) -> None:
        self.sent.append((group, message))

    async def group_add(self, group: str, channel: str) -> None: ...

    async def group_discard(self, group: str, channel: str) -> None: ...


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...

    def renders(self) -> list[dict[str, t.Any]]:
        return [payload for command, payload in self.commands if command == "render"]


@pytest.fixture(autouse=True)
def _templates_and_calls():
    CALLS.clear()
    SEEN.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield
    CALLS.clear()
    SEEN.clear()


def make_consumer(
    params: dict[str, t.Any] | None = None,
    session: t.Any = None,
    layer: RecordingLayer | None = None,
) -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(
        is_live=True,
        user=AnonymousUser(),
        params=params,
        session=session,
        channel_name="test-channel" if layer is not None else None,
        channel_layer=layer,  # type: ignore[arg-type]
    )
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def signed(component_class, **state) -> str:
    from wireview.core.meta import WireviewMeta

    return sign_state(component_class(user=AnonymousUser(), wire=WireviewMeta(params={}), **state))


def kinds(component_id: str) -> list[str]:
    return [kind for kind, cid in CALLS if cid == component_id]


def commands_of(layer: RecordingLayer) -> list[str]:
    return [message["command"] for _channel, message in layer.sent if "command" in message]


# --- mount(): order, once, halt ------------------------------------------------------------


class TestMountHelper:
    pytestmark = pytest.mark.asyncio

    async def test_hooks_run_in_order_before_joined(self):
        view = await mount(LhOrdered, id="m1")

        assert kinds("m1") == ["one", "two", "joined"]
        assert view.component.id == "m1"

    async def test_hooks_run_once_per_instance(self):
        view = await mount(LhOrdered, id="m2")
        # A second _mount() is what a re-render would do; it must be a no-op.
        assert await view.component._mount({}, {}) is True

        assert kinds("m2") == ["one", "two", "joined"]

    async def test_halt_skips_the_remaining_hooks_joined_and_the_render(self):
        """``mount()`` has to reach the same verdict a request would.

        It used to render a halted component, which made a unit test say the
        guard let the markup through on a path where the server does not. A test
        helper that disagrees with the server about a boundary is worse than no
        test.
        """
        view = await mount(LhHalted, id="m3")

        assert kinds("m3") == ["halt"], "the second hook and joined() must not run"
        assert "halted" not in (view.render() or "")

    async def test_a_repeat_mount_answers_the_same_way_it_did_first(self):
        view = await mount(LhHalted, id="m4")

        assert await view.component._mount({}, {}) is False
        assert kinds("m4") == ["halt"]

    async def test_hooks_receive_the_params_and_session_mount_was_given(self):
        await mount(LhSeen, id="m5", params={"q": "x"}, session={"uid": 7})

        assert SEEN == [({"q": "x"}, {"uid": 7})]

    async def test_a_hook_exception_propagates(self):
        class Boom:
            @staticmethod
            async def on_mount(component, params, session):
                raise RuntimeError("hook blew up")

        class LhBoom(Component):
            _template_name = "lh/plain.html"
            _on_mount = [Boom]

        with pytest.raises(RuntimeError, match="hook blew up"):
            await mount(LhBoom, id="m6")


# --- WebSocket join ------------------------------------------------------------------------


@pytest.mark.django_db
class TestWebSocketJoin:
    pytestmark = pytest.mark.asyncio

    async def test_join_runs_the_hooks_with_the_repos_params_and_session(self):
        consumer, _ = make_consumer(params={"page": "2"}, session={"uid": 9})

        await consumer.repo.join("LhSeen", {"id": "ws1"})

        assert SEEN == [({"page": "2"}, {"uid": 9})]

    async def test_join_runs_the_hooks_before_joined(self):
        consumer, _ = make_consumer()

        await consumer.repo.join("LhOrdered", {"id": "ws2"})

        assert kinds("ws2") == ["one", "two", "joined"]

    async def test_a_halting_hook_gives_up_the_component_instead_of_rendering_it(self):
        """#58 narrowed this: a halt used to skip ``joined()`` and render anyway.

        Rendering a refused component ships exactly what the hook was refusing --
        its HTML and a freshly signed ``data-state``. The instance leaves the
        repository with it, because ``component_remove()`` only tells the client
        to drop the element and an instance left behind keeps taking events.
        """
        consumer, outbound = make_consumer()

        component = await consumer.repo.join("LhHalted", {"id": "ws3"})

        assert kinds("ws3") == ["halt"]
        assert component.id == "ws3", "the caller still gets it, to flush what the hook queued"
        assert consumer.repo.get("ws3") is None, "a refused component is not an event target"
        await consumer.send_render(component)
        assert "halted" not in str(outbound.renders())

    async def test_a_redirecting_hook_sends_url_change_and_no_render(self):
        layer = RecordingLayer()
        consumer, outbound = make_consumer(layer=layer)

        await consumer.command_join("LhRedirect", signed(LhRedirect, id="ws4"))

        assert kinds("ws4") == ["redirect"], "joined() must not run behind a halt"
        assert outbound.renders() == [], "a frozen component has nothing to render"
        assert commands_of(layer) == ["url_change"]
        assert layer.sent[0][1]["kwargs"] == {"command": "redirect", "url": "/login"}

    async def test_a_rejoin_replaces_the_instance_so_the_hooks_run_again(self):
        consumer, _ = make_consumer()
        await consumer.command_join("LhOrdered", signed(LhOrdered, id="ws5"))
        first = consumer.repo.get("ws5")

        await consumer.command_join("LhOrdered", signed(LhOrdered, id="ws5"))

        assert consumer.repo.get("ws5") is not first
        assert kinds("ws5") == ["one", "two", "joined", "one", "two", "joined"]

    async def test_a_re_render_does_not_run_the_hooks_again(self):
        consumer, _ = make_consumer()
        component = await consumer.repo.join("LhOrdered", {"id": "ws6"})

        await consumer.send_render(component)
        await consumer.send_render(component)

        assert kinds("ws6") == ["one", "two", "joined"]


# --- LiveComponent children ----------------------------------------------------------------


@pytest.mark.django_db
class TestLiveComponentChildren:
    pytestmark = pytest.mark.asyncio

    async def test_a_child_named_by_the_parents_render_mounts_before_joined(self):
        consumer, _ = make_consumer()
        parent = await consumer.repo.join("LhParent", {"id": "p1"})

        await consumer.send_render(parent)

        assert kinds("child") == ["one", "joined"]

    async def test_a_halting_child_never_ships_its_diff(self):
        """A refused child leaves a reference marker in the parent and nothing else.

        The parent's template registers and names the child during its own pass,
        so a check that ran later would run after the child's HTML had already
        left (``docs/design/live-session.md`` §3-5). The child's diff travels
        separately, under ``children``, which is where the refusal takes effect.
        """
        consumer, outbound = make_consumer()
        parent = await consumer.repo.join("LhHaltParent", {"id": "p2"})

        await consumer.send_render(parent)

        assert kinds("child") == ["halt"], "the second hook and joined() must not run"
        children = [payload.get("children") or {} for payload in outbound.renders()]
        assert not any("child" in c for c in children), "a refused child ships no diff"
        assert consumer.repo.get("child") is None, "and is not an event target"

    async def test_a_child_mounts_once_across_the_parents_re_renders(self):
        consumer, _ = make_consumer()
        parent = await consumer.repo.join("LhParent", {"id": "p3"})

        await consumer.send_render(parent)
        await consumer.send_render(parent)

        assert kinds("child") == ["one", "joined"]


# --- HTTP (dead) render ---------------------------------------------------------------------


def render_page(source: str) -> str:
    return Template("{% load wireview %}" + source).render(Context({}))


class TestHttpRender:
    """The first HTML is inside the boundary: a hook that guards a page guards this too."""

    def test_the_page_component_runs_its_hooks(self):
        html = render_page("{% component 'LhOrdered' id='h1' %}")

        assert kinds("h1") == ["one", "two"], "no joined() on a dead render"
        assert "ordered" in html

    def test_a_nested_component_runs_its_own_hooks_once(self):
        render_page("{% component 'LhPage' id='h2' %}")

        assert kinds("h2") == ["one"]
        assert kinds("nested") == ["two"]

    def test_the_same_nested_id_twice_in_one_pass_still_mounts_once(self):
        render_page("{% component 'LhTwice' id='h3' %}")

        assert kinds("nested") == ["two"]

    def test_a_live_component_child_runs_its_hooks(self):
        render_page("{% component 'LhParent' id='h4' %}")

        assert kinds("child") == ["one"], "no joined() on a dead render"

    def test_a_redirecting_hook_renders_a_meta_refresh(self):
        html = render_page("{% component 'LhRedirect' id='h5' %}")

        assert kinds("h5") == ["redirect"]
        assert '<meta http-equiv="refresh" content="0; url=/login">' in html
        assert "redirect</div>" not in html, "the protected HTML must not ship"

    def test_an_async_views_own_template_pass_still_runs_the_hooks(self):
        """An ``async def`` view that calls ``render()`` renders on the loop thread.

        ``async_to_sync`` refuses to run there, so before this the page 500'd for
        any component declaring ``_on_mount``. The hooks get their own loop on a
        helper thread instead.
        """

        async def async_view_like() -> str:
            return render_page("{% component 'LhOrdered' id='h7' %}")

        html = asyncio.run(async_view_like())

        assert kinds("h7") == ["one", "two"]
        assert "ordered" in html

    def test_a_halt_on_the_loop_thread_renders_nothing(self):
        """The helper-thread path reaches the same verdict as the ordinary one.

        A dead render is the first HTML and the first ``data-state``: a halt that
        still rendered would put both in a response no join can recall (#58, AC2).
        """

        async def async_view_like() -> str:
            return render_page("{% component 'LhHalted' id='h8' %}")

        html = asyncio.run(async_view_like())

        assert kinds("h8") == ["halt"]
        assert "halted" not in html
        assert "data-state" not in html

    def test_the_repository_carries_the_request_session(self):
        class FakeRequest:
            META = {"QUERY_STRING": "q=1"}
            session = {"uid": 3}

        Template("{% load wireview %}{% component 'LhSeen' id='h6' %}").render(Context({"request": FakeRequest()}))

        assert SEEN == [({"q": "1"}, {"uid": 3})]


# --- the consumer plumbs the connection session ----------------------------------------------


@pytest.mark.asyncio
async def test_connect_hands_the_scope_session_to_the_repository():
    consumer = WireviewConsumer()
    consumer.scope = {"user": AnonymousUser(), "session": {"uid": 11}}
    consumer.channel_name = "test-channel"
    consumer.channel_layer = None  # type: ignore[assignment]
    accepted: list[dict[str, t.Any]] = []

    async def base_send(message: dict[str, t.Any]) -> None:
        accepted.append(message)

    consumer.base_send = base_send  # type: ignore[assignment]

    await consumer.connect()

    assert accepted and accepted[0]["type"] == "websocket.accept"
    assert consumer.repo.session == {"uid": 11}


# --- wireview.W007 ----------------------------------------------------------------------------


def make_component(class_name: str, **namespace) -> type[Component]:
    return type(
        class_name,
        (Component,),
        {"__module__": "probeapp.live", "_template_name": "lh/plain.html", **namespace},
    )


@pytest.fixture
def only(monkeypatch):
    """Restrict the check to the classes a test just built."""

    def _only(*classes):
        monkeypatch.setattr(wireview_checks, "iter_component_classes", lambda: iter(classes))

    return _only


class TestOnMountCheck:
    """W007: an ``_on_mount`` entry wireview cannot call is skipped in silence."""

    def test_a_valid_hook_is_silent(self, only):
        only(make_component("ProbeHookOk", _on_mount=[HookOne]))

        assert check_on_mount_hooks(None) == []

    def test_no_hooks_is_silent(self, only):
        only(make_component("ProbeHookNone"))

        assert check_on_mount_hooks(None) == []

    def test_a_class_without_on_mount_is_flagged(self, only):
        class NotAHook:
            pass

        only(make_component("ProbeHookMissing", _on_mount=[NotAHook]))

        messages = check_on_mount_hooks(None)
        assert [m.id for m in messages] == ["wireview.W007"]
        assert "NotAHook" in messages[0].msg and "no 'on_mount'" in messages[0].msg

    def test_a_sync_on_mount_is_flagged(self, only):
        class SyncHook:
            @staticmethod
            def on_mount(component, params, session):
                return {"cont": True}

        only(make_component("ProbeHookSync", _on_mount=[SyncHook]))

        messages = check_on_mount_hooks(None)
        assert [m.id for m in messages] == ["wireview.W007"]
        assert "is not async" in messages[0].msg

    def test_the_check_is_registered(self):
        from django.core.checks import registry

        assert check_on_mount_hooks in registry.registry.get_checks()
