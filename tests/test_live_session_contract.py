"""The contract ``live_session`` keeps, stated as invariants rather than as regressions.

``tests/test_live_session.py`` is the behaviour suite: it walks the acceptance
conditions of ``docs/design/live-session.md`` and pins the specific things that
went wrong on the way to them. This file is the other half. It states what has
to be true of *any* implementation of a page boundary and checks it the same way
everywhere, so that the thing which fails when someone adds a new way to build a
component is a test about the boundary rather than a test about a bug.

Two ideas carry it.

**A boundary is only as good as its narrowest path.** A component can come into
existence seven ways, and an authorization that covers six of them is not an
authorization. So the paths are a parametrized list, every one of them is run
against every kind of refusal, and the same five assertions are made each time.
Adding a path means adding a row; forgetting to gate it means the row fails.

**A suite that only proves refusal proves nothing.** Every refusal case is
paired with an admission case on the same path, because "refuses everything" and
"refuses the right things" look identical from one side.
"""

import asyncio
import re
import typing as t
from dataclasses import dataclass, field
from html import unescape
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.cache import SessionStore as CacheSessionStore
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from wireview import Component, LiveComponent, live_session, mount
from wireview.consumer import WireviewConsumer
from wireview.core import live_session as live_session_module
from wireview.core.live_session import (
    AUTH_GENERATION_KEY,
    AUTH_TOPIC_PREFIX,
    LiveSession,
    auth_fingerprint,
    auth_topic,
)
from wireview.core.meta import WireviewMeta
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

#: The boundary every test in this file draws. Declared per test by the fixture.
BOUNDARY = "cx-inside"

#: A boundary the components under test never belong to.
ELSEWHERE = "cx-elsewhere"

#: Session backend for the tests that need a real store to flush mid-test. The db
#: backend would need a write from the bridge thread while the test transaction is
#: open, which sqlite answers with "database is locked".
CACHE_SESSIONS = "django.contrib.sessions.backends.cache"

#: Rendered by every component here. Its presence in a payload means the
#: component's markup got out.
SECRET = "CX-SECRET"

#: The attribute ``{% tag_header %}`` writes beside ``data-state``.
_MARKER = 'data-name="'

#: A field value that only a restored child can have: the parent's template never
#: passes it. Its presence proves the stored state came back rather than the child
#: being rebuilt from props, which is what the restore path is for.
RESTORED = "restored-only"

TEMPLATE = "{% load wireview %}<p {% tag_header %}>" + SECRET + " {{ this.note }}</p>"

TEMPLATES = {
    "cx/leaf.html": TEMPLATE,
    "cx/nest.html": "{% load wireview %}<main {% tag_header %}>{% component NAME id='target' %}</main>",
    # The child only appears once the parent's state says so, so its first render is
    # triggered by an event or by params rather than by the join.
    "cx/later.html": (
        "{% load wireview %}<main {% tag_header %}>{% if this.show %}{% component NAME id='target' %}{% endif %}</main>"
    ),
    # A nested tag inside a slot: the fill renders in the parent's pass, and the
    # question is whether the repository and its boundary travel with it.
    "cx/slotted.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component_block 'CxSlotHost' id='host' %}{% component NAME id='target' %}{% endcomponent %}</main>"
    ),
    "cx/slothost.html": "{% load wireview %}<div {% tag_header %}>{% render_slot slots.default %}</div>",
    "cx/live.html": "{% load wireview %}<main {% tag_header %}>{% live_component NAME id='target' %}</main>",
}

#: Every hook call, in order: ``(component id, label)``.
CALLS: list[tuple[str, str]] = []


def target_hook(outcome: str):
    """A session hook that refuses only the component under test.

    The nested paths put the target under a parent, and a session hook refuses
    per component. Refusing the parent too would prove nothing about the child.
    """

    class Hook:
        @staticmethod
        async def on_mount(component, params, session):
            CALLS.append((component.id, "session"))
            if component.id != "target":
                return {"cont": True}
            if outcome == "halt":
                return {"halt": True}
            if outcome == "raise":
                raise RuntimeError("the authorization query failed")
            return {"cont": True}

    Hook.__name__ = f"CxSessionHook_{outcome}"
    return Hook


def hook(label: str, *, outcome: str = "cont"):
    """A mount hook that records that it ran and then does ``outcome``."""

    class Hook:
        @staticmethod
        async def on_mount(component, params, session):
            CALLS.append((component.id, label))
            if outcome == "halt":
                return {"halt": True}
            if outcome == "raise":
                raise RuntimeError("the authorization query failed")
            return {"cont": True}

    Hook.__name__ = f"CxHook_{label}_{outcome}"
    return Hook


def _component(name: str, base: type, **namespace: t.Any) -> type:
    """Declare a component class under a name unique to this module.

    ``note`` is annotated rather than assigned: Pydantic wants a type for every
    field, and building these classes with ``type()`` skips the annotation a
    normal class body would have carried.
    """
    annotations = namespace.setdefault("__annotations__", {})
    annotations.setdefault("note", str)
    if "show" in namespace:
        annotations.setdefault("show", bool)
    return type(
        name,
        (base,),
        {"__module__": "cxprobe.live", "_template_name": "cx/leaf.html", "note": "ok", **namespace},
    )


async def _bump(self) -> None:
    """The observable an event has to fail to reach."""
    self.note = "bumped"


async def _reveal(self) -> None:
    """Turn on the part of the template that names the child."""
    self.show = True


async def _reveal_on_params(self, params, uri) -> None:
    self.show = params.get("show") == "1"


async def _joined(self) -> None:
    """Recorded so the matrix can see lifecycle that ran before, or without, admission.

    It also opens an upload, because ``joined()`` is where an upload registry comes
    into existence -- and a probe with no registry would make every upload command
    a no-op even when the component is admitted, which would leave those rows of
    ``TestARefusedIdAnswersNothing`` proving nothing.
    """
    CALLS.append((self.id, "joined"))
    self.allow_upload("files", accept=[".txt"], max_entries=2)


async def _handle_hook_event(self, hook_id: str, event: str, payload: dict) -> t.Any:
    """Answer a client hook, so ``hook_event`` has an effect to observe."""
    return {"echo": event}


_PARENTS: dict[str, type] = {}


def _parent(name: str, template: str) -> type:
    """A host component for the nested paths, declared once.

    Component names register globally and warn on a collision, so building the
    same one per parametrized run would fill the output with warnings about a
    class colliding with itself.
    """
    if name not in _PARENTS:
        _PARENTS[name] = _component(name, Component, _template_name=template)
    return _PARENTS[name]


# --- the components, one per refusal kind ---------------------------------------------------
#
# Each kind is declared twice, as a Component and as a LiveComponent, because the
# LiveComponent path can only be exercised by the latter. The pair is otherwise
# identical, which is the point: a boundary that behaves differently for the two
# is a boundary with a hole in it.


def _pair(suffix: str, **namespace: t.Any) -> tuple[type, type]:
    return (
        _component(
            f"Cx{suffix}",
            Component,
            bump=_bump,
            joined=_joined,
            handle_hook_event=_handle_hook_event,
            **dict(namespace),
        ),
        _component(
            f"CxLive{suffix}",
            LiveComponent,
            bump=_bump,
            joined=_joined,
            handle_hook_event=_handle_hook_event,
            **dict(namespace),
        ),
    )


CxOk, CxLiveOk = _pair("Ok", _on_mount=[hook("component")])
CxElsewhere, CxLiveElsewhere = _pair("Elsewhere", _live_sessions={ELSEWHERE})
CxHalts, CxLiveHalts = _pair("Halts", _on_mount=[hook("component", outcome="halt")])
CxCrashes, CxLiveCrashes = _pair("Crashes", _on_mount=[hook("component", outcome="raise")])


@dataclass(frozen=True)
class Refusal:
    """One way a component can fail to be admitted."""

    name: str
    #: (Component subclass, LiveComponent subclass) that fails this way.
    classes: tuple[type, type]
    #: What the *page's* hook does to the target. The component-side refusals leave
    #: it at "cont"; the session-side ones are refusals the component knows nothing
    #: about, which is the point of a boundary being a layer under it.
    session_outcome: str = "cont"
    #: Whether the failure reaches the caller as an exception rather than as a verdict.
    raises: bool = False
    #: Whether any mount hook gets to run before the refusal. ``_live_sessions`` is
    #: answered first and on its own, so a component that does not belong on the
    #: page causes no hook side effects at all -- not even the page's own.
    runs_hooks: bool = True


ADMITTED = (CxOk, CxLiveOk)

REFUSALS = [
    # The class says where it lives, and this page is not it.
    Refusal("declaration", (CxElsewhere, CxLiveElsewhere), runs_hooks=False),
    # The component's own hook refuses on purpose.
    Refusal("halt", (CxHalts, CxLiveHalts)),
    # The component's own hook fails. Not the same as refusing, and it must not be safer.
    Refusal("exception", (CxCrashes, CxLiveCrashes), raises=True),
    # The page's hook refuses a component that had no objection of its own.
    Refusal("session_halt", ADMITTED, session_outcome="halt"),
    # The page's hook fails.
    Refusal("session_exception", ADMITTED, session_outcome="raise", raises=True),
]


@dataclass
class Outcome:
    """What one mount path produced for the component under test."""

    #: Everything the browser (or the HTTP response) would have received. On the
    #: nested paths that includes the parent, which is the point: the parent is
    #: allowed to be there, and the question is whether the child came with it.
    payload: str
    #: The repository the component would live in, or ``None`` for a path without one.
    repo: ComponentRepository | None
    component_id: str
    #: The class under test, so an assertion can look for *its* root element.
    component_class: type
    raised: BaseException | None = None
    #: Hook labels recorded for this component, in order.
    calls: list[str] = field(default_factory=list)
    #: The ``MountedComponent`` on the ``testing.mount()`` path, which has no repository.
    mounted: t.Any = None

    @property
    def root_element_marker(self) -> str:
        """What ``{% tag_header %}`` writes for this class, and nothing else.

        The element it names is the one carrying ``data-state``, so its absence
        is what "no signed state got out" means. Looking for the literal
        ``data-state`` instead would find the *parent's*, which belongs there.
        """
        return f'data-name="{self.component_class.__name__}"'


# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [
                        ("django.template.loaders.locmem.Loader", TEMPLATES),
                        "django.template.loaders.app_directories.Loader",
                    ]
                },
            }
        ]
    ):
        yield


@pytest.fixture(autouse=True)
def _registry():
    CALLS.clear()
    saved = dict(live_session_module._REGISTRY)
    live_session_module._REGISTRY.clear()
    yield
    live_session_module._REGISTRY.clear()
    live_session_module._REGISTRY.update(saved)


def declare_boundary(session_outcome: str = "cont") -> LiveSession:
    """Declare the page boundary under test, with the given verdict for the target."""
    live_session(ELSEWHERE)
    return live_session(BOUNDARY, on_mount=[target_hook(session_outcome)])


@pytest.fixture
def boundary() -> LiveSession:
    """The page boundary under test. Admits everyone; refusals here are never about the user."""
    return declare_boundary()


@pytest.fixture
def user():
    return get_user_model().objects.create_user(f"cx-{uuid4().hex[:8]}", password="x", is_staff=True)


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.subscribed: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None: ...

    def kinds(self) -> list[str]:
        return [command for command, _ in self.commands]


def make_consumer(*, boundary=None, user=None, session=None) -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(
        is_live=True, user=user or AnonymousUser(), session=session, live_session=boundary
    )
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    consumer.auth_fingerprint = auth_fingerprint(consumer.repo.user, consumer.repo.session)
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def signed(component_class: type, *, page: LiveSession | None, user=None, session=None, **state) -> str:
    component = component_class(
        user=user or AnonymousUser(),
        session=SessionView.wrap(session),
        wire=WireviewMeta(params={}, live_session=page),
        **state,
    )
    return sign_state(component)


def calls_for(component_id: str) -> list[str]:
    return [label for cid, label in CALLS if cid == component_id]


# --- the mount paths -------------------------------------------------------------------------
#
# Each one builds the component under test the way the framework does on that
# path, and answers the same questions about the result. They are async because
# most of them are; the sync ones simply do not await anything interesting.


def _template_naming(template: str, cls: type):
    """Point one of the host templates at the class under test.

    The nested tags take a component *name*, and the name is what decides which
    class the path builds, so the template has to be rewritten per parametrized
    run rather than once.
    """
    return override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [
                        (
                            "django.template.loaders.locmem.Loader",
                            TEMPLATES | {template: TEMPLATES[template].replace("NAME", repr(cls.__name__))},
                        ),
                        "django.template.loaders.app_directories.Loader",
                    ]
                },
            }
        ]
    )


async def path_dead_render(cls: type, boundary: LiveSession) -> Outcome:
    """``{% component %}`` in an HTTP response. The first bytes, which no join can recall."""
    repo = ComponentRepository(is_live=False, live_session=boundary)
    raised = None
    html = ""
    try:
        html = Template("{% load wireview %}{% component name id='target' %}").render(
            Context({"wireview_repository": repo, "name": cls.__name__})
        )
    except Exception as e:  # a crashing hook stops the page rather than rendering it
        raised = e
    return Outcome(html, repo, "target", cls, raised, calls_for("target"))


async def path_testing_mount(cls: type, boundary: LiveSession) -> Outcome:
    """``wireview.testing.mount()``. A unit test must reach the same verdict as a request."""
    raised = None
    mounted = None
    try:
        mounted = await mount(cls, id="target", live_session=boundary)
    except Exception as e:
        raised = e
    # What the helper actually produced. Synthesising this from ``mount_halted``
    # asserted the flag twice and the render never, which is how the helper came to
    # render a refused component while every other path refused it.
    payload = "" if mounted is None else (mounted.render() or "")
    return Outcome(payload, None, "target", cls, raised, calls_for("target"), mounted=mounted)


async def path_root_join(cls: type, boundary: LiveSession) -> Outcome:
    """The WebSocket join of a page's root component."""
    consumer, outbound = make_consumer(boundary=boundary)
    token = signed(cls, page=boundary, id="target")
    raised = None
    try:
        await consumer.command_join(cls.__name__, token)
    except Exception as e:
        raised = e
    return Outcome(str(outbound.commands), consumer.repo, "target", cls, raised, calls_for("target"))


async def path_rejoin(cls: type, boundary: LiveSession) -> Outcome:
    """A second join for an id that already joined: boost navigation brought new DOM."""
    consumer, outbound = make_consumer(boundary=boundary)
    token = signed(cls, page=boundary, id="target")
    raised = None
    try:
        await consumer.command_join(cls.__name__, token)
        outbound.commands.clear()
        CALLS.clear()
        await consumer.command_join(cls.__name__, token)
    except Exception as e:
        raised = e
    return Outcome(str(outbound.commands), consumer.repo, "target", cls, raised, calls_for("target"))


async def path_nested_plain(cls: type, boundary: LiveSession) -> Outcome:
    """An ordinary ``{% component %}`` inside a live parent, rendered inline during its pass."""
    consumer, outbound = make_consumer(boundary=boundary)
    parent_class = _parent(f"CxNestOf{cls.__name__}", "cx/nest.html")
    with _template_naming("cx/nest.html", cls):
        raised = None
        try:
            parent = await consumer.repo.join(parent_class.__name__, {"id": "parent"})
            await consumer.send_render(parent)
        except Exception as e:
            raised = e
    return Outcome(str(outbound.commands), consumer.repo, "target", cls, raised, calls_for("target"))


async def path_live_child(cls: type, boundary: LiveSession) -> Outcome:
    """A LiveComponent the parent's render named. Settled after the pass, shipped in the same frame."""
    consumer, outbound = make_consumer(boundary=boundary)
    parent_class = _parent(f"CxLiveParentOf{cls.__name__}", "cx/live.html")
    with _template_naming("cx/live.html", cls):
        raised = None
        try:
            parent = await consumer.repo.join(parent_class.__name__, {"id": "parent"})
            await consumer.send_render(parent)
        except Exception as e:
            raised = e
    return Outcome(str(outbound.commands), consumer.repo, "target", cls, raised, calls_for("target"))


async def path_child_restore(cls: type, boundary: LiveSession) -> Outcome:
    """A child's stored state arriving in the parent's join, to be restored under it."""
    consumer, outbound = make_consumer(boundary=boundary)
    parent_class = _parent(f"CxRestoreParentOf{cls.__name__}", "cx/live.html")
    child_token = signed(cls, page=boundary, id="target", note=RESTORED)
    with _template_naming("cx/live.html", cls):
        raised = None
        try:
            await consumer.command_join(
                parent_class.__name__,
                signed(parent_class, page=boundary, id="parent"),
                # A pair, not a mapping: ``command_join`` unpacks ``(name, state)``,
                # and handing it a dict iterated the keys instead, so every child
                # failed its signature and was quietly rebuilt from template props.
                # The path looked exercised and was not.
                children={"target": (cls.__name__, child_token)},
            )
        except Exception as e:
            raised = e
    return Outcome(str(outbound.commands), consumer.repo, "target", cls, raised, calls_for("target"))


async def path_dead_live_child(cls: type, boundary: LiveSession) -> Outcome:
    """A ``{% live_component %}`` in an HTTP response.

    Its own branch: on a dead render the child is mounted and rendered inline
    rather than left as a reference for the consumer to settle, so skipping the
    check here puts a protected child in the first response.
    """
    repo = ComponentRepository(is_live=False, live_session=boundary)
    parent_class = _parent(f"CxDeadLiveParentOf{cls.__name__}", "cx/live.html")
    raised = None
    html = ""
    with _template_naming("cx/live.html", cls):
        try:
            html = Template("{% load wireview %}{% component parent id='parent' %}").render(
                Context({"wireview_repository": repo, "parent": parent_class.__name__})
            )
        except Exception as e:
            raised = e
    return Outcome(html, repo, "target", cls, raised, calls_for("target"))


#: Paths on which a failing hook reaches the caller rather than being logged. The
#: consumer absorbs an exception from a child so one bad component cannot take the
#: page down; a template render has nobody to absorb it and the request fails,
#: which is the louder and safer answer for a page that has not been sent yet.
PATHS_THAT_PROPAGATE = {"dead_render", "dead_live_child", "testing_mount", "nested_plain"}

#: ``(name, adapter, index into Refusal.classes)``. The index picks the Component
#: or the LiveComponent form, because some of the paths only exist for the latter.
PATHS = [
    ("dead_render", path_dead_render, 0),
    ("dead_live_child", path_dead_live_child, 1),
    ("testing_mount", path_testing_mount, 0),
    ("root_join", path_root_join, 0),
    ("rejoin", path_rejoin, 0),
    ("nested_plain", path_nested_plain, 0),
    ("live_child", path_live_child, 1),
    ("child_restore", path_child_restore, 1),
]

PATH_IDS = [name for name, _, _ in PATHS]


async def run_path(path_name: str, cls: type, boundary: LiveSession) -> Outcome:
    adapter = next(fn for name, fn, _ in PATHS if name == path_name)
    return await adapter(cls, boundary)


def component_for(path_name: str, classes: tuple[type, type]) -> type:
    index = next(i for name, _, i in PATHS if name == path_name)
    return classes[index]


# --- the invariants ---------------------------------------------------------------------------


REFUSAL_BY_NAME = {r.name: r for r in REFUSALS}


async def refused(path: str, refusal: Refusal) -> Outcome:
    """Run ``path`` with a component that this ``refusal`` keeps out."""
    boundary = declare_boundary(refusal.session_outcome)
    return await run_path(path, component_for(path, refusal.classes), boundary)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATH_IDS)
@pytest.mark.parametrize("refusal", REFUSALS, ids=[r.name for r in REFUSALS])
class TestARefusalIsTotal:
    """Whatever refuses a component, and wherever it is built, six things hold.

    They are separate tests rather than six asserts in one because they fail for
    different reasons and a reader deserves to know which one gave way.
    """

    async def test_no_markup_reaches_the_client(self, path, refusal):
        outcome = await refused(path, refusal)

        assert SECRET not in outcome.payload

    async def test_no_signed_state_reaches_the_client(self, path, refusal):
        """Markup is the visible half. The state is the half that comes back as a join."""
        outcome = await refused(path, refusal)

        assert outcome.root_element_marker not in outcome.payload

    async def test_it_is_not_in_the_repository(self, path, refusal):
        outcome = await refused(path, refusal)

        if outcome.repo is None:
            pytest.skip("this path has no repository to be left in")
        assert outcome.repo.get(outcome.component_id) is None

    async def test_events_addressed_to_it_do_nothing(self, path, refusal):
        """``component_remove()`` speaks to the client. The repository has to forget."""
        outcome = await refused(path, refusal)

        if outcome.repo is None:
            pytest.skip("this path has no repository to dispatch into")
        assert await outcome.repo.dispatch_event(outcome.component_id, "bump", (), {}) is None

    async def test_no_lifecycle_runs_ahead_of_the_verdict(self, path, refusal):
        """``joined()`` is where a component starts work: subscriptions, queries, uploads.

        Cleaning the markup up afterwards does not undo any of that, so a refused
        component's ``joined()`` must never run at all.
        """
        outcome = await refused(path, refusal)

        assert "joined" not in outcome.calls

    async def test_the_failure_is_carried_rather_than_swallowed(self, path, refusal):
        """A refusal is a verdict; a failure is a verdict *and* a traceback.

        An implementation that turned the exception into a quiet "no" would keep
        every other invariant here and lose the only signal that something is
        broken rather than forbidden.
        """
        outcome = await refused(path, refusal)

        if refusal.runs_hooks:
            assert outcome.calls, "the hook that decides must have been reached"
        if refusal.raises and path in PATHS_THAT_PROPAGATE:
            assert isinstance(outcome.raised, RuntimeError)
        if not refusal.raises:
            assert outcome.raised is None, "an ordinary refusal is not an error"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATH_IDS)
class TestBelongingIsSettledBeforeAnythingRuns:
    """A stronger statement than "a component that does not belong is refused".

    ``_live_sessions`` is answered before anything is awaited, so a component on
    the wrong page never reaches the page's hooks either. That matters because
    hooks are where tracking, audit entries and subscriptions get written: a
    component that was never allowed here should leave no trace that it was tried.
    """

    async def test_no_hook_runs_for_a_component_that_does_not_belong(self, path):
        outcome = await refused(path, REFUSAL_BY_NAME["declaration"])

        assert outcome.calls == []

    async def test_the_pages_own_hook_runs_for_one_that_does(self, path, boundary):
        outcome = await run_path(path, component_for(path, ADMITTED), boundary)

        assert "session" in outcome.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATH_IDS)
class TestAnAdmissionIsWhole:
    """The control. Without it, "refuse everything" passes every test above."""

    async def test_the_component_renders(self, path, boundary):
        outcome = await run_path(path, component_for(path, ADMITTED), boundary)

        assert outcome.raised is None
        assert SECRET in outcome.payload
        assert outcome.root_element_marker in outcome.payload, "and with the state that lets it re-join"

    async def test_it_is_an_event_target(self, path, boundary):
        outcome = await run_path(path, component_for(path, ADMITTED), boundary)

        if outcome.repo is None:
            pytest.skip("this path has no repository")
        component = outcome.repo.get(outcome.component_id)
        assert component is not None
        await outcome.repo.dispatch_event(outcome.component_id, "bump", (), {})
        assert component.note == "bumped"

    async def test_the_session_hooks_run_before_the_components_own(self, path, boundary):
        """The page's policy is a layer under the component's, not beside it."""
        outcome = await run_path(path, component_for(path, ADMITTED), boundary)

        assert outcome.calls[:2] == ["session", "component"]

    async def test_lifecycle_runs_once_and_only_after_the_hooks(self, path, boundary):
        """``joined()`` is the component's own start, so it comes last, and once."""
        outcome = await run_path(path, component_for(path, ADMITTED), boundary)

        assert outcome.calls.count("joined") <= 1, "a second joined() would repeat its side effects"
        if "joined" in outcome.calls:
            assert outcome.calls[-1] == "joined"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATH_IDS)
class TestTheBoundaryIsWhatDecides:
    """The same class, the same code, a different page: a different answer.

    Without this pair, ``_live_sessions`` could be implemented as "always refuse"
    and every refusal test above would still pass.
    """

    async def test_a_declared_component_is_refused_outside_its_session(self, path, boundary):
        outcome = await run_path(path, component_for(path, (CxElsewhere, CxLiveElsewhere)), boundary)

        assert SECRET not in outcome.payload

    async def test_the_same_component_is_admitted_inside_its_session(self, path):
        live_session(BOUNDARY, on_mount=[hook("session")])
        its_own = live_session(ELSEWHERE, on_mount=[hook("session")])

        outcome = await run_path(path, component_for(path, (CxElsewhere, CxLiveElsewhere)), its_own)

        assert SECRET in outcome.payload


# --- the boundary is opt-in -------------------------------------------------------------------


@pytest.mark.asyncio
class TestAProjectWithoutBoundariesIsUntouched:
    """Nothing here may cost a project that has not declared a live_session.

    Not a performance note: it is the compatibility contract. Every one of these
    would be a behaviour change shipped to somebody who did not ask for one.
    """

    async def test_the_signed_state_carries_no_boundary_and_no_login(self):
        payload = unsign_envelope(signed(CxOk, page=None, id="target"), "CxOk")

        assert payload.live_session == ""
        assert payload.auth is None, "binding a public page to a login would reload it on every login"

    async def test_the_connection_subscribes_to_no_authentication_topic(self):
        consumer, outbound = make_consumer()

        await consumer.command_join("CxOk", signed(CxOk, page=None, id="target"))

        assert [topic for topic in outbound.subscribed if topic.startswith(AUTH_TOPIC_PREFIX)] == [], (
            "an unrelated logout must not close a public page's socket"
        )

    async def test_the_connection_does_not_reread_the_session(self, monkeypatch):
        """One backend read per connection is cheap; one for every page is not free."""
        reads: list[str] = []

        async def counted(self) -> bool:
            reads.append("read")
            return True

        monkeypatch.setattr(WireviewConsumer, "_reload_session", counted)
        consumer, _ = make_consumer()

        await consumer.command_join("CxOk", signed(CxOk, page=None, id="target"))

        assert reads == []

    async def test_an_ordinary_nested_component_crosses_no_bridge(self, monkeypatch):
        """A component with no hooks on a page with no boundary has nothing to await."""
        from wireview.templatetags import wireview as tags

        crossings: list[t.Any] = []
        original = tags.async_to_sync
        monkeypatch.setattr(tags, "async_to_sync", lambda fn: crossings.append(fn) or original(fn))

        plain = _component("CxPlainNested", Component)
        repo = ComponentRepository(is_live=True, live_session=None)
        Template("{% load wireview %}{% component 'CxPlainNested' id='target' %}").render(
            Context({"wireview_repository": repo})
        )

        assert crossings == []
        assert repo.get("target") is not None and isinstance(repo.get("target"), plain)


# --- the authentication generation --------------------------------------------------------------


class TestTheGenerationMovesExactlyWhenItShould:
    """A fingerprint that moves too often reloads working pages; one that moves too
    rarely keeps a retired login alive. Both directions are the contract."""

    def _fingerprint(self, user, **session) -> str:
        return auth_fingerprint(user, session)

    def test_it_moves_between_anonymous_and_logged_in(self, user):
        assert self._fingerprint(AnonymousUser()) != self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1"})

    def test_it_moves_between_two_logins_by_the_same_user(self, user):
        assert self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1"}) != self._fingerprint(
            user, **{AUTH_GENERATION_KEY: "g2"}
        )

    def test_it_moves_when_the_password_changes(self, user):
        before = self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1", "_auth_user_hash": "a"})
        after = self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1", "_auth_user_hash": "b"})

        assert before != after

    def test_it_moves_between_two_users(self, user):
        other = get_user_model().objects.create_user(f"cx-other-{uuid4().hex[:8]}", password="x")

        assert self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1"}) != self._fingerprint(
            other, **{AUTH_GENERATION_KEY: "g1"}
        )

    def test_it_does_not_move_when_unrelated_session_data_does(self, user):
        """The signed-cookie backend's session_key is the whole cookie, so it moves
        on every write. A generation built on it would reload pages for nothing."""
        before = self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1", "cart": [1]})
        after = self._fingerprint(user, **{AUTH_GENERATION_KEY: "g1", "cart": [1, 2]})

        assert before == after

    def test_it_does_not_move_when_nothing_does(self, user):
        session = {AUTH_GENERATION_KEY: "g1", "_auth_user_hash": "a"}

        assert auth_fingerprint(user, session) == auth_fingerprint(user, session)

    def test_the_topic_follows_the_generation(self, user):
        first = auth_topic(auth_fingerprint(user, {AUTH_GENERATION_KEY: "g1"}))
        second = auth_topic(auth_fingerprint(user, {AUTH_GENERATION_KEY: "g2"}))

        assert first != second


# --- one boundary per page, and per connection ----------------------------------------------------


@pytest.mark.asyncio
class TestOneBoundaryPerConnection:
    async def test_the_first_join_settles_it(self, boundary):
        consumer, _ = make_consumer(boundary=boundary)

        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))

        assert consumer.live_session_name == BOUNDARY

    async def test_a_later_join_naming_another_boundary_is_refused(self, boundary):
        other = live_session("cx-other")
        consumer, outbound = make_consumer(boundary=boundary)
        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))
        outbound.commands.clear()

        await consumer.command_join("CxOk", signed(CxOk, page=other, id="second"))

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("second") is None

    async def test_a_later_join_naming_no_boundary_is_refused(self, boundary):
        """The interesting direction: a public page's *valid* token on an admitted socket."""
        consumer, outbound = make_consumer(boundary=boundary)
        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))
        outbound.commands.clear()

        await consumer.command_join("CxOk", signed(CxOk, page=None, id="second"))

        assert outbound.kinds() == ["reload"]

    async def test_a_second_join_in_the_same_boundary_is_fine(self, boundary):
        """The control: a page with two root components is one boundary, not two."""
        consumer, outbound = make_consumer(boundary=boundary)
        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))
        outbound.commands.clear()

        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="second"))

        assert outbound.kinds() == ["render"]


# --- no client command reaches a refused component ---------------------------------------------


#: Every consumer command a client can address to a component id. The point of
#: naming them all is that ``component_remove()`` only tells the browser to drop
#: an element -- if the repository still holds the instance, every one of these is
#: a way back in, and testing ``user_event`` alone would say nothing about the rest.
UPLOAD_ENTRY = {"ref": "ref-1", "name": "note.txt", "size": 12, "type": "text/plain"}

CLIENT_COMMANDS = {
    "user_event": lambda c, cid: c.command_user_event(cid, "bump", {}, {}),
    # A ref, so the consumer has somewhere to send the hook's answer.
    "hook_event": lambda c, cid: c.command_hook_event(cid, "hook-1", "ping", {}, ref="r1"),
    # A real entry against a real upload config, so an admitted component answers.
    "upload_register": lambda c, cid: c.command_upload_register(cid, "files", [dict(UPLOAD_ENTRY)]),
    "upload_cancel": lambda c, cid: c.command_upload_cancel(cid, "files", "ref-1"),
    "upload_complete": lambda c, cid: c.command_upload_complete(cid, "files", "ref-1"),
    "leave": lambda c, cid: c.command_leave(cid),
}


#: What each command visibly does when it lands on a component that is really there.
#: Some of them answer the client, some only change the server; both count, and a
#: command with no observable effect at all would make its own control vacuous.
OBSERVABLE = {
    "user_event": lambda c, out, comp: comp.note == "bumped",
    "hook_event": lambda c, out, comp: out.commands != [],
    "upload_register": lambda c, out, comp: out.commands != [],
    # Cancelling does not change what the component renders, so the effect to look
    # for is in the registry rather than in a frame.
    "upload_cancel": lambda c, out, comp: (
        getattr(comp._upload_registry.get_entry("files", "ref-1"), "status", None) is None
        or comp._upload_registry.get_entry("files", "ref-1").status.value == "cancelled"
    ),
    "upload_complete": lambda c, out, comp: out.commands != [],
    "leave": lambda c, out, comp: c.repo.get("target") is None,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", list(CLIENT_COMMANDS), ids=list(CLIENT_COMMANDS))
class TestARefusedIdAnswersNothing:
    async def test_the_command_changes_nothing_and_sends_nothing(self, command, boundary):
        consumer, outbound = make_consumer(boundary=boundary)
        await consumer.command_join("CxHalts", signed(CxHalts, page=boundary, id="target"))
        outbound.commands.clear()

        await CLIENT_COMMANDS[command](consumer, "target")

        assert outbound.commands == []

    async def test_the_control_reaches_an_admitted_component(self, command, boundary):
        """The same command, on the same id, when the component was admitted.

        It has to be the *same call*: asserting only that an admitted component
        exists would leave "ignore every command" indistinguishable from "refuse
        this one", which is the whole question.
        """
        consumer, outbound = make_consumer(boundary=boundary)
        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))
        component = consumer.repo.get("target")
        assert component is not None
        if command in {"upload_cancel", "upload_complete"}:
            # They act on an entry, so there has to be one.
            await consumer.command_upload_register("target", "files", [dict(UPLOAD_ENTRY)])
        outbound.commands.clear()

        await CLIENT_COMMANDS[command](consumer, "target")

        assert OBSERVABLE[command](consumer, outbound, component), (
            f"{command} did nothing for an admitted component, so its refusal proves nothing"
        )


# --- a refusal does not wear off ----------------------------------------------------------------


@pytest.mark.asyncio
class TestARefusalIsSticky:
    async def test_mounting_the_same_instance_again_answers_the_same_way(self, boundary):
        """``_mount`` is idempotent, and its answer has to be too.

        The flag that says "already mounted" is set before the hooks run, so an
        implementation that only remembered *that* it ran would report a refused
        instance as admitted on the second call.
        """
        component = CxHalts(user=AnonymousUser(), wire=WireviewMeta(params={}, live_session=boundary), id="target")

        assert await component._mount({}, {}) is False
        assert await component._mount({}, {}) is False

    async def test_a_crashed_mount_does_not_report_success_afterwards(self, boundary):
        component = CxCrashes(user=AnonymousUser(), wire=WireviewMeta(params={}, live_session=boundary), id="target")

        with pytest.raises(RuntimeError):
            await component._mount({}, {})
        assert await component._mount({}, {}) is False

    async def test_an_admitted_mount_stays_admitted(self, boundary):
        component = CxOk(user=AnonymousUser(), wire=WireviewMeta(params={}, live_session=boundary), id="target")

        assert await component._mount({}, {}) is True
        assert await component._mount({}, {}) is True
        assert calls_for("target") == ["session", "component"], "and the hooks ran once"


# --- the envelope binds three things, and all three are checked ------------------------------------


@pytest.mark.asyncio
class TestTheEnvelopeBindsClassBoundaryAndLogin:
    """Each field closes one substitution. Dropping any one of them is a test that
    should fail, so each is checked with the other two correct."""

    async def test_a_token_for_another_class_is_refused(self, boundary):
        consumer, outbound = make_consumer(boundary=boundary)
        token = signed(CxOk, page=boundary, id="target")

        await consumer.command_join("CxHalts", token)

        assert outbound.kinds() == ["reload"]

    async def test_the_boundary_comes_from_the_envelope_and_nowhere_else(self, boundary):
        """A first join adopts the boundary its own token names, and only that.

        Which is the correct answer, not a gap: a token naming ``ELSEWHERE``
        could only have been issued by an ``ELSEWHERE`` page, and the user still
        has to pass that boundary's predicate. What must not happen is the name
        arriving from anywhere the client could set separately -- that is the
        substitution the envelope exists to prevent. Mixing two on one socket is
        refused (see ``TestOneBoundaryPerConnection``).
        """
        elsewhere = live_session_module.get_live_session(ELSEWHERE)
        consumer, outbound = make_consumer(boundary=boundary)

        await consumer.command_join("CxOk", signed(CxOk, page=elsewhere, id="target"))

        assert consumer.live_session_name == ELSEWHERE
        assert consumer.repo.live_session is elsewhere

    async def test_a_boundary_whose_declaration_is_gone_is_refused(self, boundary):
        """A renamed or removed session reloads rather than falling back to "no policy"."""
        token = signed(CxOk, page=boundary, id="target")
        live_session_module._REGISTRY.pop(BOUNDARY)
        consumer, outbound = make_consumer(boundary=boundary)

        await consumer.command_join("CxOk", token)

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("target") is None

    async def test_a_token_from_another_login_is_refused(self, boundary, user):
        issued_under = {AUTH_GENERATION_KEY: "g1"}
        token = signed(CxOk, page=boundary, user=user, session=issued_under, id="target")
        consumer, outbound = make_consumer(boundary=boundary, user=user, session={AUTH_GENERATION_KEY: "g2"})

        await consumer.command_join("CxOk", token)

        assert outbound.kinds() == ["reload"]

    async def test_all_three_matching_is_admitted(self, boundary, user):
        """The control for the three above."""
        session = {AUTH_GENERATION_KEY: "g1"}
        token = signed(CxOk, page=boundary, user=user, session=session, id="target")
        consumer, outbound = make_consumer(boundary=boundary, user=user, session=session)

        await consumer.command_join("CxOk", token)

        assert outbound.kinds() == ["render"]


# --- the session re-read ----------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEnteringABoundaryRereadsTheSession:
    async def test_it_happens_once_per_connection_not_once_per_join(self, boundary, monkeypatch):
        reads: list[str] = []

        async def counted(self) -> bool:
            reads.append("read")
            return True

        monkeypatch.setattr(WireviewConsumer, "_reload_session", counted)
        consumer, _ = make_consumer(boundary=boundary)

        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="target"))
        await consumer.command_join("CxOk", signed(CxOk, page=boundary, id="second"))

        assert reads == ["read"]

    async def test_an_unreachable_session_backend_refuses_rather_than_admits(self, boundary):
        """Fail closed. A backend that cannot answer is not evidence that the login stands.

        The engine really raises, rather than the method being replaced: the
        branch under test is the ``except`` inside ``_reload_session``, and
        stubbing the method that contains it would test the stub.
        """
        # A key Django will accept: it validates the shape and quietly drops one it
        # does not like, and a dropped key means the backend is never asked at all.
        session = SessionView({AUTH_GENERATION_KEY: "g1"}, session_key="0123456789abcdef0123456789abcdef")
        token = signed(CxOk, page=boundary, session=session, id="target")
        consumer, outbound = make_consumer(boundary=boundary, session=session)

        with override_settings(SESSION_ENGINE="testproj.broken_sessions"):
            await consumer.command_join("CxOk", token)

        assert outbound.kinds() == ["reload"]
        assert consumer.repo.get("target") is None

    async def test_a_connection_refused_by_the_re_read_cannot_simply_ask_again(self, boundary):
        """The bookkeeping is the verdict, not the subscription.

        Keying "have we checked" on "have we subscribed" let a refused connection
        send the same join a second time and skip the check, because the first
        attempt had already subscribed.
        """
        with override_settings(SESSION_ENGINE=CACHE_SESSIONS):
            store = CacheSessionStore()
            store[AUTH_GENERATION_KEY] = "g1"
            store.save()
            key = str(store.session_key)
            token = signed(CxOk, page=boundary, session=CacheSessionStore(key), id="target")
            consumer, outbound = make_consumer(boundary=boundary, session=CacheSessionStore(key))
            CacheSessionStore(key).flush()

            await consumer.command_join("CxOk", token)
            first = outbound.kinds()
            outbound.commands.clear()
            await consumer.command_join("CxOk", token)

        assert first == ["reload"]
        assert outbound.kinds() == ["reload"], "asking again is not a second chance"
        assert consumer.repo.get("target") is None


# --- a child's stored state is restored, or dropped, and never confused -----------------------


@pytest.mark.asyncio
class TestAChildsStoredStateIsRestoredOrDropped:
    """The restore path has two jobs and they pull in opposite directions.

    A child's own state has to survive a reconnect -- that is what the map is for
    -- and a child's state from somewhere else has to be thrown away. An
    implementation that always restores keeps the boundary open; one that never
    restores looks safe and quietly loses what the user typed. Only asserting
    that the *child exists* afterwards cannot tell the two apart, because the
    parent's template rebuilds it either way.
    """

    async def _join_with_child_state(self, boundary: LiveSession, child_token: str):
        consumer, outbound = make_consumer(boundary=boundary)
        parent_class = _parent("CxRestoreProbeParent", "cx/live.html")
        with _template_naming("cx/live.html", CxLiveOk):
            await consumer.command_join(
                parent_class.__name__,
                signed(parent_class, page=boundary, id="parent"),
                children={"target": ("CxLiveOk", child_token)},
            )
        return consumer, outbound

    async def test_its_own_state_comes_back(self, boundary):
        token = signed(CxLiveOk, page=boundary, id="target", note=RESTORED)

        consumer, _ = await self._join_with_child_state(boundary, token)

        child = consumer.repo.get("target")
        assert child is not None and child.note == RESTORED

    async def test_state_from_another_boundary_is_dropped(self, boundary):
        """The child is still built -- the parent's template names it -- from props."""
        elsewhere = live_session_module.get_live_session(ELSEWHERE)
        token = signed(CxLiveOk, page=elsewhere, id="target", note=RESTORED)

        consumer, _ = await self._join_with_child_state(boundary, token)

        child = consumer.repo.get("target")
        assert child is not None, "the parent named it, so it exists"
        assert child.note != RESTORED, "but not with state from another page"

    async def test_state_from_another_login_is_dropped(self, boundary, user):
        token = signed(
            CxLiveOk, page=boundary, user=user, session={AUTH_GENERATION_KEY: "other"}, id="target", note=RESTORED
        )

        consumer, _ = await self._join_with_child_state(boundary, token)

        child = consumer.repo.get("target")
        assert child is not None
        assert child.note != RESTORED


# --- a component that was admitted and then is not ------------------------------------------------


@pytest.mark.asyncio
class TestARejoinCanTakeAdmissionAway:
    """The rejoin row of the matrix starts from nothing, which is the easy case.

    Boost navigation re-joins an id that is already live, and the consumer retires
    the old instance before mounting the new one. If the new mount is refused, what
    matters is that the *old* instance went with it -- otherwise a page whose policy
    now says no keeps a working component from when it said yes.
    """

    async def test_the_previous_instance_goes_with_the_refusal(self, boundary):
        verdicts = iter([{"cont": True}, {"halt": True}])

        class OnceThenNo:
            @staticmethod
            async def on_mount(component, params, session):
                CALLS.append((component.id, "gate"))
                return next(verdicts)

        cls = _component("CxTurnsAway", Component, _on_mount=[OnceThenNo], bump=_bump, joined=_joined)
        token = signed(cls, page=boundary, id="target")
        consumer, outbound = make_consumer(boundary=boundary)

        await consumer.command_join(cls.__name__, token)
        first = consumer.repo.get("target")
        assert first is not None, "the first join was admitted"
        outbound.commands.clear()

        await consumer.command_join(cls.__name__, token)

        assert consumer.repo.get("target") is None, "the instance from when the answer was yes is gone"
        assert SECRET not in str(outbound.commands)
        assert await consumer.repo.dispatch_event("target", "bump", (), {}) is None

    async def test_a_second_yes_keeps_it_working(self, boundary):
        """The control: an ordinary re-join replaces the instance and it still works."""
        consumer, outbound = make_consumer(boundary=boundary)
        token = signed(CxOk, page=boundary, id="target")

        await consumer.command_join("CxOk", token)
        await consumer.command_join("CxOk", token)

        component = consumer.repo.get("target")
        assert component is not None
        await consumer.repo.dispatch_event("target", "bump", (), {})
        assert component.note == "bumped"


# --- the topic a connection listens on is the topic a logout speaks to ------------------------


class TestTheInvalidationReachesTheConnection:
    """Subscribing and publishing are two halves of one claim.

    Tested apart, each passes on its own terms while the message goes somewhere
    nobody is on -- which is what happened: the re-login publish named the
    generation with its nonce alone, and the sockets it meant to retire had
    subscribed under a fingerprint taken from a whole session. Both tests were
    green. So the topic is compared, once, end to end.

    Synchronous, and the join is run to completion first, because that is the
    real order: a socket exists, and then an HTTP request logs the user out. The
    publish crosses from sync to async, which needs a thread with no loop running
    on it -- the one Django gives a view.
    """

    @staticmethod
    def _recording_broker(published: list[str]):
        class RecordingBroker:
            async def publish(self, topic, message):
                published.append(topic)

            async def send_to_session(self, session_id, message): ...

        return RecordingBroker()

    def _connect(self, boundary, user, session) -> list[str]:
        """Open a connection on ``session`` and return the topics it subscribed to."""
        consumer, outbound = make_consumer(boundary=boundary, user=user, session=dict(session.items()))
        asyncio.run(
            consumer.command_join(
                "CxOk",
                signed(CxOk, page=boundary, user=user, session=dict(session.items()), id="target"),
            )
        )
        return [topic for topic in outbound.subscribed if topic.startswith(AUTH_TOPIC_PREFIX)]

    def test_a_logout_publishes_where_the_connection_is_listening(self, boundary, user):
        from django.contrib.auth import login, logout
        from django.contrib.sessions.backends.db import SessionStore

        from wireview.core.transport import set_broker

        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        listening_on = self._connect(boundary, user, request.session)

        published: list[str] = []
        set_broker(self._recording_broker(published))
        try:
            logout(request)
        finally:
            set_broker(None)

        assert listening_on, "the connection subscribed to something"
        assert published == listening_on, "and that is where the logout spoke"

    def test_a_re_login_publishes_where_the_connection_is_listening(self, boundary, user):
        """The half that was broken: it named the generation with less than the fingerprint."""
        from django.contrib.auth import login
        from django.contrib.sessions.backends.db import SessionStore

        from wireview.core.transport import set_broker

        request = RequestFactory().get("/")
        request.session = SessionStore()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        listening_on = self._connect(boundary, user, request.session)

        published: list[str] = []
        set_broker(self._recording_broker(published))
        try:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        finally:
            set_broker(None)

        assert listening_on
        assert published == listening_on


# --- a page has one boundary, and says so in three places -------------------------------------


class TestAPageAgreesWithItself:
    """The connection side of "one boundary" is well covered; the page side was not.

    Three things carry the name -- the request the view stamped, the meta the
    header rendered, and the envelope inside every component's ``data-state`` --
    and they are produced by different code. A page whose meta says one thing and
    whose tokens say another sends the browser to the wrong decision about
    navigation while the server admits the tokens anyway.
    """

    @pytest.fixture
    def page(self, boundary):
        class Request:
            META = {"QUERY_STRING": ""}
            session: dict = {}
            wireview_live_session = BOUNDARY

        html = Template(
            "{% load wireview %}{% wireview_header %}{% component 'CxOk' id='one' %}{% component 'CxOk' id='two' %}"
        ).render(Context({"request": Request()}))
        return html

    def test_the_header_meta_names_the_request_boundary(self, page):
        assert f'<meta name="wireview-live-session" content="{BOUNDARY}" />' in page

    def test_every_component_on_it_signs_the_same_boundary(self, page):
        states = re.findall(r'data-state="([^"]+)"', page)

        assert len(states) == 2, "both components rendered"
        for state in states:
            assert unsign_envelope(unescape(state), "CxOk").live_session == BOUNDARY

    def test_a_page_outside_a_boundary_agrees_the_other_way(self):
        html = Template("{% load wireview %}{% wireview_header %}{% component 'CxOk' id='one' %}").render(Context({}))
        states = re.findall(r'data-state="([^"]+)"', html)

        assert '<meta name="wireview-live-session" content="" />' in html
        assert states and unsign_envelope(unescape(states[0]), "CxOk").live_session == ""


# --- a child that appears later, and a child inside a slot ---------------------------------------


CxSlotHost = _component("CxSlotHost", Component, _template_name="cx/slothost.html")


@pytest.mark.asyncio
class TestAChildThatAppearsAfterTheJoin:
    """The join is not the only moment a component can first exist.

    An event, a params change or a broadcast re-renders an admitted parent, and
    the boundary has to be applied to whatever that render names for the first
    time. A gate that ran only on the join would be a gate on the first frame.
    """

    async def _parent_that_reveals(self, boundary, cls, trigger):
        parent_class = _component(
            f"CxReveal{cls.__name__}{trigger}",
            Component,
            _template_name="cx/later.html",
            show=False,
            reveal=_reveal,
            params_changed=_reveal_on_params,
        )
        consumer, outbound = make_consumer(boundary=boundary)
        with _template_naming("cx/later.html", cls):
            parent = await consumer.repo.join(parent_class.__name__, {"id": "parent"})
            await consumer.send_render(parent)
            assert consumer.repo.get("target") is None, "not there yet"
            outbound.commands.clear()
            CALLS.clear()

            if trigger == "event":
                await consumer.repo.dispatch_event("parent", "reveal", (), {})
            else:
                await parent.params_changed({"show": "1"}, "?show=1")
            await consumer.send_render(parent)
        return consumer, outbound

    @pytest.mark.parametrize("trigger", ["event", "params"])
    async def test_a_refused_child_revealed_later_still_ships_nothing(self, boundary, trigger):
        consumer, outbound = await self._parent_that_reveals(boundary, CxElsewhere, trigger)

        assert SECRET not in str(outbound.commands)
        assert consumer.repo.get("target") is None

    @pytest.mark.parametrize("trigger", ["event", "params"])
    async def test_an_admitted_child_revealed_later_does_appear(self, boundary, trigger):
        """The control: the parent really does reveal it."""
        consumer, outbound = await self._parent_that_reveals(boundary, CxOk, trigger)

        assert SECRET in str(outbound.commands)
        assert consumer.repo.get("target") is not None


@pytest.mark.asyncio
class TestAChildInsideASlot:
    """Slot content renders during the parent's pass, through another component.

    The repository and its boundary have to travel with it, or a component put
    inside a slot is mounted on a page with no policy while sitting on one.
    """

    async def _render_slotted(self, boundary, cls):
        parent_class = _component(f"CxSlotParentOf{cls.__name__}", Component, _template_name="cx/slotted.html")
        consumer, outbound = make_consumer(boundary=boundary)
        with _template_naming("cx/slotted.html", cls):
            parent = await consumer.repo.join(parent_class.__name__, {"id": "parent"})
            await consumer.send_render(parent)
        return consumer, outbound

    async def test_a_refused_child_in_a_slot_ships_nothing(self, boundary):
        consumer, outbound = await self._render_slotted(boundary, CxElsewhere)

        assert SECRET not in str(outbound.commands)
        assert consumer.repo.get("target") is None

    async def test_an_admitted_child_in_a_slot_runs_the_pages_hooks(self, boundary):
        consumer, outbound = await self._render_slotted(boundary, CxOk)

        assert SECRET in str(outbound.commands)
        assert calls_for("target")[:1] == ["session"], "the page's policy reached into the slot"
