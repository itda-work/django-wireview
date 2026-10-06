"""``Meta.shared_render``: one render for the connections handling the same broadcast (#176, stage 2).

docs/design/broadcast-fanout.md §4-A and §7 say what is shared and what is
not. In short, these hold:

- the render is shared only within one message, and only between instances of
  the same class with the same id, fields, language and time zone
- a field that holds a model instance or a QuerySet keeps the render the
  connection's own: the same pk may carry other attributes on another connection
- each connection still diffs against its own page, and its ``data-state`` is
  its own token: what a page boundary signs differs between viewers
- the frames are the bytes an unshared render sends
- a class out of scope does not share, and ``wireview.W019`` says why
- with ``VERIFY_SHARED_RENDER`` a render that reads the viewer raises, and a
  render taken from another connection is rendered again and compared
- the token is signed off the event loop, by the leader and the takers alike
- what the process keeps of a message goes after a while, and within a size
"""

import asyncio
import json
import re
import typing as t
from collections import Counter
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from django.utils import translation
from pydantic import Field, computed_field
from testproj.bookmarks.models import Bookmark
from testproj.outbound import RecordingOutbound
from testproj.waiting import eventually
from testproj.wireview_setting import set_wireview

from wireview import Component, LiveComponent, live_session
from wireview import testing as wireview_testing
from wireview.checks import check_shared_render
from wireview.core import live_session as live_session_module
from wireview.core import shared_render
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, Rendered
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

BOARD = (
    "{% load wireview %}<section {% tag_header %}><h1>{{ headline }}</h1><p>page {{ page }}</p>"
    "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
    "{% if page %}<b>{{ page }}</b>{% endif %}</section>"
)
TEMPLATES = {
    "sh/board.html": BOARD,
    "sh/user.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} for {{ this.user.username }}</p>",
    "sh/user_property.html": "{% load wireview %}<p {% tag_header %}>{{ greeting }}</p>",
    "sh/request.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ request.path }}</p>",
    "sh/csrf.html": "{% load wireview %}<form {% tag_header %}>{% csrf_token %}{{ headline }}</form>",
    "sh/messages.html": "{% load wireview %}<p {% tag_header %}>{% for m in messages %}{{ m }}{% endfor %}</p>",
    "sh/viewer.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} seen by {{ viewer }}</p>",
    "sh/temp.html": "{% load wireview %}<p {% tag_header %}>{% for r in rows %}{{ r }}{% endfor %}</p>",
    "sh/nested.html": '{% load wireview %}<div {% tag_header %}>{% component "ShInner" id="inner" %}</div>',
    "sh/inner.html": "{% load wireview %}<span {% tag_header %}>{{ headline }}</span>",
    "sh/plain.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}</p>",
    "sh/child.html": "{% load wireview %}<i {% live_tag_header %}>{{ headline }}</i>",
    "sh/include.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/partial.html" %}</p>',
    "sh/partial.html": "<i>{{ request.path }}</i>",
    "sh/session_property.html": "{% load wireview %}<p {% tag_header %}>{{ visits }}</p>",
    "sh/twice.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}</p><p {% tag_header %}></p>",
    "sh/doc.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ doc.title }}</p>",
    "sh/marks.html": "{% load wireview %}<p {% tag_header %}>{% for b in marks %}{{ b.title }}{% endfor %}</p>",
    "sh/counted.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ rows }}</p>",
    "sh/hidden.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ viewer }}</p>",
    "sh/if_include.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/if_part.html" %}</p>',
    "sh/if_part.html": "{% if request.user.is_staff and headline %}staff{% endif %}",
    "sh/if_property.html": "{% load wireview %}<p {% tag_header %}>{% if staff and headline %}staff{% endif %}</p>",
    "sh/filter_arg.html": "{% load wireview %}<p {% tag_header %}>{{ headline|default:request.path }}</p>",
    "sh/firstof.html": "{% load wireview %}<p {% tag_header %}>{% firstof headline user.username %}</p>",
}

STORE: dict[str, t.Any] = {}
RENDERS: Counter[str] = Counter()
VIEWERS: dict[str, str] = {}


def _reset_store() -> None:
    STORE.clear()
    STORE.update(headline="hello", items=["a", "b", "c"])
    RENDERS.clear()
    VIEWERS.clear()


class _Headline:
    """Counts the renders of each class: every render reads ``headline`` once."""

    @property
    def headline(self) -> str:
        RENDERS[type(self).__name__] += 1
        if STORE.get("raise_once") == type(self).__name__:
            del STORE["raise_once"]
            raise RuntimeError("the first render of this message fails")
        return STORE["headline"]


class ShBoard(_Headline, Component):
    class Meta:
        template_name = "sh/board.html"
        subscriptions = {"sh-board"}
        shared_render = True

    page: int = 0

    @property
    def items(self) -> list[str]:
        return STORE["items"]

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass

    async def turn(self, to: int):
        self.page = to


class ShPlainBoard(ShBoard):
    """The same board without the declaration: what an unshared render sends."""

    class Meta:
        shared_render = False


class ShUser(_Headline, Component):
    class Meta:
        template_name = "sh/user.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShUserProperty(_Headline, Component):
    class Meta:
        template_name = "sh/user_property.html"
        shared_render = True

    @property
    def greeting(self) -> str:
        return f"hi {self.user}"


class ShSessionProperty(_Headline, Component):
    class Meta:
        template_name = "sh/session_property.html"
        shared_render = True

    @property
    def visits(self) -> int:
        return self.session.get("visits", 0)


class ShInclude(_Headline, Component):
    """Reads the request in an included template, which the system check does not open."""

    class Meta:
        template_name = "sh/include.html"
        shared_render = True


class ShRequest(_Headline, Component):
    class Meta:
        template_name = "sh/request.html"
        shared_render = True


class ShCsrf(_Headline, Component):
    class Meta:
        template_name = "sh/csrf.html"
        shared_render = True


class ShMessages(_Headline, Component):
    """Has a field named like a context processor's: the field wins, nothing raises."""

    class Meta:
        template_name = "sh/messages.html"
        shared_render = True

    messages: list[str] = ["one", "two"]


class ShViewer(_Headline, Component):
    """Wrongly declared: what it shows depends on the connection, through no watched name."""

    class Meta:
        template_name = "sh/viewer.html"
        subscriptions = {"sh-board"}
        shared_render = True

    @property
    def viewer(self) -> str:
        return VIEWERS.get(self.wire.channel_name or "", "?")

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShTemporary(_Headline, Component):
    class Meta:
        template_name = "sh/temp.html"
        subscriptions = {"sh-board"}
        temporary_assigns = {"rows"}
        shared_render = True

    rows: list[str] = []

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.rows = ["x"]


class ShInner(_Headline, Component):
    class Meta:
        template_name = "sh/inner.html"


class ShNested(_Headline, Component):
    class Meta:
        template_name = "sh/nested.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShChild(_Headline, LiveComponent):
    class Meta:
        template_name = "sh/child.html"
        shared_render = True


class ShBounded(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        live_sessions = {"sh-room"}
        shared_render = True


class ShSlotted(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        slots = {"body": {}}
        shared_render = True


class ShTwice(_Headline, Component):
    class Meta:
        template_name = "sh/twice.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShDoc(_Headline, Component):
    """A row typed as one: out of scope before any render."""

    class Meta:
        template_name = "sh/doc.html"
        subscriptions = {"sh-board"}
        shared_render = True

    doc: Bookmark | None = None

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShAnyDoc(ShDoc):
    """A row in a field whose type does not say so: the render tells."""

    class Meta:
        shared_render = True

    doc: t.Any = None


class ShMarks(_Headline, Component):
    """A QuerySet in a field whose type does not say so: in scope, and its state's JSON queries."""

    class Meta:
        template_name = "sh/marks.html"
        subscriptions = {"sh-board"}
        shared_render = True

    marks: t.Any = None

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShCounted(_Headline, Component):
    """Shared, with a computed field in its state that queries: every token is signed off the loop."""

    class Meta:
        template_name = "sh/counted.html"
        subscriptions = {"sh-board"}
        shared_render = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rows(self) -> int:
        return Bookmark.objects.count()

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShHidden(_Headline, Component):
    """A field pydantic leaves out of its dumps, which the render reads all the same."""

    class Meta:
        template_name = "sh/hidden.html"
        subscriptions = {"sh-board"}
        shared_render = True

    viewer: str = Field("", exclude=True)

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


REDIRECTED: set[str] = set()


class ShLeaving(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        if self.wire.channel_name in REDIRECTED:
            await self.wire.redirect_to("/elsewhere/")


class ShIfInclude(_Headline, Component):
    """Reads the request where ``{% if a and b %}`` swallows what the read raises."""

    class Meta:
        template_name = "sh/if_include.html"
        shared_render = True


class ShIfProperty(_Headline, Component):
    """The same through a property that reads the user and swallows what that raises itself."""

    class Meta:
        template_name = "sh/if_property.html"
        shared_render = True

    @property
    def staff(self) -> bool:
        try:
            return bool(self.user.is_staff)
        except Exception:
            return False


class ShFilterArg(_Headline, Component):
    class Meta:
        template_name = "sh/filter_arg.html"
        shared_render = True


class ShFirstOf(_Headline, Component):
    class Meta:
        template_name = "sh/firstof.html"
        shared_render = True


@pytest.fixture(autouse=True)
def _templates():
    _reset_store()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)],
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                    ],
                },
            }
        ]
    ):
        shared_render._SCOPE.clear()
        yield
    shared_render._SCOPE.clear()


def signed(cls: type[Component], *, id: str = "board", user=None, page_session=None, **fields: t.Any) -> str:
    return sign_state(
        cls(id=id, user=user or AnonymousUser(), wire=WireviewMeta(params={}, live_session=page_session), **fields)
    )


class Page:
    """One browser tab: a session with a component joined, and what it was sent."""

    def __init__(self, session: WireviewSession, outbound: RecordingOutbound, id: str) -> None:
        self.session = session
        self.outbound = outbound
        self.id = id

    @classmethod
    async def open(cls, component: type[Component] = ShBoard, *, user=None, id: str = "board", **fields) -> "Page":
        outbound = RecordingOutbound()
        session = WireviewSession(outbound, user=user or AnonymousUser(), channel_name=f"sh-{uuid4().hex[:8]}")
        await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
        await session.handle_message(
            {
                "command": "join",
                # Off the loop: a state that holds a QuerySet or a computed field queries
                "payload": {"name": component._name, "state": await sync_to_async(signed)(component, id=id, **fields)},
            }
        )
        assert outbound.renders(), f"{component._name} did not join"
        outbound.commands.clear()
        return cls(session, outbound, id)

    @property
    def component(self) -> Component:
        return self.session.repo.get(self.id)

    def frames(self) -> list[str]:
        return [json.dumps(payload, sort_keys=True) for command, payload in self.outbound.commands]

    def html(self) -> str:
        last = self.component.wire._last_rendered
        assert last is not None
        return last.to_html()


async def broadcast(pages: list[Page], message_id: str | None, **kwargs: t.Any) -> None:
    data: dict[str, t.Any] = {"channel": "sh-board", "kwargs": kwargs}
    if message_id is not None:
        data["message_id"] = message_id
    await asyncio.gather(*(page.session.notification(dict(data)) for page in pages))


# -- the option -------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_option_is_off_unless_declared_and_inherited_by_subclasses():
    class ShOff(Component, public=False):
        class Meta:
            template_name = "sh/plain.html"

    class ShOnSub(ShBoard, public=False):
        pass

    assert ShOff._meta.shared_render is False
    assert ShBoard._meta.shared_render is True
    assert ShOnSub._meta.shared_render is True
    assert ShPlainBoard._meta.shared_render is False


# -- sharing ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_connections_handling_one_message_render_once():
    pages = [await Page.open() for _ in range(5)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 1
    for page in pages:
        (render,) = page.outbound.renders()
        assert render["id"] == "board" and render["diff"] == {"1": "news"}
        assert "<h1>news</h1>" in page.html()


@pytest.mark.asyncio
async def test_a_message_without_an_id_renders_on_every_connection():
    """A message from a publisher before #176, or from another process's older code."""
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, None)

    assert RENDERS["ShBoard"] == 3
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)


@pytest.mark.asyncio
async def test_each_message_renders_again():
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()

    STORE["headline"] = "first"
    await broadcast(pages, "m-1")
    STORE["headline"] = "second"
    await broadcast(pages, "m-2")

    assert RENDERS["ShBoard"] == 2
    assert all("<h1>second</h1>" in page.html() for page in pages)


@pytest.mark.asyncio
async def test_other_fields_render_apart():
    pages = [await Page.open(page=n % 2) for n in range(6)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 2, "one render for page 0, one for page 1"
    for page in pages:
        shown = page.component.page  # type: ignore[attr-defined]
        assert f"<p>page {shown}</p>" in page.html()


@pytest.mark.asyncio
async def test_another_id_renders_apart():
    pages = [await Page.open(id="board-a"), await Page.open(id="board-a"), await Page.open(id="board-b")]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 2
    assert [page.outbound.renders()[0]["id"] for page in pages] == ["board-a", "board-a", "board-b"]


@pytest.mark.asyncio
async def test_another_language_renders_apart():
    pages = [await Page.open() for _ in range(4)]
    RENDERS.clear()

    async def handle(page: Page, language: str) -> None:
        with translation.override(language):
            await page.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})

    await asyncio.gather(*(handle(page, ("en", "ko")[n % 2]) for n, page in enumerate(pages)))

    assert RENDERS["ShBoard"] == 2


@pytest.mark.asyncio
async def test_an_undeclared_class_renders_on_every_connection():
    pages = [await Page.open(ShPlainBoard) for _ in range(3)]
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert RENDERS["ShPlainBoard"] == 3


@pytest.mark.asyncio
async def test_a_connection_whose_page_shows_another_render_gets_its_own_diff():
    """Shared is the render, not the diff: each connection diffs against what its page shows."""
    ahead, behind = await Page.open(), await Page.open()
    STORE["headline"] = "one"
    await ahead.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-0"})
    ahead.outbound.commands.clear()
    RENDERS.clear()

    STORE["items"] = ["a", "b", "c", "d"]
    await broadcast([ahead, behind], "m-1")

    assert RENDERS["ShBoard"] == 1
    assert "1" not in ahead.outbound.renders()[0]["diff"], "the headline is already on that page"
    assert behind.outbound.renders()[0]["diff"]["1"] == "one"
    assert _without_state(ahead.html()) == _without_state(behind.html()), "each signs its own state"


@pytest.mark.asyncio
async def test_the_frames_are_the_bytes_an_unshared_render_sends(monkeypatch):
    """Each step of a run, shared on five connections and unshared on five more: the same frames.

    The clock is fixed so the state tokens, which carry a timestamp, are alike too.
    """
    monkeypatch.setattr("time.time", lambda: 1_800_000_000.0)
    shared = [await Page.open(page=n % 2) for n in range(5)]
    plain = [await Page.open(page=n % 2) for n in range(5)]
    monkeypatch.setattr(shared_render, "declared", lambda component: component.wire.channel_name in sharing)
    sharing = {page.session.channel_name for page in shared}

    steps: list[t.Callable[[], None]] = [
        lambda: STORE.update(headline="news"),
        lambda: STORE.update(items=["c", "a"]),
        lambda: None,
        lambda: STORE.update(items=[], headline="<b>&</b>"),
        lambda: STORE.update(items=["x"] * 3),
    ]
    for n, step in enumerate(steps):
        step()
        await broadcast(shared, f"m-{n}")
        await broadcast(plain, f"p-{n}")
        if n == 2:  # a user event in between: the next shared render diffs against it
            for page in (shared[0], plain[0]):
                await page.session.handle_message(
                    {
                        "command": "user_event",
                        "payload": {"id": "board", "command": "turn", "implicit_args": {}, "explicit_args": {"to": 7}},
                    }
                )

    for mine, theirs in zip(shared, plain, strict=True):
        assert mine.frames() == theirs.frames()
        assert mine.html() == theirs.html()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_each_connection_signs_its_own_state(monkeypatch):
    """A page boundary signs who is looking: the shared render holds every connection's own token."""
    saved = dict(live_session_module._REGISTRY)
    room = live_session("sh-room-tokens")
    try:
        users = [await get_user_model().objects.acreate(username=f"sh-{n}-{uuid4().hex[:6]}") for n in range(3)]
        pages = [await Page.open(user=user) for user in users]
        for page in pages:
            page.component.wire.live_session = room
            page.component.wire._state_token = None  # issued again, under the boundary
        RENDERS.clear()
        STORE["headline"] = "news"

        await broadcast(pages, "m-1")

        assert RENDERS["ShBoard"] == 1
        tokens = set()
        for page, user in zip(pages, users, strict=True):
            token = _state_token(page)
            tokens.add(token)
            envelope = unsign_envelope(token, "ShBoard")
            assert envelope.live_session == "sh-room-tokens"
            assert envelope.auth == live_session_module.auth_fingerprint(user, page.component.session)
            assert f'data-state="{token}"' in page.html()
        assert len(tokens) == 3
    finally:
        live_session_module._REGISTRY.clear()
        live_session_module._REGISTRY.update(saved)


def _without_state(html: str) -> str:
    return re.sub(r'data-state="[^"]*"', 'data-state=""', html)


def _state_token(page: Page) -> str:
    html = page.html()
    start = html.index('data-state="') + len('data-state="')
    return html[start : html.index('"', start)]


@pytest.mark.asyncio
async def test_a_refused_component_takes_no_part():
    """What a failed join left is not rendered; the others share as ever."""
    pages = [await Page.open() for _ in range(3)]
    refused = pages[0]
    refused.session.repo.join_failed("board", [])
    assert refused.session.repo.refused("board")
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert refused.outbound.renders() == []
    assert RENDERS["ShBoard"] == 1
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages[1:])


@pytest.mark.asyncio
async def test_when_the_first_render_fails_the_others_render_on_their_own():
    pages = [await Page.open() for _ in range(4)]
    RENDERS.clear()
    STORE.update(headline="news", raise_once="ShBoard")

    await broadcast(pages, "m-1")

    errors = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert len(errors) == 1, "the connection whose render raised is the one that crashed"
    fine = [page for page in pages if page not in errors]
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in fine)
    assert RENDERS["ShBoard"] == 2, "the failed render and one more the others shared"


@pytest.mark.asyncio
async def test_the_store_forgets_a_message_after_a_while(monkeypatch):
    """By a timer: nothing is held once the traffic stops, without a next message to clean up."""
    monkeypatch.setattr(shared_render, "KEEP_SECONDS", 0.05)
    pages = [await Page.open(page=n) for n in range(3)]
    await broadcast(pages, "m-1")
    store = shared_render._store()
    assert list(store.messages) == ["m-1"] and store.renders == 3 and store.size > 0

    await eventually(lambda: not store.messages)
    assert store.renders == 0 and store.size == 0


@pytest.mark.asyncio
async def test_the_store_keeps_renders_within_a_size(monkeypatch):
    """Connections whose fields are all their own share nothing: what they leave is bounded."""
    pages = [await Page.open(page=n) for n in range(4)]
    store = shared_render._store()
    await broadcast(pages, "m-0")
    one = store.size // 4
    monkeypatch.setattr(shared_render, "KEEP_BYTES", one * 10)
    for n in range(1, 6):
        await broadcast(pages, f"m-{n}")
        assert store.size <= one * 10
    assert list(store.messages) == ["m-4", "m-5"], "the oldest go first"
    assert store.renders == 8

    monkeypatch.setattr(shared_render, "KEEP_RENDERS", 5)
    await broadcast(pages, "m-6")
    assert list(store.messages) == ["m-6"] and store.renders == 4


@pytest.mark.asyncio
async def test_a_message_dropped_while_its_render_is_under_way_is_not_counted(monkeypatch):
    pages = [await Page.open() for _ in range(2)]
    store = shared_render._store()
    monkeypatch.setattr(shared_render, "KEEP_SECONDS", 0)  # gone as soon as the loop turns
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)
    assert store.size == 0 and store.renders == 0 and not store.messages


@pytest.mark.unit
def test_a_shared_render_is_never_changed_by_a_connection():
    shared = shared_render.Shared.parse(
        f'<p id="x" data-state="<!--$0-->{shared_render.STATE_SLOT}<!--/$0-->"><!--$B1-->'
        f"<b><!--$2-->x<!--/$2--></b><!--/$B1--></p>"
    )
    assert shared is not None and shared.state_path == (0,)
    one = shared.with_state("token-1")
    two = shared.with_state("token&2")
    assert one.dynamic[0] == "token-1" and two.dynamic[0] == "token&amp;2"
    assert shared.rendered.dynamic[0] == shared_render.STATE_SLOT
    assert one.dynamic[1] is shared.rendered.dynamic[1], "what is not on the path to the token is shared"
    assert one.static is shared.rendered.static


@pytest.mark.unit
def test_a_slot_inside_a_block_is_found_and_one_in_a_loop_is_not_shared():
    inside = shared_render.Shared.parse(
        f'<!--$B0--><p data-state="<!--$1-->{shared_render.STATE_SLOT}<!--/$1-->"><!--$2-->y<!--/$2--></p><!--/$B0-->'
    )
    assert inside is not None and inside.state_path == (0, 0)
    assert isinstance(inside.with_state("t").dynamic[0], Rendered)
    looped = shared_render.Shared.parse(
        f"<!--$C0--><!--$I1--><p><!--$2-->{shared_render.STATE_SLOT}<!--/$2--></p><!--/$I1--><!--/$C0-->"
    )
    assert looped is None


# -- out of scope -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporary_assigns_put_a_class_out_of_scope(caplog):
    pages = [await Page.open(ShTemporary) for _ in range(3)]
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert all("<p" in page.html() and ">x</p>" in page.html() for page in pages)
    assert "ShTemporary declares Meta.shared_render, but its renders are not shared" in caplog.text


@pytest.mark.asyncio
async def test_a_template_that_draws_a_component_renders_on_every_connection():
    pages = [await Page.open(ShNested) for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShNested"] == 3
    assert all("news" in page.html() for page in pages)


@pytest.mark.asyncio
async def test_a_template_that_signs_twice_renders_on_every_connection(caplog):
    """W019 cannot see two tag headers; the render can, and it keeps to its own."""
    caplog.set_level("WARNING", "wireview")
    pages = [await Page.open(ShTwice) for _ in range(3)]
    assert "its render draws another component or holds data-state more than once" in caplog.text
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    # The first join found out and rendered again; the class renders on its own from then on
    assert RENDERS["ShTwice"] == 3
    assert shared_render.STATE_SLOT not in "".join(page.html() for page in pages)
    assert all(page.html().count('data-state="') == 2 for page in pages)


@pytest.mark.unit
def test_the_system_check_names_every_reason():
    found = {message.obj: message for message in check_shared_render(None)}
    assert set(found) >= {ShTemporary, ShNested, ShChild, ShBounded, ShSlotted, ShUser, ShRequest, ShCsrf}
    assert {ShBoard, ShPlainBoard, ShViewer, ShMessages, ShTwice, ShInner}.isdisjoint(found)
    assert all(message.id == "wireview.W019" for message in found.values())
    assert "temporary_assigns" in found[ShTemporary].msg
    assert "component" in found[ShNested].msg
    assert "LiveComponent" in found[ShChild].msg
    assert "live_sessions" in found[ShBounded].msg
    assert "Meta.slots" in found[ShSlotted].msg
    assert "this.user" in found[ShUser].msg
    assert "request" in found[ShRequest].msg
    assert "csrf_token" in found[ShCsrf].msg


@pytest.mark.unit
def test_a_field_named_like_a_context_processor_is_not_reported():
    """``messages`` is a field of ShMessages: the template reads the field, not the request's."""
    assert all(message.obj is not ShMessages for message in check_shared_render(None))


# -- verification -----------------------------------------------------------------------------


@pytest.fixture
def verify(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("component", "name"),
    [(ShUserProperty, "user"), (ShSessionProperty, "session"), (ShInclude, "request")],
)
async def test_a_render_that_reads_the_viewer_raises(verify, component, name):
    """What the system check cannot see -- a property, an included template -- the render catches."""
    mounted = await wireview_testing.mount(component, id="board")
    with pytest.raises(shared_render.SharedRenderError, match=f"its render read '{name}'"):
        await mounted.render_diff()


@pytest.mark.asyncio
@pytest.mark.parametrize("component", [ShUser, ShRequest, ShCsrf])
async def test_a_template_that_reads_the_viewer_is_out_of_scope(verify, component):
    """What the system check sees keeps the class's renders its own: nothing to catch, nothing shared."""
    assert not shared_render.declared(component(id="board", user=AnonymousUser(), wire=WireviewMeta(params={})))
    mounted = await wireview_testing.mount(component, id="board")
    assert await mounted.render_diff() is not None


@pytest.mark.asyncio
async def test_a_field_named_like_the_request_is_read_as_the_field(verify):
    mounted = await wireview_testing.mount(ShMessages, id="board")
    diff = json.dumps(await mounted.render_diff())
    assert '["one"], ["two"]' in diff


@pytest.mark.asyncio
async def test_without_verification_the_viewer_is_read_as_ever(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    mounted = await wireview_testing.mount(ShUserProperty, id="board")
    assert "hi " in json.dumps(await mounted.render_diff())


@pytest.mark.asyncio
async def test_wireview_testing_verifies_unless_told_not_to():
    """pytest runs with DEBUG off; the test helpers watch all the same."""
    mounted = await wireview_testing.mount(ShUserProperty, id="board")
    with pytest.raises(shared_render.SharedRenderError):
        await mounted.render_diff()


@pytest.mark.asyncio
async def test_debug_turns_verification_on(settings, monkeypatch):
    settings.DEBUG = True
    assert shared_render.verifying()
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert not shared_render.verifying()


@pytest.mark.asyncio
async def test_a_render_taken_from_another_connection_that_differs_raises(verify, caplog):
    """ShViewer shows something of the connection through no watched name: two renders tell."""
    pages = [await Page.open(ShViewer) for _ in range(3)]
    for n, page in enumerate(pages):
        VIEWERS[page.session.channel_name] = f"viewer-{n}"  # type: ignore[index]
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    crashed = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert len(crashed) == 2, "every connection that took the render found its own differ"
    assert "renders it otherwise than the connection whose render it would take" in caplog.text


@pytest.mark.asyncio
async def test_verification_passes_a_render_that_is_the_same(verify):
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 3, "the leader's render and each follower's own, compared"
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_watched_fields_come_back_after_the_render(verify):
    user = await get_user_model().objects.acreate(username=f"sh-back-{uuid4().hex[:6]}")
    page = await Page.open(user=user)
    await broadcast([page], "m-1")
    assert page.component.user == user
    assert isinstance(page.component.session, SessionView)


# -- what keeps a render the connection's own -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("component", [ShDoc, ShAnyDoc])
async def test_a_model_instance_is_never_shared(component):
    """The key could name the row's pk alone: one connection's unsaved edit went to the other's page."""
    mark = await Bookmark.objects.acreate(title="saved title", url="https://example.com/")
    try:
        a, b = await Page.open(component, doc=mark.pk), await Page.open(component, doc=mark.pk)
        a.component.doc = mark  # type: ignore[attr-defined]
        b.component.doc = await Bookmark.objects.aget(pk=mark.pk)  # type: ignore[attr-defined]
        a.component.doc.title = "A's unsaved draft"  # type: ignore[attr-defined]
        RENDERS.clear()

        await a.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
        await b.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})

        assert RENDERS[component.__name__] == 2
        assert "unsaved draft" in a.html()
        assert "saved title" in b.html() and "unsaved draft" not in b.html()
    finally:
        await Bookmark.objects.filter(pk=mark.pk).adelete()


@pytest.mark.unit
def test_the_system_check_names_a_field_typed_as_a_row():
    found = {message.obj: message for message in check_shared_render(None)}
    assert "doc" in found[ShDoc].msg and "model instances or QuerySets" in found[ShDoc].msg
    assert ShAnyDoc not in found, "its type does not say so: the render finds out"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        Bookmark(pk=1, title="x"),
        Bookmark.objects.none(),
        [1, {"a": Bookmark(pk=1)}],
        {"rows": (Bookmark(pk=1),)},
    ],
)
def test_a_value_that_holds_a_row_has_no_key(value):
    component = ShAnyDoc(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}), doc=value)
    assert shared_render.key(component) is None


@pytest.mark.unit
@pytest.mark.parametrize(("one", "other"), [(1, True), ([1], (1,)), ({"1": 1}, {1: 1}), ("2026-10-06", None)])
def test_values_that_render_otherwise_have_other_keys(one, other):
    import datetime

    if other is None:
        other = datetime.date(2026, 10, 6)
    keys = [
        shared_render.key(ShAnyDoc(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}), doc=value))
        for value in (one, other)
    ]
    assert None not in keys and keys[0] != keys[1]


@pytest.mark.asyncio
async def test_a_field_pydantic_excludes_is_in_the_key():
    """``Field(exclude=True)`` keeps a value out of model_dump_json, not out of the render."""
    a, b = await Page.open(ShHidden), await Page.open(ShHidden)
    a.component.viewer = "alice"  # type: ignore[attr-defined]
    b.component.viewer = "bob"  # type: ignore[attr-defined]
    RENDERS.clear()

    await broadcast([a, b], "m-1")

    assert RENDERS["ShHidden"] == 2
    assert "alice" in a.html() and "bob" in b.html()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("message_id", [None, "m-1"])
async def test_a_queryset_field_signs_off_the_loop(message_id):
    """Its state's JSON queries the ids: signed on the loop, every render raised SynchronousOnlyOperation."""
    await Bookmark.objects.acreate(title="one", url="https://example.com/")
    try:
        mounted = await wireview_testing.mount(ShMarks, id="board", marks=await sync_to_async(Bookmark.objects.all)())
        assert "one" in json.dumps(await mounted.render_diff())

        pages = [await Page.open(ShMarks) for _ in range(2)]
        for page in pages:
            page.component.marks = await sync_to_async(Bookmark.objects.all)()  # type: ignore[attr-defined]
        await broadcast(pages, message_id)
        for page in pages:
            assert not any(command == "error" for command, _ in page.outbound.commands)
            assert "one" in page.html()
            assert '"ids":' not in page.html()
    finally:
        await Bookmark.objects.all().adelete()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_connections_that_take_a_render_sign_off_the_loop():
    """A computed field in the state queries: the takers' tokens too are signed in a trip."""
    await Bookmark.objects.acreate(title="one", url="https://example.com/")
    try:
        pages = [await Page.open(ShCounted) for _ in range(3)]
        RENDERS.clear()

        await broadcast(pages, "m-1")

        assert RENDERS["ShCounted"] == 1
        for page in pages:
            assert not any(command == "error" for command, _ in page.outbound.commands)
            assert unsign_envelope(_state_token(page), "ShCounted").state["rows"] == 1
    finally:
        await Bookmark.objects.all().adelete()


@pytest.mark.asyncio
async def test_a_component_its_receiver_redirected_takes_no_render():
    """Frozen by its own receiver: nothing more is drawn, a taken render as little as its own."""
    leader, follower = await Page.open(ShLeaving), await Page.open(ShLeaving)
    REDIRECTED.clear()
    REDIRECTED.add(follower.session.channel_name)  # type: ignore[arg-type]
    STORE["headline"] = "news"
    try:
        await leader.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
        await follower.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
    finally:
        REDIRECTED.clear()

    assert leader.outbound.renders()[0]["diff"] == {"1": "news"}
    assert follower.outbound.renders() == []
    assert leader.component.wire.template_evaluated


@pytest.mark.asyncio
async def test_a_connection_that_takes_a_render_evaluated_the_template():
    pages = [await Page.open() for _ in range(2)]
    STORE["headline"] = "news"
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 1
    assert all(page.component.wire.template_evaluated for page in pages)


@pytest.mark.asyncio
async def test_a_component_broadcast_is_shared(monkeypatch):
    """``self.broadcast()``, as the tutorials and Presence publish, names its message as abroadcast() does."""
    published: list[dict[str, t.Any]] = []

    class Broker:
        async def publish(self, channel: str, message: dict[str, t.Any]) -> None:
            published.append(message)

    pages = [await Page.open() for _ in range(3)]
    pages[0].component.wire.broker = Broker()  # type: ignore[assignment]
    await pages[0].component.broadcast("sh-board", n=1)
    await pages[0].component.broadcast("sh-board", n=2)
    (first, second) = published
    assert first["message_id"] != second["message_id"]
    assert first["kwargs"] == {"n": 1} and first["type"] == "notification" and first["channel"] == "sh-board"
    RENDERS.clear()
    STORE["headline"] = "news"

    await asyncio.gather(*(page.session.notification(dict(first)) for page in pages))

    assert RENDERS["ShBoard"] == 1


# -- what the checks see ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("component", "name"), [(ShIfInclude, "request"), (ShIfProperty, "user")])
async def test_a_read_the_template_swallows_raises_after_the_render(verify, component, name):
    """``{% if a and b %}`` turns any exception of an operand into False, and a property may catch its own:
    the read is written down first, and raises once the render is over."""
    mounted = await wireview_testing.mount(component, id="board")
    with pytest.raises(shared_render.SharedRenderError, match=f"its render read '{name}'"):
        await mounted.render_diff()


@pytest.mark.asyncio
async def test_a_swallowed_read_is_not_held_against_the_next_render(verify, monkeypatch):
    mounted = await wireview_testing.mount(ShIfInclude, id="board")
    with pytest.raises(shared_render.SharedRenderError):
        await mounted.render_diff()
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert await mounted.render_diff() is not None


@pytest.mark.unit
def test_the_system_check_reads_filter_arguments_and_tag_variables():
    found = {message.obj: message for message in check_shared_render(None)}
    assert "request" in found[ShFilterArg].msg
    assert "user" in found[ShFirstOf].msg
    assert ShIfInclude not in found, "an included template is the render's to catch"


@pytest.mark.asyncio
async def test_the_connections_that_take_a_render_sign_in_few_trips(monkeypatch):
    """A trip per taker cost more on the loop than the signature: those waiting together sign in one."""
    trips: list[int] = []
    sign_each = shared_render._sign_each

    def counted(components):
        trips.append(len(components))
        return sign_each(components)

    monkeypatch.setattr(shared_render, "_sign_each", counted)
    pages = [await Page.open() for _ in range(6)]
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert sum(trips) == 5, "every connection but the one that rendered"
    assert len(trips) < 5
    tokens = {_state_token(page) for page in pages}
    assert all(unsign_envelope(token, "ShBoard") for token in tokens)


@pytest.mark.asyncio
async def test_a_token_that_fails_to_sign_fails_its_own_connection(monkeypatch):
    from wireview.core import state

    pages = [await Page.open() for _ in range(3)]
    failing = pages[2].component
    sign = state.sign_state

    def sign_state(component):
        if component is failing:
            raise RuntimeError("this one cannot be signed")
        return sign(component)

    monkeypatch.setattr(state, "sign_state", sign_state)
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    errors = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert errors == [pages[2]]
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages[:2])
