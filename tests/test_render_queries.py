"""Which render part ran which SQL (#182, docs/design/render-part-queries.md).

The contract is stated as tables, as ``tests/test_live_session_contract.py`` does:

- where a statement is told to come from: the template line of the node that ran
  it (inheritance, ``block.super``, include, a function component, slots with and
  without ``let:``, the cached loader twice), the property the context read, or
  the async property's scope -- and the user nodes the design names
- which scope it lands in, for every path that renders or runs component code:
  join, event, HTTP, nested, LiveComponent, stream item, Broadcast item, handler,
  mount, ``joined()``, task. A new path gets a row
- a batch that signs many connections' tokens gives each signature to its asker
- connections and requests do not mix, a block drops what arrives after it
- the wrapper sits at the bottom of ``execute_wrappers`` and survives another
  tool's ``execute_wrapper()``; a block entered late still sees the worker
- off and outside a block, the boundaries make nothing
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import textwrap
import typing as t
from pathlib import Path
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django import template as django_template
from django.contrib.auth.models import AnonymousUser
from django.db import connection, connections
from django.http import HttpResponse
from django.template import loader
from django.template.base import Node
from django.test import AsyncClient, override_settings
from django.urls import path
from pydantic import BaseModel, computed_field
from testproj.outbound import RecordingOutbound
from testproj.waiting import eventually
from testproj.wireview_setting import set_wireview

from examples.quiz.models import Choice, Question, Quiz
from wireview import Broadcast, Component, LiveComponent, function_component, mount
from wireview.core import shared_render
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.debug import render_queries
from wireview.debug.render_queries import Queries, Row
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

# -- templates -------------------------------------------------------------------------------
# Line numbers matter: the tables below name them.

TEMPLATES = {
    # The location table (design §2-2)
    "rq/base.html": (
        "{% load wireview %}<div {% tag_header %}>\n"  # 1
        "{% block body %}\n"  # 2
        "{{ questions.count }}\n"  # 3
        "{% endblock %}\n"  # 4
        "{{ choices.exists }}\n"  # 5
        "</div>"  # 6
    ),
    "rq/child.html": (
        '{% extends "rq/base.html" %}\n'  # 1
        "{% load wireview %}\n"  # 2
        "{% block body %}\n"  # 3
        "{{ block.super }}\n"  # 4
        "{{ choices.count }}\n"  # 5
        '{% include "rq/inc.html" %}\n'  # 6
        '{% func "rqcard" items=choices %}\n'  # 7
        '{% component_block "RqOwner" id="own" %}{% fill row let:r %}\n'  # 8
        "{{ r.question.text }}\n"  # 9
        "{% endfill %}{% fill head %}\n"  # 10
        "{{ questions.exists }}\n"  # 11
        "{% endfill %}{% endcomponent %}\n"  # 12
        "{% endblock %}"  # 13
    ),
    "rq/inc.html": "<p>\n{{ choices.last.text }}\n</p>",
    "rq/card.html": "<ul>\n{{ items.count }}\n</ul>",
    "rq/owner.html": (
        "{% load wireview %}<s {% tag_header %}>\n"  # 1
        '{% render_slot "head" %}\n'  # 2
        '{% for r in rows %}{% render_slot "row" r=r %}{% endfor %}\n'  # 3
        "</s>"  # 4
    ),
    # The path table
    "rq/probe.html": "{% load wireview %}<p {% tag_header %}>\n{{ choices.count }}\n</p>",
    "rq/live_probe.html": "{% load wireview %}<p {% live_tag_header %}>\n{{ choices.count }}\n</p>",
    "rq/host.html": '{% load wireview %}<main {% tag_header %}>\n{% component "RqProbe" id="p" %}\n</main>',
    "rq/live_host.html": (
        '{% load wireview %}<main {% tag_header %}>\n{% live_component "RqLiveProbe" id="lp" %}\n</main>'
    ),
    "rq/item.html": "<li>\n{{ item.question.text }}\n</li>",
    "rq/feed.html": '{% load wireview %}<ul {% tag_header %} wire-stream="items"></ul>',
    "rq/plain.html": "{% load wireview %}<p {% tag_header %}>{{ count }}</p>",
    "rq/board.html": "{% load wireview %}<p {% tag_header %}>\n{{ choices.count }} {{ total }}\n</p>",
    # User nodes (design §2-4)
    "rq/outer.html": '{% include "rq/inner.html" %}',
    "rq/inner.html": (
        "{% load rq_nodes %}\n"  # 1
        "-\n"  # 2
        "{% rq_overridden %}\n"  # 3
        "{% rq_overridden_other %}\n"  # 4
        "{% rq_dynamic %}\n"  # 5
        "{% rq_bound %}"  # 6
    ),
}


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    # Cached: the same compiled nodes on every render, as in production
                    "loaders": [
                        (
                            "django.template.loaders.cached.Loader",
                            [("django.template.loaders.locmem.Loader", TEMPLATES)],
                        )
                    ],
                    "libraries": {"rq_nodes": __name__},
                },
            }
        ]
    ):
        yield


@pytest.fixture
def quiz(db):
    quiz = Quiz.objects.create(title="Q")
    question = Question.objects.create(quiz=quiz, text="Which?")
    Choice.objects.create(question=question, text="a")
    Choice.objects.create(question=question, text="b")
    return quiz


# -- components ------------------------------------------------------------------------------


class _Lists:
    @property
    def questions(self):
        return Question.objects.all()

    @property
    def choices(self):
        return Choice.objects.all()


class RqPage(_Lists, Component):
    class Meta:
        template_name = "rq/child.html"

    @property
    def total(self) -> int:
        return Choice.objects.count()

    @property
    async def first_title(self) -> str:
        quiz = await Quiz.objects.afirst()
        return quiz.title if quiz else ""


class RqOwner(Component):
    class Meta:
        template_name = "rq/owner.html"

    @property
    def rows(self):
        return Choice.objects.all()


@function_component(name="rqcard", template="rq/card.html")
def rqcard(items: t.Any = None):
    return {"items": items}


class _Probe(_Lists):
    @property
    def total(self) -> int:
        return Choice.objects.count()

    @property
    async def first_title(self) -> str:
        quiz = await Quiz.objects.afirst()
        return quiz.title if quiz else ""

    async def poke(self):
        await Choice.objects.acount()


class RqProbe(_Probe, Component):
    class Meta:
        template_name = "rq/probe.html"

    async def joined(self):
        await Question.objects.acount()


class RqLiveProbe(_Probe, LiveComponent):
    class Meta:
        template_name = "rq/live_probe.html"


class RqHost(Component):
    class Meta:
        template_name = "rq/host.html"


class RqLiveHost(Component):
    class Meta:
        template_name = "rq/live_host.html"


class RqFeed(Component):
    class Meta:
        template_name = "rq/feed.html"
        subscriptions = {"rq-feed"}

    async def add(self):
        await self.stream_insert("items", await Choice.objects.select_related(None).afirst(), template="rq/item.html")


class RqWorker(Component):
    class Meta:
        template_name = "rq/plain.html"

    count: int = 0

    async def start(self):
        await self.start_async("load", self._load())

    async def assign(self):
        self._result = await self.assign_async(self._load())

    async def start_and_wait(self):
        await self.start_async("load", self._load())
        await self._async_tasks["load"]

    async def _load(self) -> int:
        return await Choice.objects.acount()

    async def handle_async(self, name, result):
        await Question.objects.acount()
        self.count = result.result


def _reads_per_dump() -> int:
    """How many times ``model_dump_json`` reads a computed field: twice before pydantic 2.12.

    The floor is 2.7 (``make test-lowest``), so a signature of ``RqBoard``'s state runs
    ``total``'s statement this many times.
    """
    reads = []

    class Probe(BaseModel):
        @computed_field  # type: ignore[prop-decorator]
        @property
        def read(self) -> int:
            reads.append(1)
            return 0

    Probe().model_dump_json(exclude=set())
    return len(reads)


#: The statements one signature of ``RqBoard``'s state runs
SIGN = _reads_per_dump()


class RqBoard(_Lists, Component):
    """A shared render whose state signs with a query: a computed field."""

    class Meta:
        template_name = "rq/board.html"
        subscriptions = {"rq-board"}
        shared_render = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return Choice.objects.count()

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


# -- user nodes (design §1-2, §2-4) ------------------------------------------------------------

register = django_template.Library()


def _query() -> str:
    return str(Choice.objects.count())


class _Overridden(Node):
    """``render_annotated`` overridden to call ``render()`` directly: exact."""

    def render_annotated(self, context):
        return self.render(context)

    def render(self, context):
        return _query()


class _OverriddenOther(Node):
    """``render_annotated`` overridden, the SQL in a method of another name: exact."""

    def render_annotated(self, context):
        return self._work()

    def _work(self):
        return _query()


class _Made(Node):
    """Made while rendering: the parser never gave it an origin."""

    def render(self, context):
        return _query()


class _Dynamic(Node):
    def render(self, context):
        return _Made().render(context)


def _impl(self, context):
    return _query()


class _Bound(Node):
    """The entry bound to a function of another name: the nearest node that can be seen."""

    render_annotated = _impl

    def render(self, context):
        return _query()


register.tag("rq_overridden", lambda parser, token: _Overridden())
register.tag("rq_overridden_other", lambda parser, token: _OverriddenOther())
register.tag("rq_dynamic", lambda parser, token: _Dynamic())
register.tag("rq_bound", lambda parser, token: _Bound())


# -- helpers ---------------------------------------------------------------------------------


def where(row: Row) -> str:
    return row.where


def render_of(row: Row) -> str:
    """The render (or other non-property scope) the row ran in."""
    scope = row.scope
    while scope is not None and scope.kind == "property":
        scope = scope.outer
    return scope.describe() if scope is not None else "-"


def table(rows: list[Row]) -> list[tuple[str, str]]:
    return sorted((where(row), render_of(row)) for row in rows)


async def started(name: str = "rq") -> tuple[WireviewSession, RecordingOutbound]:
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name=f"{name}-{uuid4().hex[:8]}")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    return session, outbound


def signed(cls: type[Component], id: str, **fields: t.Any) -> str:
    return sign_state(cls(id=id, user=AnonymousUser(), wire=WireviewMeta(params={}), **fields))


async def join(session: WireviewSession, cls: type[Component], id: str, **fields: t.Any) -> None:
    state = await sync_to_async(signed)(cls, id, **fields)
    await session.handle_message({"command": "join", "payload": {"name": cls._name, "state": state}})


# -- where: the location table ---------------------------------------------------------------

PAGE = "render RqPage#page (join)"
OWNER = "render RqOwner#own (nested)"
LOCATIONS = [
    ("property total", PAGE),
    ("property first_title", PAGE),
    ("rq/base.html:3", PAGE),  # block.super draws the parent's block
    ("rq/child.html:5", PAGE),  # the child's block: not the parent's file
    ("rq/base.html:5", PAGE),  # the parent outside its blocks
    ("rq/inc.html:2", PAGE),  # inside an include
    ("rq/card.html:2", PAGE),  # inside a function component's template
    ("rq/child.html:11", PAGE),  # a slot without let: is drawn in the filler's pass
    ("rq/owner.html:3", OWNER),  # the slot owner's loop
    ("rq/child.html:9", OWNER),  # a let: slot runs in the owner's render, at the fill
    ("rq/child.html:9", OWNER),
]


@pytest.mark.asyncio
async def test_each_statement_is_told_at_its_own_template_line(quiz):
    session, _ = await started()
    async with Queries() as joined:
        await join(session, RqPage, "page")
    assert table(joined.rows) == sorted(LOCATIONS)

    # The cached loader hands the same compiled nodes to the next render
    page = session.repo.get("page")
    async with Queries() as again:
        await session.send_render(page)
    assert table(again.rows) == sorted((at, scope.replace("(join)", "(render)")) for at, scope in LOCATIONS)


@pytest.mark.asyncio
async def test_an_http_render_tells_the_same_lines(quiz):
    view = await mount(RqPage, id="page")
    for _ in range(2):  # twice: the cached loader's nodes
        async with view.queries() as q:
            await sync_to_async(view.render)()
        assert table(q.rows) == sorted((at, scope.replace("(join)", "(http)")) for at, scope in LOCATIONS)


@pytest.mark.asyncio
async def test_the_node_kind_and_the_template_path_are_on_each_row(quiz):
    view = await mount(RqPage, id="page")
    async with view.queries() as q:
        await view.render_diff()
    by_place = {row.where: row for row in q.rows}
    assert by_place["rq/child.html:5"].node == "{{ }}"
    assert by_place["rq/owner.html:3"].node == "for"
    assert by_place["rq/child.html:5"].path == "rq/child.html"  # locmem: the name; a file loader: the path
    assert by_place["property first_title"].scope.kind == "property"
    assert by_place["property total"].scope.kind == "render"


def test_user_nodes_are_told_as_the_design_promises(quiz):
    """Overridden entries are exact; a node made while rendering is approximate; a renamed entry is not seen."""
    with Queries() as q:
        for _ in range(2):
            loader.get_template("rq/outer.html").render({})
    rows = q.rows
    assert [(row.where, row.approximate) for row in rows[:4]] == [
        ("rq/inner.html:3", False),
        ("rq/inner.html:4", False),
        ("rq/inner.html:5 (approx.)", True),
        ("rq/outer.html:1", False),  # the include around it: the limit the design states
    ]
    assert [row.where for row in rows[4:]] == [row.where for row in rows[:4]]


# -- which scope: the path table -------------------------------------------------------------


async def _path_join(quiz) -> list[Row]:
    session, _ = await started()
    async with Queries() as q:
        await join(session, RqProbe, "p")
    return q.rows


async def _path_event(quiz) -> list[Row]:
    session, _ = await started()
    await join(session, RqProbe, "p")
    async with Queries() as q:
        await session.handle_message(
            {
                "command": "user_event",
                "payload": {"id": "p", "command": "poke", "implicit_args": {}, "explicit_args": {}},
            }
        )
    return q.rows


async def _path_http(quiz) -> list[Row]:
    view = await mount(RqProbe, id="p")
    async with view.queries() as q:
        await sync_to_async(view.render)()
    return q.rows


async def _path_nested(quiz) -> list[Row]:
    session, _ = await started()
    async with Queries() as q:
        await join(session, RqHost, "h")
    return q.rows


async def _path_live_component(quiz) -> list[Row]:
    session, _ = await started()
    async with Queries() as q:
        await join(session, RqLiveHost, "h")
    return q.rows


async def _path_live_component_http(quiz) -> list[Row]:
    view = await mount(RqLiveHost, id="h")
    async with view.queries() as q:
        await sync_to_async(view.render)()
    return q.rows


RENDER_PATHS = {
    "join": (_path_join, "render RqProbe#p (join)"),
    "event": (_path_event, "render RqProbe#p (event poke)"),
    "http": (_path_http, "render RqProbe#p (http)"),
    "nested": (_path_nested, "render RqProbe#p (nested)"),
    "live component": (_path_live_component, "render RqLiveProbe#lp (join)"),
    "live component, http": (_path_live_component_http, "render RqLiveProbe#lp (nested)"),
}
POSITIONS = {
    "sync property": "property total",
    "async property": "property first_title",
    "template line": None,  # the probe's template, line 2
}


@pytest.mark.asyncio
@pytest.mark.parametrize("position", list(POSITIONS))
@pytest.mark.parametrize("path_name", list(RENDER_PATHS))
async def test_every_render_path_tells_its_scope_and_position(quiz, path_name, position):
    run, scope = RENDER_PATHS[path_name]
    rows = await run(quiz)
    template = "rq/live_probe.html:2" if "live" in path_name else "rq/probe.html:2"
    at = POSITIONS[position] or template
    assert (at, scope) in table(rows), table(rows)


@pytest.mark.asyncio
async def test_a_handler_its_join_and_its_mount_are_scopes_of_their_own(quiz):
    session, _ = await started()
    async with Queries() as joined:
        await join(session, RqProbe, "p")
    assert ("(outside a template)", "joined RqProbe#p") in table(joined.rows)
    async with Queries() as event:
        await session.handle_message(
            {
                "command": "user_event",
                "payload": {"id": "p", "command": "poke", "implicit_args": {}, "explicit_args": {}},
            }
        )
    assert ("(outside a template)", "handler RqProbe#p.poke") in table(event.rows)

    view = await mount(RqProbe, id="p")
    async with view.queries() as called:
        await view.call("poke")
    assert table(called.rows) == [("(outside a template)", "handler RqProbe#p.poke")]


@pytest.mark.asyncio
async def test_a_stream_item_renders_in_a_scope_of_its_own(quiz):
    view = await mount(RqFeed, id="feed")
    async with view.queries() as q:
        await view.call("add")
    assert ("rq/item.html:2", "render RqFeed#feed (stream item)") in table(q.rows)


@pytest.mark.asyncio
async def test_a_broadcast_item_renders_in_the_publish(quiz):
    choice = await Choice.objects.afirst()
    with Queries() as q:
        await Broadcast(RqFeed, "rq-feed").stream_insert("items", choice, template="rq/item.html").asend()
    assert table(q.rows) == [("rq/item.html:2", "broadcast RqFeed 'rq-feed'")]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["start", "assign"])
async def test_a_task_is_not_the_handler_that_started_it(quiz, handler):
    view = await mount(RqWorker, id="w")
    async with view.queries() as q:
        await view.call(handler)
        await eventually(lambda: len(q.rows) >= (2 if handler == "start" else 1))
    rendered = table(q.rows)
    task = "task RqWorker#w load" if handler == "start" else "task RqWorker#w assign_async(RqWorker._load)"
    assert ("(outside a template)", task) in rendered
    assert not any(scope == "handler RqWorker#w." + handler for _, scope in rendered), rendered
    if handler == "start":
        assert ("(outside a template)", "handler RqWorker#w.handle_async('load')") in rendered


@pytest.mark.asyncio
async def test_a_task_a_handler_waits_for_logs_on_its_own(quiz, monkeypatch, caplog):
    """The task's statements are not the handler's even while that handler is still open (detach)."""
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True)
    view = await mount(RqWorker, id="w")
    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        await view.call("start_and_wait")
    heads = sorted(
        record.getMessage().splitlines()[0] for record in caplog.records if record.name == "wireview.queries"
    )
    assert heads == [
        "handler RqWorker#w.handle_async('load'): 1 query",
        "task RqWorker#w load: 1 query",
    ]


@pytest.mark.asyncio
async def test_a_block_drops_what_a_task_runs_after_it_ends(quiz):
    view = await mount(RqWorker, id="w")
    gate = asyncio.Event()

    async def late() -> int:
        await gate.wait()
        return await Choice.objects.acount()

    async with view.queries() as q:
        await view.component.start_async("late", late())
    gate.set()
    await eventually(lambda: view.component.count == 2)
    assert q.count == 0


# -- shared render ---------------------------------------------------------------------------


class _Tab:
    def __init__(self, session: WireviewSession, block: Queries) -> None:
        self.session = session
        self.block = block


async def _open_tabs(n: int) -> list[_Tab]:
    tabs = []
    for _ in range(n):
        session, _ = await started("rqsh")
        await join(session, RqBoard, "board")
        tabs.append(_Tab(session, Queries()))
    return tabs


async def _notify(tab: _Tab, message_id: str) -> None:
    async with tab.block:
        await tab.session.notification({"channel": "rq-board", "kwargs": {}, "message_id": message_id})


def _kinds(rows: list[Row]) -> list[str]:
    out = []
    for row in rows:
        scope = row.scope
        assert scope is not None
        if scope.kind == "sign":
            out.append("sign")
        elif scope.kind == "render" and scope.detail.startswith("verify"):
            out.append("verify")
        else:
            out.append(scope.kind)
    return sorted(out)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verify", "expected"),
    [
        (None, ["render", "render", "render"]),  # wireview.testing is not imported here: DEBUG off, no verify
        (True, ["verify", "verify", "verify"]),
        (False, ["sign"]),
    ],
)
async def test_each_shared_render_connection_gets_its_own_statements(quiz, monkeypatch, verify, expected):
    """The leader renders; a taker verifies (its own render again) or signs its own token, in its own block."""
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=verify)
    tabs = await _open_tabs(3)
    await asyncio.gather(*(_notify(tab, "m-1") for tab in tabs))
    leaders = [
        tab
        for tab in tabs
        if any(
            row.scope.describe().startswith("render") and "verify" not in row.scope.describe() for row in tab.block.rows
        )
    ]
    assert len(leaders) == 1
    leader = leaders[0]
    # The board: {{ choices.count }}, the computed field read by the context and by the signature
    assert len(leader.block.rows) == 2 + SIGN
    takers = [tab for tab in tabs if tab is not leader]
    for taker in takers:
        kinds = _kinds(taker.block.rows)
        if verify is False:
            assert kinds == ["sign"] * SIGN, "the taker's own signature, not the other taker's"
        elif verify is True:
            assert set(kinds) == {"verify"} and len(kinds) == 2 + SIGN
    if verify is None:
        # Django's test runner turns DEBUG off: the takers sign, as with False
        for taker in takers:
            assert _kinds(taker.block.rows) == ["sign"] * SIGN


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [True, False])
async def test_a_signature_batch_gives_each_statement_to_its_asker(quiz, monkeypatch, restore):
    """Design §2-4: A and B sign in one batch, each in its own block."""
    if not restore:
        monkeypatch.setattr(render_queries, "restored", lambda state: render_queries._NULL)
    signer = shared_render._Signer()
    gate = asyncio.Event()
    real = shared_render._sign_each
    trips: list[int] = []

    def held(components):
        trips.append(len(components))
        return real(components)

    a_comp = RqBoard(id="a", user=AnonymousUser(), wire=WireviewMeta(params={}))
    b_comp = RqBoard(id="b", user=AnonymousUser(), wire=WireviewMeta(params={}))
    outer = Queries()
    a, b = Queries(), Queries()

    async def ask(block: Queries, component: Component, inside: Queries | None = None) -> None:
        async def go():
            async with block:
                with render_queries.scope("render", component, "probe"):
                    waiting.append(component)
                    await gate.wait()
                    await signer.sign(component)

        if inside is not None:
            async with inside:
                await go()
        else:
            await go()

    monkeypatch.setattr(shared_render, "_sign_each", held)
    waiting: list[Component] = []
    tasks = [asyncio.create_task(ask(a, a_comp, outer)), asyncio.create_task(ask(b, b_comp))]
    # Both ask before the batch starts: one trip signs the two
    await eventually(lambda: len(waiting) == 2)
    gate.set()
    await asyncio.gather(*tasks)
    assert trips == [2]
    if restore:
        assert (a.count, b.count, outer.count) == (SIGN, SIGN, SIGN)
        assert render_of(a.rows[0]) == "state signing RqBoard#a"
        assert a.rows[0].scope.outer.describe() == "render RqBoard#a (probe)"
        assert b.rows[0].scope.outer.describe() == "render RqBoard#b (probe)"
    else:
        assert sorted((a.count, b.count)) == [0, 2 * SIGN], "the batch's starter took both"


@pytest.mark.asyncio
async def test_a_signature_whose_asker_left_is_dropped(quiz):
    signer = shared_render._Signer()
    a_comp = RqBoard(id="a", user=AnonymousUser(), wire=WireviewMeta(params={}))
    b_comp = RqBoard(id="b", user=AnonymousUser(), wire=WireviewMeta(params={}))
    a, b = Queries(), Queries()

    async def ask_a():
        async with a:
            await signer.sign(a_comp)

    async def ask_b():
        async with b:
            asked = asyncio.ensure_future(signer.sign(b_comp))
        await asked  # B's block ended before the batch ran

    await asyncio.gather(ask_a(), ask_b())
    assert (a.count, b.count) == (SIGN, 0)


# -- isolation -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_connections_joining_at_once_keep_their_own(quiz):
    (s1, _), (s2, _) = await started(), await started()
    b1, b2 = Queries(), Queries()

    async def go(session, block, cls, id):
        async with block:
            await join(session, cls, id)

    await asyncio.gather(go(s1, b1, RqPage, "page"), go(s2, b2, RqProbe, "p"))
    assert table(b1.rows) == sorted(LOCATIONS)
    assert {render_of(row) for row in b2.rows} == {"render RqProbe#p (join)", "joined RqProbe#p"}


class _PageView:
    """A plain Django view drawing one component: the HTTP render with no wireview around it."""

    def __init__(self, cls: type[Component]) -> None:
        self.cls = cls

    def __call__(self, request):
        html = loader.get_template("rq/http.html").render({"name": self.cls._name}, request)
        return HttpResponse(html)


TEMPLATES["rq/http.html"] = '{% load wireview %}{% component name id="x" %}'

urlpatterns = [
    path("page/", _PageView(RqPage)),
    path("probe/", _PageView(RqProbe)),
]


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=__name__, WIREVIEW={"DEBUG_RENDER_QUERIES": True})
async def test_two_http_requests_at_once_log_their_own(quiz, caplog):
    client = AsyncClient()
    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        first, second = await asyncio.gather(client.get("/page/"), client.get("/probe/"))
    assert first.status_code == second.status_code == 200
    heads = sorted(
        record.getMessage().splitlines()[0] for record in caplog.records if record.name == "wireview.queries"
    )
    assert heads == [
        "render RqPage#x (http): 11 queries",
        "render RqProbe#x (http): 3 queries",
    ]


# -- the log ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_render_with_repeats_logs_a_warning(quiz, monkeypatch, caplog):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True)
    await sync_to_async(Choice.objects.create)(question=await Question.objects.afirst(), text="c")
    session, _ = await started()
    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        await join(session, RqPage, "page")
    (warning,) = [record for record in caplog.records if record.levelno == logging.WARNING]
    text = warning.getMessage()
    assert text.splitlines()[0] == "render RqPage#page (join): 12 queries, 1 repeated"
    (repeated,) = [line for line in text.splitlines() if "<- repeated" in line]
    assert "rq/child.html:9" in repeated and "3×" in repeated and "[render RqOwner#own (nested)]" in repeated


TEMPLATES["rq/rack.html"] = (
    '{% load wireview %}<main {% tag_header %}>{% for k in keys %}{% live_component "RqSibling" id=k n=n %}{% endfor %}'
    "</main>"
)
TEMPLATES["rq/sibling.html"] = "{% load wireview %}<p {% live_tag_header %}>\n{{ questions.first.text }}\n</p>"


class RqRack(Component):
    class Meta:
        template_name = "rq/rack.html"

    keys: list[str] = ["s1", "s2", "s3"]
    n: int = 0

    async def bump(self):
        self.n += 1


class RqSibling(_Lists, LiveComponent):
    class Meta:
        template_name = "rq/sibling.html"

    n: int = 0


def _heads(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "wireview.queries"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["join", "event"])
async def test_sibling_live_components_running_one_statement_each_log_one_repeat(quiz, monkeypatch, caplog, path):
    """#189: the LiveComponents a render names render after it, each on its own; one send_render is one log."""
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True)
    session, _ = await started()
    if path == "event":
        await join(session, RqRack, "rack")
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        if path == "join":
            await join(session, RqRack, "rack")
        else:
            # A new prop for each: update() and a render of every sibling
            payload = {"id": "rack", "command": "bump", "implicit_args": {}, "explicit_args": {}}
            await session.handle_message({"command": "user_event", "payload": payload})
    messages = _heads(caplog)
    why = "join" if path == "join" else "event bump"
    (text,) = [message for message in messages if message.startswith("render RqRack#rack")]
    assert text.splitlines()[0] == f"render RqRack#rack ({why}): 3 queries, 1 repeated", messages
    (line,) = text.splitlines()[1:]
    assert "rq/sibling.html:2" in line and "3×" in line and "<- repeated" in line
    assert f"[render RqSibling ×3 ({why})]" in line
    assert not any(message.startswith("render RqSibling") for message in messages), messages
    assert [record.levelname for record in caplog.records if record.getMessage() == text] == ["WARNING"]


@pytest.mark.asyncio
async def test_a_send_render_is_one_scope_around_its_live_components(quiz):
    """The rows keep their own render; the tree is the outermost scope above them."""
    session, _ = await started()
    async with Queries() as q:
        await join(session, RqRack, "rack")
    assert table(q.rows) == [("rq/sibling.html:2", f"render RqSibling#{k} (join)") for k in ("s1", "s2", "s3")]
    (tree,) = {row.scope.outer for row in q.rows}
    assert tree is not None and tree.kind == "tree" and tree.outer is None
    assert tree.describe() == "render RqRack#rack (join)"


@pytest.mark.asyncio
async def test_below_the_threshold_it_logs_at_debug(quiz, monkeypatch, caplog):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True)
    session, _ = await started()
    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        await join(session, RqProbe, "p")
    levels = {
        record.getMessage().splitlines()[0]: record.levelname
        for record in caplog.records
        if record.name == "wireview.queries"
    }
    assert levels == {"render RqProbe#p (join): 3 queries": "DEBUG", "joined RqProbe#p: 1 query": "DEBUG"}


@pytest.mark.asyncio
async def test_assert_no_repeats_names_the_place(quiz):
    view = await mount(RqPage, id="page")
    async with view.queries() as q:
        await view.render_diff()
    assert q.count == 11
    q.assert_no_repeats(threshold=3)
    with pytest.raises(AssertionError) as raised:
        q.assert_no_repeats()
    assert "rq/child.html:9" in str(raised.value) and "2×" in str(raised.value)


@pytest.mark.asyncio
async def test_blocks_nest(quiz):
    view = await mount(RqProbe, id="p")
    async with view.queries() as outer:
        await view.call("poke")
        async with view.queries() as inner:
            await view.call("poke")
    assert (outer.count, inner.count) == (2, 1)


# -- off -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_and_outside_a_block_the_boundaries_make_nothing(quiz, monkeypatch):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=False)
    seen: list[t.Any] = []
    real = render_queries._Open.__enter__

    def spy(self):
        seen.append(self.scope)
        return real(self)

    monkeypatch.setattr(render_queries._Open, "__enter__", spy)
    session, _ = await started()
    await join(session, RqPage, "page")
    view = await mount(RqWorker, id="w")
    await view.call("start")
    await eventually(lambda: view.component.count == 2)
    assert seen == []
    assert render_queries.capture() is None


@pytest.mark.unit
def test_none_follows_debug(settings):
    settings.WIREVIEW = {}
    settings.DEBUG = True
    assert render_queries.enabled()
    settings.DEBUG = False
    assert not render_queries.enabled()
    settings.WIREVIEW = {"DEBUG_RENDER_QUERIES": True}
    assert render_queries.enabled()


# -- the wrapper's life (design §6) ------------------------------------------------------------


def _other(execute, sql, params, many, context):
    return execute(sql, params, many, context)


@pytest.mark.parametrize("alias", ["default"])
def test_the_wrapper_stays_at_the_bottom_under_another_tools_wrapper(alias):
    conn = connections[alias]
    ours = render_queries._installed_wrapper()
    conn.close()
    conn.execute_wrappers[:] = [w for w in conn.execute_wrappers if w is not ours]
    # Another tool's block, and the connection's first cursor opens inside it
    with conn.execute_wrapper(_other):
        conn.ensure_connection()  # connection_created puts ours in
        assert conn.execute_wrappers == [ours, _other]
    assert conn.execute_wrappers == [ours]
    # Reconnecting does not add a second one
    conn.close()
    conn.ensure_connection()
    assert conn.execute_wrappers == [ours]
    # Nested blocks, a reconnect inside, an exception on the way out
    with pytest.raises(RuntimeError):
        with conn.execute_wrapper(_other):
            with conn.execute_wrapper(_other):
                conn.close()
                conn.ensure_connection()
                assert conn.execute_wrappers == [ours, _other, _other]
            raise RuntimeError
    assert conn.execute_wrappers == [ours]


def test_capture_queries_context_still_works_beside_it():
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured, Queries() as q:
        Choice.objects.count()
    assert len(captured) == 1 and q.count == 1


SCRIPT = """
import os, sys, asyncio
sys.path[:0] = [{root!r}, {tests!r}]
os.environ["DJANGO_SETTINGS_MODULE"] = "testproj.settings"
import django
from django.conf import settings
settings.DEBUG = False
django.setup()
from django.db import connection, connections
from asgiref.sync import sync_to_async
from wireview.debug import render_queries

def wrapped():
    return [render_queries._installed_wrapper() in c.execute_wrappers for c in connections.all(initialized_only=True)]

connection.ensure_connection()
print("off", render_queries.installed(), any(wrapped()))
{rest}
"""

LATE = """
def select():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")

# How the async ORM reaches the database: the thread-sensitive worker
aselect = sync_to_async(select, thread_sensitive=True)

async def main():
    # Off: the worker opens its connection first
    await aselect()
    async with render_queries.Queries() as q:
        await aselect()
        await aselect()
    print("late", q.count)
    # A task created in the block that runs after it: dropped
    gate = asyncio.Event()
    async def later():
        await gate.wait()
        await aselect()
    async with render_queries.Queries() as r:
        task = asyncio.create_task(later())
    gate.set()
    await task
    print("after", r.count)

asyncio.run(main())
import wireview.testing
print("testing", render_queries.installed(), all(wrapped()))
"""


def _run(rest: str) -> list[str]:
    root = Path(__file__).resolve().parent.parent
    script = SCRIPT.format(root=str(root), tests=str(root / "tests"), rest=textwrap.dedent(rest))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, cwd=root)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


@pytest.mark.integration
def test_off_without_wireview_testing_no_connection_has_the_wrapper_and_a_late_block_still_sees_the_worker():
    out = _run(LATE)
    assert out == ["off False False", "late 2", "after 0", "testing True True"]


# -- collecting ends with the last block (review of #182) --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("starts", ["before the block ends", "after the block ends"])
async def test_with_the_setting_off_a_task_stops_collecting_when_its_block_ends(quiz, monkeypatch, caplog, starts):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=False)
    begun, gate = asyncio.Event(), asyncio.Event()
    opened: list[t.Any] = []

    async def task() -> None:
        if starts == "after the block ends":
            await begun.wait()
        render_queries.detach()
        with render_queries.scope("task", None, "late") as scope:
            opened.append(scope)
            if starts == "before the block ends":
                await Choice.objects.acount()  # inside the block: the block's
                begun.set()
            await gate.wait()
            for _ in range(3):  # after it: nobody's
                await Choice.objects.acount()
                if scope is not None:
                    assert len(scope.rows) <= 1, "nothing more lands in the scope"

    with caplog.at_level(logging.DEBUG, logger="wireview.queries"):
        async with Queries() as q:
            running = asyncio.create_task(task())
            if starts == "before the block ends":
                await begun.wait()
        begun.set()
        gate.set()
        await running

    assert q.count == (1 if starts == "before the block ends" else 0)
    assert [record for record in caplog.records if record.name == "wireview.queries"] == []
    if starts == "after the block ends":
        assert opened == [None], "no scope is made for a context whose blocks all ended"
    else:
        assert opened[0].rows == [], "dropped when it closed: nobody collects it"


@pytest.mark.asyncio
async def test_a_task_whose_inner_block_ended_still_collects_for_the_open_outer_one(quiz, monkeypatch):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=False)
    gate = asyncio.Event()

    async def task() -> None:
        await gate.wait()
        render_queries.detach()
        with render_queries.scope("task", None, "late"):
            await Choice.objects.acount()

    async with Queries() as outer:
        async with Queries() as inner:
            running = asyncio.create_task(task())
        gate.set()
        await running
    assert (outer.count, inner.count) == (1, 0)
    assert render_of(outer.rows[0]) == "task  late"


# -- what is one place (review of #182) --------------------------------------------------------


class RqTotalA(Component):
    class Meta:
        template_name = "rq/plain.html"

    @property
    def total(self) -> int:
        return Choice.objects.count()

    async def first(self):
        await Choice.objects.acount()

    async def second(self):
        await Choice.objects.acount()


class RqTotalB(Component):
    class Meta:
        template_name = "rq/plain.html"

    @property
    def total(self) -> int:
        return Choice.objects.count()

    @property
    async def later(self) -> int:
        return await Choice.objects.acount()


class RqTotalC(RqTotalB):
    """Inherits B's properties: the same definitions."""


@pytest.mark.asyncio
async def test_two_classes_properties_of_one_name_are_two_places(quiz):
    a, b = await mount(RqTotalA, id="a"), await mount(RqTotalB, id="b")
    async with a.queries() as q:
        await a.render_diff()
        await b.render_diff()
    q.assert_no_repeats()  # A.total once, B.total once, B.later once


@pytest.mark.asyncio
async def test_one_property_read_twice_is_a_repeat(quiz):
    b, c = await mount(RqTotalB, id="b"), await mount(RqTotalC, id="c")
    async with b.queries() as q:
        await b.render_diff()
        await c.render_diff()  # the same definitions, read by another instance
    with pytest.raises(AssertionError) as raised:
        q.assert_no_repeats()
    assert "property total" in str(raised.value) and "property later" in str(raised.value)


@pytest.mark.asyncio
async def test_work_without_a_place_is_told_apart_by_kind_class_and_name(quiz):
    a = await mount(RqTotalA, id="a")
    async with a.queries() as apart:
        await a.call("first")
        await a.call("second")
    apart.assert_no_repeats()
    async with a.queries() as again:
        await a.call("first")
        await a.call("first")
    with pytest.raises(AssertionError):
        again.assert_no_repeats()


# -- more boundaries (review of #182) ----------------------------------------------------------


class RqGate:
    @staticmethod
    async def on_mount(component, params, session):
        await Choice.objects.acount()
        return {"cont": True}


class RqGuarded(Component):
    class Meta:
        template_name = "rq/plain.html"
        on_mount = [RqGate]

    async def params_changed(self, params, uri):
        await Question.objects.acount()

    async def boom(self):
        await Choice.objects.acount()
        raise RuntimeError("boom")


class RqChild(LiveComponent):
    class Meta:
        template_name = "rq/plain.html"

    async def update(self, **assigns):
        await Choice.objects.acount()


class RqChildren(LiveComponent):
    class Meta:
        template_name = "rq/plain.html"

    @classmethod
    async def update_many(cls, updates):
        await Choice.objects.acount()


@pytest.mark.asyncio
async def test_mount_hooks_and_params_changed_are_scopes_of_their_own(quiz):
    with Queries() as q:
        await mount(RqGuarded, id="g", params={"q": "1"})
    assert table(q.rows) == [
        ("(outside a template)", "handler RqGuarded#g.params_changed"),
        ("(outside a template)", "mount RqGuarded#g"),
    ]


@pytest.mark.asyncio
async def test_update_and_update_many_are_handlers(quiz):
    from wireview.live_component import run_updates

    child = RqChild(id="c1", user=AnonymousUser(), wire=WireviewMeta(params={}))
    many = [RqChildren(id=f"m{n}", user=AnonymousUser(), wire=WireviewMeta(params={})) for n in range(2)]
    with Queries() as q:
        await run_updates([(child, {}), *((each, {}) for each in many)], lambda *a: None)
    assert table(q.rows) == [
        ("(outside a template)", "handler RqChild#c1.update"),
        ("(outside a template)", "handler RqChildren#m0.update_many"),
    ]


@pytest.mark.asyncio
async def test_the_state_comes_back_after_a_halt_a_crash_and_a_cancel(quiz):
    session, _ = await started()
    await join(session, RqGuarded, "g")
    async with Queries():
        before = render_queries.capture()
        # A handle_event hook that halts returns from inside the handler's scope
        component = session.repo.get("g")

        async def halt(event, params):
            return {"halt": True}

        component.attach_hook("stop", "handle_event", halt)
        await session.repo.dispatch_event("g", "boom", [], {})
        assert render_queries.capture() is before
        component.detach_hook("stop")
        # A handler that raises: the session recovers (_crashed)
        await session.handle_message(
            {
                "command": "user_event",
                "payload": {"id": "g", "command": "boom", "implicit_args": {}, "explicit_args": {}},
            }
        )
        assert render_queries.capture() is before

        # A task cancelled inside a scope leaves its context as it found it
        import contextvars

        context = contextvars.copy_context()
        entered = asyncio.Event()

        async def stuck() -> None:
            with render_queries.scope("task", None, "stuck"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.get_running_loop().create_task(stuck(), context=context)
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert context[render_queries._state] is before


# -- a name built only when something collects (review of #182) --------------------------------


class _Loud:
    """A coroutine that refuses to be printed: building a diagnostic name from it would raise."""

    def __init__(self, inner):
        self.inner = inner

    def __await__(self):
        return self.inner.__await__()

    def send(self, value):
        return self.inner.send(value)

    def throw(self, *args):
        return self.inner.throw(*args)

    def close(self):
        return self.inner.close()

    def __str__(self):
        raise RuntimeError("printed")

    __repr__ = __format__ = __str__  # type: ignore[assignment]


async def _forty_two() -> int:
    return 42


@pytest.mark.asyncio
async def test_while_collecting_the_task_is_named_by_its_type(quiz):
    view = await mount(RqWorker, id="w")
    async with view.queries() as q:
        result = await view.component.assign_async(_Loud(_answer_with_a_query()))
        await eventually(lambda: result.ok)
    assert result.result == 42
    assert render_of(q.rows[0]) == "task RqWorker#w assign_async(_Loud)"


async def _answer_with_a_query() -> int:
    await Choice.objects.acount()
    return 42


OFF_ASSIGN = """
from wireview import Component
from wireview.core.meta import WireviewMeta
from django.contrib.auth.models import AnonymousUser
import collections.abc

class Loud(collections.abc.Coroutine):
    def __init__(self, inner): self.inner = inner
    def __await__(self): return self.inner.__await__()
    def send(self, value): return self.inner.send(value)
    def throw(self, *args): return self.inner.throw(*args)
    def close(self): return self.inner.close()
    def __str__(self): raise RuntimeError("printed")
    __repr__ = __format__ = __str__

class Off(Component, public=False):
    class Meta:
        template_name = "x.html"

async def answer():
    return 42

async def main():
    component = Off(id="o", user=AnonymousUser(), wire=WireviewMeta(params={}))
    result = await component.assign_async(Loud(answer()))
    for _ in range(100):
        if not result.loading:
            break
        await asyncio.sleep(0.01)
    print("result", result.state, result.result)

asyncio.run(main())
print("installed", render_queries.installed())
"""


@pytest.mark.integration
def test_off_assign_async_does_not_print_the_users_coroutine():
    out = _run(OFF_ASSIGN)
    assert out == ["off False False", "result success 42", "installed False"]
