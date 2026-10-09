"""Render-part SQL written for the editor (#188, docs/design/render-queries-editor.md).

The record is ``wireview.debug.render_queries_file``'s; the attribution under it is
``render_queries``' (tests/test_render_queries.py). What is checked here is what a
reader of the file relies on:

- the line: format 1.0, one snapshot per render that ended inside the work (rows
  or none), each row pointing at its render, ``count`` the whole
- the digest of the source the *running* template was compiled from, and only
  for the loaders that make an origin per compile; a property's ``def`` line and
  ``stat``, as strings
- what is written and what is not: renders only, a class's unchanged snapshot not
  again within a minute, nothing unless every condition holds
- the byte limit, the render limit and the integrity of ``by``
- segments, sweeping, permissions, threads, a short write
"""

from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
import sys
import threading
import typing as t
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from django.template import Context, Engine, Origin, Template, engines
from django.template.loaders import app_directories, filesystem
from django.test import override_settings
from testproj.wireview_setting import set_wireview

import wireview
from examples.quiz.models import Choice, Question, Quiz
from wireview import Component, LiveComponent, function_component, mount
from wireview.debug import render_queries, render_queries_file
from wireview.debug.render_queries_file import digest_of

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

# -- templates -------------------------------------------------------------------------------

TEMPLATES = {
    "rqf/base.html": (
        "{% load wireview %}<div {% tag_header %}>\n"  # 1
        "{% block body %}\n"  # 2
        "{{ questions.count }}\n"  # 3
        "{% endblock %}\n"  # 4
        "</div>"  # 5
    ),
    "rqf/child.html": (
        '{% extends "rqf/base.html" %}\n'  # 1
        "{% load wireview %}\n"  # 2
        "{% block body %}\n"  # 3
        "{{ block.super }}\n"  # 4
        "{{ choices.count }}\n"  # 5
        '{% include "rqf/inc.html" %}\n'  # 6
        '{% func "rqfcard" items=choices %}\n'  # 7
        '{% component "RqfQuiet" id="quiet" %}\n'  # 8
        "{% endblock %}"  # 9
    ),
    "rqf/inc.html": "<p>\n{{ choices.last.text }}\n</p>",
    "rqf/card.html": "<ul>\n{{ items.exists }}\n</ul>",
    "rqf/quiet.html": "{% load wireview %}<p {% tag_header %}>quiet</p>",
    "rqf/shelf.html": '{% load wireview %}<main {% tag_header %}>\n{% component "RqfBook" id="book" %}\n</main>',
    "rqf/book.html": (
        "{% load wireview %}<ol {% tag_header %}>\n{% for c in choices %}{{ c.question.text }}{% endfor %}\n</ol>"
    ),
    "rqf/frame.html": (
        "{% load wireview %}<section {% tag_header %}>\n"  # 1
        '{% live_component_block "RqfList" id="list" %}{% fill row let:c %}\n'  # 2
        "{{ c.question.text }}\n"  # 3
        "{% endfill %}{% endlive_component %}\n"  # 4
        "</section>"  # 5
    ),
    "rqf/list.html": (
        "{% load wireview %}<ul {% live_tag_header %}>"
        '{% for c in choices %}{% render_slot "row" c=c %}{% endfor %}</ul>'
    ),
    "rqf/props.html": "{% load wireview %}<p {% tag_header %}></p>",
    "rqf/plain.html": "{% load wireview %}<p {% tag_header %}>\n{{ choices.count }}\n</p>",
    "rqf/rack.html": (
        '{% load wireview %}<main {% tag_header %}>{% for k in keys %}{% live_component "RqfSibling" id=k %}'
        "{% endfor %}</main>"
    ),
    "rqf/sibling.html": "{% load wireview %}<p {% live_tag_header %}>\n{{ questions.first.text }}\n</p>",
}


@pytest.fixture
def template_dir(tmp_path: Path, settings: t.Any) -> Path:
    # Through pytest-django's settings like every other change here: an override_settings
    # entered in a fixture, with settings changed after it, unwinds out of order and leaves
    # these templates behind for the tests that follow
    root = tmp_path / "templates"
    for name, text in TEMPLATES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(root)],
            "OPTIONS": {
                "loaders": [("django.template.loaders.cached.Loader", ["django.template.loaders.filesystem.Loader"])]
            },
        }
    ]
    return root


@pytest.fixture
def sink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: t.Any, template_dir: Path) -> Path:
    """Writing on, to a directory of the test's: what a dev server has."""
    out = tmp_path / "out"
    monkeypatch.delenv(render_queries_file.ENVIRONMENT, raising=False)
    monkeypatch.setattr(render_queries_file, "_suppressed", False)
    settings.DEBUG = True
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True, DEBUG_RENDER_QUERIES_DIR=str(out))
    render_queries_file._reset()
    yield out
    render_queries_file._reset()


def lines(out: Path) -> list[dict[str, t.Any]]:
    found = []
    for path in sorted(out.glob("*.jsonl"), key=lambda p: (p.name.rsplit(".", 2)[0], int(p.name.rsplit(".", 2)[1]))):
        for text in path.read_text(encoding="utf-8").splitlines():
            found.append(json.loads(text))
    return found


def rows_of(record: dict[str, t.Any], component: str) -> list[dict[str, t.Any]]:
    renders = record["renders"]
    return [row for row in record["rows"] if "by" in row and renders[row["by"]]["component"].endswith(component)]


def assert_whole(record: dict[str, t.Any]) -> None:
    """``count`` is every statement: the rows kept and those the limits cut."""
    more = record.get("more", {}).get("statements", 0)
    assert record["count"] == sum(row["count"] for row in record["rows"]) + more
    for row in record["rows"]:
        if "by" in row:
            assert 0 <= row["by"] < len(record["renders"]), row


@pytest.fixture
def quiz(db):
    quiz = Quiz.objects.create(title="Q")
    for n in range(3):
        Choice.objects.create(question=Question.objects.create(quiz=quiz, text=f"q{n}"), text=f"c{n}")
    return quiz


# -- components ------------------------------------------------------------------------------


class _RqfLists:
    @property
    def questions(self):
        return Question.objects.all()

    @property
    def choices(self):
        return Choice.objects.all()


class RqfPage(_RqfLists, Component):
    class Meta:
        template_name = "rqf/child.html"

    @property
    def total(self) -> int:
        return Choice.objects.count()


class RqfQuiet(Component):
    class Meta:
        template_name = "rqf/quiet.html"


@function_component(name="rqfcard", template="rqf/card.html")
def rqfcard(items: t.Any = None):
    return {"items": items}


class RqfFrame(Component):
    class Meta:
        template_name = "rqf/frame.html"


class RqfList(LiveComponent):
    class Meta:
        template_name = "rqf/list.html"
        slots = {"row": {}}

    @property
    def choices(self):
        return Choice.objects.all()


class RqfShelf(Component):
    class Meta:
        template_name = "rqf/shelf.html"


class RqfBook(Component):
    class Meta:
        template_name = "rqf/book.html"

    empty: bool = False

    @property
    def choices(self):
        return Choice.objects.none() if self.empty else Choice.objects.all()


class RqfRack(Component):
    class Meta:
        template_name = "rqf/rack.html"

    keys: list[str] = ["s1", "s2", "s3"]


class RqfSibling(LiveComponent):
    class Meta:
        template_name = "rqf/sibling.html"

    @property
    def questions(self):
        return Question.objects.all()


class RqfFeed(Component):
    class Meta:
        template_name = "rqf/plain.html"
        subscriptions = {"rqf-feed"}


class RqfPlain(Component):
    class Meta:
        template_name = "rqf/plain.html"

    async def bump(self):
        await Choice.objects.acount()

    @property
    def choices(self):
        return Choice.objects.all()


def _decorated(function):
    @functools.wraps(function)
    def wrapper(*args):
        return function(*args)

    return wrapper


def _aliased_getter(self) -> int:
    return Choice.objects.count()


class _RqfInherited:
    @property
    def total(self) -> int:
        return Choice.objects.count()


class RqfProps(_RqfInherited, Component):
    class Meta:
        template_name = "rqf/props.html"

    @property
    @_decorated
    def multi(self) -> int:
        return Choice.objects.count()

    @functools.cached_property
    def cached(self) -> int:
        return Choice.objects.count()

    @property
    async def later(self) -> int:
        return await Choice.objects.acount()

    aliased = property(_aliased_getter)


# -- stand-ins for components where only the scope is under test ---------------------------------


def stand_in(name: str, id: str = "x", module: str = __name__, qualname: str | None = None) -> t.Any:
    """What a scope reads of a component: its type, ``_name`` and ``id``."""
    cls = type(qualname or name, (), {"__module__": module, "__qualname__": qualname or name})
    item = cls()
    item._name = name
    item.id = id
    return item


def run(sql: str = "SELECT 1") -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)


async def _join(cls: type[Component], id: str) -> None:
    """A real connection's join: the session renders LiveComponents after their parent."""
    from uuid import uuid4

    from django.contrib.auth.models import AnonymousUser
    from testproj.outbound import RecordingOutbound

    from wireview.core.meta import WireviewMeta
    from wireview.core.rendered import PROTOCOL_VERSION
    from wireview.core.session import SessionView
    from wireview.core.state import sign_state
    from wireview.session import WireviewSession

    session = WireviewSession(RecordingOutbound(), user=AnonymousUser(), channel_name=f"rqf-{uuid4().hex[:8]}")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    state = await sync_to_async(sign_state)(cls(id=id, user=AnonymousUser(), wire=WireviewMeta(params={})))
    await session.handle_message({"command": "join", "payload": {"name": cls._name, "state": state}})


# -- the line --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_render_is_one_line_of_snapshots_with_the_running_source(sink, quiz, template_dir, settings):
    settings.BASE_DIR = template_dir.parent
    view = await mount(RqfPage, id="page")
    await sync_to_async(view.render)()
    [record] = lines(sink)
    assert record["version"] == render_queries_file.VERSION
    assert record["kind"] == "render" and record["detail"] == "http"
    assert [(r["name"], r["id"], r["why"]) for r in record["renders"]] == [
        ("RqfPage", "page", "http"),
        ("RqfQuiet", "quiet", "nested"),  # no rows: still a snapshot, of none
    ]
    assert record["renders"][0]["component"] == f"{__name__}.RqfPage"
    assert_whole(record)
    assert not rows_of(record, ".RqfQuiet")

    placed = {}
    for row in rows_of(record, ".RqfPage"):
        if "template" in row:
            template = row["template"]
            placed[(template["name"], template["line"])] = template
    assert set(placed) == {
        ("rqf/base.html", 3),  # block.super
        ("rqf/child.html", 5),  # the child's block
        ("rqf/inc.html", 2),  # an include
        ("rqf/card.html", 2),  # a function component's template
    }
    for (name, line), template in placed.items():
        file = Path(template["file"])
        assert file == (template_dir / name).resolve()
        assert template["source"] == digest_of(file.read_text())
        assert template["text"] in file.read_text().splitlines()[line - 1]
        assert template["rel"] == f"templates/{name}"  # under BASE_DIR, set to the test's directory below
    props = [row["property"] for row in rows_of(record, ".RqfPage") if "property" in row]
    assert {prop["name"] for prop in props} == {"total"}  # the querysets run where the template reads them


@pytest.mark.asyncio
async def test_a_template_the_cache_still_runs_is_told_with_its_old_source(sink, quiz, template_dir):
    file = template_dir / "rqf/plain.html"
    old = file.read_text()
    view = await mount(RqfPlain, id="plain")
    await sync_to_async(view.render)()
    # Another line changes on disk; the cached compile is what runs
    file.write_text(old.replace("<p ", "<p data-new ", 1))
    await sync_to_async(Choice.objects.create)(question=await Question.objects.afirst(), text="more")
    await sync_to_async(view.render)()
    stale = lines(sink)[-1]
    [row] = [row for row in stale["rows"] if "template" in row]
    assert row["template"]["source"] == digest_of(old)
    assert row["template"]["source"] != digest_of(file.read_text())

    for loader in engines["django"].engine.template_loaders:
        loader.reset()  # what the autoreloader's template_changed does
    view = await mount(RqfPlain, id="plain")
    await sync_to_async(view.render)()
    [row] = [row for row in lines(sink)[-1]["rows"] if "template" in row]
    assert row["template"]["source"] == digest_of(file.read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["http", "live"])
async def test_a_let_slot_a_live_component_draws_later_is_told_with_a_source_only_when_one_is_on_the_stack(
    sink, quiz, template_dir, path
):
    if path == "http":
        view = await mount(RqfFrame, id="frame")
        await sync_to_async(view.render)()
    else:
        await _join(RqfFrame, "frame")
    rows = [row for record in lines(sink) for row in record["rows"] if row.get("template", {}).get("line") == 3]
    assert rows, lines(sink)
    for row in rows:
        assert row["template"]["name"] == "rqf/frame.html"
        # The filler's Template is on the stack (its own pass) or the digest is left out: never a guess
        if "source" in row["template"]:
            assert row["template"]["source"] == digest_of((template_dir / "rqf/frame.html").read_text())
    told = {"source" in row["template"] for row in rows}
    # HTTP draws the child inside the frame's pass; live, the child renders after it, on its own
    assert told == ({True} if path == "http" else {False})


def _engine_loader(engine: Engine, kind: type) -> t.Any:
    return kind(engine)


def test_lookup_finds_the_running_template_not_another_of_the_same_origin(sink, quiz, template_dir):
    """Pure lookup and the per-Template cache: two Templates share one origin (2판 리뷰 3)."""
    path = template_dir / "rqf/shared.html"
    old = "{% with value=old %}\n{{ value }}\n{% endwith %}"
    new = "{% with value=new %}\n{{ value }}\n{% endwith %}"
    path.write_text(new)
    engine = engines["django"].engine
    origin = Origin(str(path), template_name="rqf/shared.html", loader=filesystem.Loader(engine))
    stale = Template(old, origin=origin, engine=engine)
    fresh = Template(new, origin=origin, engine=engine)

    class Query:
        def __init__(self, sql: str) -> None:
            self.sql = sql

        def __str__(self) -> str:
            run(self.sql)
            return ""

    page = stand_in("Shared")
    with render_queries.scope("render", page, "http"):
        fresh.render(Context({"new": Query("SELECT 'fresh'"), "old": Query("SELECT 'x'")}))
        stale.render(Context({"new": Query("SELECT 'y'"), "old": Query("SELECT 'stale'")}))
    [record] = lines(sink)
    sources = {row["sql"]: row["template"]["source"] for row in record["rows"]}
    assert sources["SELECT 'fresh'"] == digest_of(new)
    assert sources["SELECT 'stale'"] == digest_of(old)  # not the digest the fresh one left on the origin


class _SubclassLoader(filesystem.Loader):
    pass


@pytest.mark.parametrize(
    ("loader", "told"),
    [
        (filesystem.Loader, True),
        (app_directories.Loader, True),
        (_SubclassLoader, False),  # may make its origins another way
        (None, False),  # a Template made directly, no loader
    ],
)
def test_the_loader_gate_tells_the_source_only_for_the_loaders_that_make_an_origin_per_compile(
    sink, quiz, template_dir, loader, told
):
    path = template_dir / "rqf/gate.html"
    text = "-\n{{ value }}"
    path.write_text(text)
    engine = engines["django"].engine
    origin = Origin(str(path), template_name="rqf/gate.html", loader=loader(engine) if loader else None)
    template = Template(text, origin=origin, engine=engine)

    class Query:
        def __str__(self) -> str:
            run()
            return ""

    with render_queries.scope("render", stand_in("Gate"), "http"):
        template.render(Context({"value": Query()}))
    [record] = lines(sink)
    [row] = record["rows"]
    assert ("source" in row["template"]) is told
    assert row["template"]["line"] == 2


@pytest.mark.asyncio
async def test_a_locmem_template_has_no_file_and_no_source(sink, quiz, monkeypatch):
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        view = await mount(RqfPlain, id="plain")
        await sync_to_async(view.render)()
    [row] = [row for row in lines(sink)[0]["rows"] if "template" in row]
    assert "file" not in row["template"] and "source" not in row["template"]
    assert row["template"]["name"] == "rqf/plain.html" and row["template"]["line"] == 2


@pytest.mark.asyncio
async def test_the_source_is_the_same_under_the_test_environment_and_outside_it(sink, quiz, monkeypatch):
    from django.test.utils import _TestState

    view = await mount(RqfPlain, id="plain")
    await sync_to_async(view.render)()
    inside = [row for row in lines(sink)[-1]["rows"] if "template" in row][0]["template"]["source"]
    assert Template._render.__name__ == "instrumented_test_render"

    monkeypatch.setattr(Template, "_render", _TestState.saved_data.template_render)
    await sync_to_async(Choice.objects.create)(question=await Question.objects.afirst(), text="more")
    view = await mount(RqfPlain, id="plain2")
    await sync_to_async(view.render)()
    outside = [row for row in lines(sink)[-1]["rows"] if "template" in row][0]["template"]["source"]
    assert inside == outside


# -- a property's place ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_property_is_told_at_its_def_line_with_its_stat(sink, quiz):
    view = await mount(RqfProps, id="props")
    await view.render_diff()
    [record] = lines(sink)
    props = {row["property"]["name"]: row["property"] for row in record["rows"] if "property" in row}
    assert set(props) >= {"total", "multi", "cached", "later", "aliased"}
    for name in ("total", "multi", "cached", "later"):
        prop = props[name]
        text = Path(prop["file"]).read_text().splitlines()[prop["line"] - 1]
        assert text.strip().startswith(("def " + name, "async def " + name)), (name, text)
        stat = os.stat(prop["file"])
        assert prop["stat"] == [str(stat.st_mtime_ns), str(stat.st_size)]  # strings: exact past 2**53
    assert props["total"]["owner"] == f"{__name__}._RqfInherited"
    assert props["later"]["async"] is True and props["total"]["async"] is False
    assert "line" not in props["aliased"]  # property(f): no def of that name


@pytest.mark.asyncio
async def test_a_property_file_edited_after_import_has_no_place(sink, quiz, monkeypatch):
    monkeypatch.setattr(wireview, "_IMPORTED_AT_NS", 0)
    view = await mount(RqfProps, id="props")
    await view.render_diff()
    props = [row["property"] for row in lines(sink)[0]["rows"] if "property" in row]
    assert props and all("line" not in prop and "stat" not in prop for prop in props)


# -- snapshots: what is written and when -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_component_fixed_alone_clears_what_it_ran_inside_another(sink, quiz):
    """The sequence of 1판 리뷰 3: B inside A runs 6, B alone runs 6, then B alone runs none."""
    shelf = await mount(RqfShelf, id="shelf")
    await sync_to_async(shelf.render)()
    alone = await mount(RqfBook, id="book")
    await sync_to_async(alone.render)()
    fixed = await mount(RqfBook, id="book", empty=True)
    await sync_to_async(fixed.render)()

    records = lines(sink)
    assert [r["name"] for r in records[0]["renders"]] == ["RqfShelf", "RqfBook"]
    assert sum(row["count"] for row in rows_of(records[0], ".RqfBook")) == 4  # the loop and three questions
    # B alone with the same rows is the same snapshot: not written again within a minute
    assert len(records) == 2
    assert [r["name"] for r in records[1]["renders"]] == ["RqfBook"]
    assert records[1]["rows"] == [] and records[1]["count"] == 0


@pytest.mark.asyncio
async def test_sibling_live_components_are_one_line_and_their_statement_is_repeated(sink, quiz):
    """#189: a render and the LiveComponents it names are one piece of work, so one line."""
    await _join(RqfRack, "rack")
    [record] = lines(sink)
    assert record["kind"] == "tree" and record["detail"] == "join"
    assert [(r["name"], r["id"], r["why"]) for r in record["renders"]] == [
        ("RqfRack", "rack", "join"),
        ("RqfSibling", "s1", "join"),
        ("RqfSibling", "s2", "join"),
        ("RqfSibling", "s3", "join"),
    ]
    assert_whole(record)
    rows = rows_of(record, ".RqfSibling")
    # One row per render, as ever: a reader sums a class's rows (the snapshot of a class drawn many times)
    assert sorted(row["by"] for row in rows) == [1, 2, 3]
    assert {(row["template"]["line"], row["count"], row.get("repeated")) for row in rows} == {(2, 1, True)}
    assert len({row["sql"] for row in rows}) == 1


@pytest.mark.asyncio
async def test_an_unchanged_snapshot_waits_a_minute_and_a_new_source_does_not(sink, quiz, monkeypatch, template_dir):
    now = [1000.0]
    monkeypatch.setattr(render_queries_file, "_clock", lambda: now[0])
    view = await mount(RqfPlain, id="plain")
    await sync_to_async(view.render)()
    await sync_to_async(view.render)()
    assert len(lines(sink)) == 1

    now[0] += render_queries_file.REPEAT_WINDOW
    await sync_to_async(view.render)()
    assert len(lines(sink)) == 2

    # The file changes elsewhere, the rows do not: the digest is part of the snapshot (2판 리뷰 7)
    file = template_dir / "rqf/plain.html"
    file.write_text(file.read_text().replace("<p ", "<p data-new ", 1))
    for loader in engines["django"].engine.template_loaders:
        loader.reset()
    view = await mount(RqfPlain, id="plain")
    await sync_to_async(view.render)()
    records = lines(sink)
    assert len(records) == 3
    [row] = [row for row in records[-1]["rows"] if "template" in row]
    assert row["template"]["source"] == digest_of(file.read_text())


def test_work_with_no_render_is_not_written_and_its_rows_in_a_render_have_no_by(sink, db):
    with render_queries.scope("handler", stand_in("Alone"), "save"):
        run()
    with render_queries.scope("handler", stand_in("Alone"), "empty"):
        pass
    assert lines(sink) == []

    with render_queries.scope("handler", stand_in("Host"), "save"):
        run("SELECT 'handler'")
        with render_queries.scope("render", stand_in("Drawn"), "nested"):
            run("SELECT 'render'")
    [record] = lines(sink)
    by_sql = {row["sql"]: row for row in record["rows"]}
    assert "by" not in by_sql["SELECT 'handler'"]
    assert by_sql["SELECT 'render'"]["by"] == 0
    assert record["kind"] == "handler" and [r["name"] for r in record["renders"]] == ["Drawn"]


@pytest.mark.asyncio
async def test_a_broadcast_item_is_not_written(sink, quiz):
    from wireview import Broadcast

    choice = await Choice.objects.afirst()
    await Broadcast(RqfFeed, "rqf-feed").stream_insert("items", choice, template="rqf/inc.html").asend()
    assert lines(sink) == []


def test_two_classes_of_one_qualname_in_two_modules_are_two_components(sink, db):
    with render_queries.scope("render", stand_in("Same", module="app_one.live", qualname="Same"), "http"):
        run()
    with render_queries.scope("render", stand_in("Same", module="app_two.live", qualname="Same"), "http"):
        run()
    assert [r["renders"][0]["component"] for r in lines(sink)] == ["app_one.live.Same", "app_two.live.Same"]


# -- conditions ------------------------------------------------------------------------------


def _render_plain() -> None:
    with render_queries.scope("render", stand_in("Plain"), "http"):
        run()


@pytest.mark.parametrize(
    "turn_off",
    [
        "setting",
        "block",
        "debug",
        "environment",
        "testing",
        "directory",
        "no base dir",
    ],
)
def test_nothing_is_written_unless_every_condition_holds(sink, db, monkeypatch, settings, turn_off):
    if turn_off == "setting":
        set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=False)
    elif turn_off == "debug":
        settings.DEBUG = False
    elif turn_off == "environment":
        monkeypatch.setenv(render_queries_file.ENVIRONMENT, "off")
    elif turn_off == "testing":
        monkeypatch.setattr(render_queries_file, "_suppressed", True)
    elif turn_off == "directory":
        set_wireview(monkeypatch, DEBUG_RENDER_QUERIES_DIR=False)
    elif turn_off == "no base dir":
        set_wireview(monkeypatch, DEBUG_RENDER_QUERIES_DIR=None)
        del settings.BASE_DIR
    if turn_off == "block":
        with render_queries.Queries() as q:
            _render_plain()
        assert q.count == 1  # the test's block saw it; the editor's file did not
    else:
        _render_plain()
    assert not sink.exists() or lines(sink) == []


def test_the_default_directory_is_under_base_dir(sink, db, monkeypatch, settings, tmp_path):
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES_DIR=None)
    settings.BASE_DIR = tmp_path / "project"
    _render_plain()
    assert lines(tmp_path / "project" / ".wireview" / "render-queries")


def test_importing_wireview_testing_suppresses_writing():
    import wireview.testing  # noqa: F401 -- this suite imports it already

    assert render_queries_file._suppressed is True


def test_a_locmem_mail_backend_does_not_turn_writing_off(sink, db):
    from django.core.mail.backends.locmem import EmailBackend

    EmailBackend()  # makes django.core.mail.outbox, as in a dev server with locmem mail
    _render_plain()
    assert len(lines(sink)) == 1


@pytest.mark.asyncio
async def test_under_the_suite_a_mounted_render_with_debug_on_writes_nothing(
    quiz, settings, tmp_path, monkeypatch, template_dir
):
    """The suite as it is: wireview.testing imported, the variable set. Neither alone lets it through."""
    settings.DEBUG = True
    set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=True, DEBUG_RENDER_QUERIES_DIR=str(tmp_path / "out"))
    render_queries_file._reset()
    view = await mount(RqfPlain, id="plain")
    await view.render_diff()
    await sync_to_async(view.render)()
    monkeypatch.delenv(render_queries_file.ENVIRONMENT)
    await view.render_diff()  # still suppressed by wireview.testing
    assert not (tmp_path / "out").exists()


# -- limits ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT '{}'",
        "SELECT '가나다라{}'",  # several UTF-8 bytes a character
        "SELECT '\"\\\\\"{}'",  # escaped in JSON
    ],
)
def test_a_line_is_cut_to_its_byte_limit_and_still_counts_everything(sink, db, statement):
    with render_queries.scope("render", stand_in("Big"), "http"):
        for n in range(200):
            run(statement.format(f"{n:04d}" + "x" * 990))
    [record] = lines(sink)
    raw = next(sink.glob("*.jsonl")).read_bytes()
    assert len(raw.splitlines()[0]) + 1 <= render_queries_file.LINE_LIMIT
    assert record["more"]["groups"] + len(record["rows"]) == 200
    assert record["count"] == 200
    assert_whole(record)
    assert record["partial"] == [f"{__name__}.Big"]
    assert all(len(row["sql"]) <= render_queries_file.SQL_WIDTH for row in record["rows"])


def test_a_line_that_cannot_fit_without_rows_is_not_written(sink, db):
    with render_queries.scope("render", stand_in("Huge", id="i" * 1000), "http"):
        for n in range(150):
            with render_queries.scope("render", stand_in("Huge", id=f"{n}" + "i" * 1000), "nested"):
                pass
    assert lines(sink) == []


def test_past_the_render_limit_every_by_points_at_a_kept_render(sink, db):
    """3판 리뷰 P3: renders cut at the limit, rows ordered and cut by bytes, one class on both sides."""
    with render_queries.scope("render", stand_in("Host", id="host"), "http"):
        for n in range(render_queries_file.RENDERS_LIMIT + 20):
            with render_queries.scope("render", stand_in("Row", id=f"r{n}"), "nested"):
                run(f"SELECT {n} /* " + "y" * 900 + " */")
                if n % 7 == 0:
                    for _ in range(3):
                        run(f"SELECT 'again {n}'")
    [record] = lines(sink)
    assert len(record["renders"]) == render_queries_file.RENDERS_LIMIT
    assert record["renders_more"] == 21  # 121 renders: the host and 120 rows
    assert_whole(record)
    assert f"{__name__}.Row" in record["partial"]  # kept and cut: never a whole snapshot
    flags = [row.get("repeated", False) for row in record["rows"]]
    assert flags == sorted(flags, reverse=True)  # repeats first


# -- files -----------------------------------------------------------------------------------


def test_a_full_segment_gives_way_to_the_next_and_the_one_before_the_last_goes(sink, db, monkeypatch):
    monkeypatch.setattr(render_queries_file, "SEGMENT_LIMIT", 600)
    for n in range(8):
        with render_queries.scope("render", stand_in(f"Seg{n}"), "http"):
            run(f"SELECT {n}")
    names = sorted(path.name for path in sink.glob("*.jsonl"))
    segments = sorted(int(name.rsplit(".", 2)[1]) for name in names)
    assert len(segments) == 2 and segments[1] == segments[0] + 1
    records = lines(sink)
    assert [r["segment"] for r in records] == sorted(r["segment"] for r in records)


def test_a_removed_segment_is_not_written_again_under_its_name(sink, db):
    _render_plain()
    [first] = list(sink.glob("*.jsonl"))
    first.unlink()
    with render_queries.scope("render", stand_in("Other"), "http"):
        run("SELECT 2")
    [second] = list(sink.glob("*.jsonl"))
    assert second.name != first.name
    assert lines(sink)[0]["segment"] == int(first.name.rsplit(".", 2)[1]) + 1


def test_other_processes_files_are_swept_only_when_idle(sink, db):
    import time

    sink.mkdir(parents=True)
    now = time.time()

    def other(name: str, age: float) -> Path:
        path = sink / f"{name}.1.jsonl"
        path.write_text("{}\n")
        os.utime(path, (now - age, now - age))
        return path

    fresh = other("20260101T000000-1", 60)
    expired = other("20260101T000000-2", render_queries_file.EXPIRE + 60)
    idle = [other(f"20260101T000000-{100 + n}", render_queries_file.IDLE + 60 + n) for n in range(40)]
    _render_plain()
    assert fresh.exists()
    assert not expired.exists()
    left = [path for path in idle if path.exists()]
    assert len(left) == render_queries_file.KEEP
    assert left == idle[: render_queries_file.KEEP]  # the newest stay


def test_two_processes_sweeping_at_once_do_not_fail(tmp_path):
    import time

    out = tmp_path / "out"
    out.mkdir()
    for n in range(300):
        path = out / f"20250101T000000-{n}.1.jsonl"
        path.write_text("{}\n")
        old = time.time() - render_queries_file.EXPIRE - 60
        os.utime(path, (old, old))
    code = (
        "import sys; from pathlib import Path\n"
        "from wireview.debug.render_queries_file import _Writer\n"
        "w = _Writer(Path(sys.argv[1])); w._open(); w.close()\n"
    )
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "tests")])}
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(out)], env=env, stderr=subprocess.PIPE) for _ in range(2)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err.decode()
    assert len(list(out.glob("2025*.jsonl"))) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_directory_is_private_and_ignored(sink, db):
    _render_plain()
    assert (sink / ".gitignore").read_text() == "*\n"
    assert sink.stat().st_mode & 0o777 == 0o700
    [path] = list(sink.glob("*.jsonl"))
    assert path.stat().st_mode & 0o777 == 0o600


def test_threads_writing_at_once_leave_whole_lines(sink, db):
    from django.db import connections

    def work(n: int) -> None:
        try:
            for m in range(20):
                with render_queries.scope("render", stand_in(f"T{n}", id=f"{m}"), "http"):
                    run(f"SELECT {n * 100 + m} /* " + "z" * (n * 500) + " */")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    records = lines(sink)  # every line parses
    assert len(records) == 8 * 20


@pytest.mark.parametrize("failure", ["short then nothing", "part then error"])
def test_a_short_write_is_taken_back_and_turns_writing_off(sink, db, monkeypatch, caplog, failure):
    _render_plain()
    real = os.write
    calls = []

    def write(fd: int, data: t.Any) -> int:
        calls.append(len(data))
        if failure == "short then nothing":
            return real(fd, bytes(data[:10])) if len(calls) == 1 else 0
        real(fd, bytes(data[:10]))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(render_queries_file, "_write", write)
    with caplog.at_level(logging.WARNING, logger="wireview.queries"):
        with render_queries.scope("render", stand_in("Second"), "http"):
            run("SELECT 'second'")  # the render goes on
        monkeypatch.setattr(render_queries_file, "_write", real)
        with render_queries.scope("render", stand_in("Third"), "http"):
            run("SELECT 'third'")
    records = lines(sink)  # whole lines only: the half line was taken back
    assert [r["renders"][0]["name"] for r in records] == ["Plain"]
    warnings = [r for r in caplog.records if "no longer written" in r.getMessage()]
    assert len(warnings) == 1


# -- review A1: failures, published snapshots, late suppression ----------------------------------


def test_a_digest_that_cannot_be_made_is_left_out_and_the_statement_still_runs(sink, db, tmp_path):
    """Finding the source runs before the statement does: it must not keep it from running."""
    root = tmp_path / "escaped"
    root.mkdir()
    (root / "escaped.html").write_text(r"{# \ud800 #}{{ value }}", encoding="ascii")
    # A lone surrogate in the source: it cannot be encoded to hash
    engine = Engine(
        dirs=[str(root)], file_charset="unicode_escape", loaders=["django.template.loaders.filesystem.Loader"]
    )
    template = engine.get_template("escaped.html")

    class Query:
        def __str__(self) -> str:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return str(cursor.fetchone()[0])

    with render_queries.scope("render", stand_in("Escaped"), "http"):
        assert template.render(Context({"value": Query()})) == "1"
    [record] = lines(sink)
    [row] = record["rows"]
    assert row["template"]["name"] == "escaped.html" and "source" not in row["template"]


def _render(name: str, outer: t.Any = None) -> t.Any:
    return render_queries.Scope("render", stand_in(name), "http", outer, sink=True)


def _run(scope: t.Any, sql: str, times: int = 1) -> None:
    for _ in range(times):
        row = render_queries.Row(sql, scope)
        row.render = scope
        scope.rows.append(row)


def _host(big: int) -> t.Any:
    """A Host line where B's repeated statements push A's one statement past the byte limit."""
    host = _render("Host")
    a = _render("A", host)
    b = _render("B", host)
    _run(a, "SELECT 'a'")
    for n in range(big):
        _run(b, f"SELECT {n} /* " + "x" * 980 + " */", 3)
    host.renders = [b, a]
    host.rows = b.rows + a.rows
    return host


def test_a_class_cut_short_by_bytes_is_published_again_when_seen_whole(sink):
    render_queries_file.emit(_host(150))
    [cut] = lines(sink)
    assert f"{__name__}.A" in cut["partial"] and not rows_of(cut, ".A")

    alone = _render("A")
    _run(alone, "SELECT 'a'")
    render_queries_file.emit(alone)  # the same rows as before the cut: new to a reader
    records = lines(sink)
    assert len(records) == 2
    assert [row["sql"] for row in records[1]["rows"]] == ["SELECT 'a'"] and "partial" not in records[1]

    again = _render("A")
    _run(again, "SELECT 'a'")
    render_queries_file.emit(again)  # now the reader holds it whole
    assert len(lines(sink)) == 2


def test_the_same_work_cut_the_same_way_is_not_written_again(sink):
    render_queries_file.emit(_host(150))
    render_queries_file.emit(_host(150))
    assert len(lines(sink)) == 1


def test_what_another_class_takes_of_the_line_decides_what_is_published(sink):
    """A's rows are kept or cut by B's size: what was published is what a repeat is measured against."""
    render_queries_file.emit(_host(150))
    render_queries_file.emit(_host(5))  # B smaller: A fits
    records = lines(sink)
    assert len(records) == 2 and rows_of(records[1], ".A") and "partial" not in records[1]

    alone = _render("A")
    _run(alone, "SELECT 'a'")
    render_queries_file.emit(alone)  # the reader holds A whole already
    assert len(lines(sink)) == 2


@pytest.mark.parametrize("statements", [1, 0])
@pytest.mark.parametrize("turn_off", ["environment", "testing", "debug", "setting", "block"])
def test_writing_turned_off_while_the_work_runs_holds_when_it_ends(
    sink, db, monkeypatch, settings, turn_off, statements
):
    with render_queries.scope("render", stand_in("Late"), "http"):
        for _ in range(statements):
            run()
        if turn_off == "environment":
            monkeypatch.setenv(render_queries_file.ENVIRONMENT, "off")
        elif turn_off == "testing":
            monkeypatch.setattr(render_queries_file, "_suppressed", True)
        elif turn_off == "debug":
            settings.DEBUG = False
        elif turn_off == "setting":
            set_wireview(monkeypatch, DEBUG_RENDER_QUERIES=False)
        else:
            with render_queries.Queries() as q:  # a test's block opened inside the work makes it the test's
                run()
            assert q.count == 1
    assert not sink.exists() or lines(sink) == []


class _Gate:
    """The writer's lock, with a thread named ``waiting`` saying it is about to take it."""

    def __init__(self, lock: t.Any, arrived: threading.Event, resume: threading.Event | None = None) -> None:
        self.lock = lock
        self.arrived = arrived
        self.resume = resume

    def __enter__(self) -> None:
        if threading.current_thread().name == "waiting":
            self.arrived.set()
            if self.resume is not None:
                assert self.resume.wait(10)
        self.lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self.lock.release()


def test_a_writer_already_past_the_check_writes_nothing_after_a_failure(sink, monkeypatch, caplog):
    render_queries_file.emit(_render("Initial"))
    writer = render_queries_file._writer
    arrived, resume = threading.Event(), threading.Event()
    monkeypatch.setattr(writer, "lock", _Gate(writer.lock, arrived, resume))
    waiting = threading.Thread(name="waiting", target=lambda: render_queries_file.emit(_render("AfterFailure")))
    waiting.start()
    assert arrived.wait(10)  # past the process's failure check, before the lock

    def fail(fd: int, data: t.Any) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(render_queries_file, "_write", fail)
    with caplog.at_level(logging.WARNING, logger="wireview.queries"):
        render_queries_file.emit(_render("Failure"))
        monkeypatch.setattr(render_queries_file, "_write", os.write)
        resume.set()
        waiting.join(10)
    assert not waiting.is_alive()
    assert [record["renders"][0]["name"] for record in lines(sink)] == ["Initial"]
    assert len([r for r in caplog.records if "no longer written" in r.getMessage()]) == 1


def test_a_writer_waiting_on_the_lock_during_a_short_write_writes_nothing(sink, monkeypatch, caplog):
    render_queries_file.emit(_render("Initial"))
    writer = render_queries_file._writer
    arrived = threading.Event()
    monkeypatch.setattr(writer, "lock", _Gate(writer.lock, arrived))
    waiting = threading.Thread(name="waiting", target=lambda: render_queries_file.emit(_render("Waiting")))

    def short(fd: int, data: t.Any) -> int:
        if threading.current_thread() is waiting:
            return os.write(fd, data)  # the disk is fine again: only the turned-off writer keeps it out
        os.write(fd, bytes(data[:10]))
        waiting.start()
        assert arrived.wait(10)  # it asks for the lock while this write holds it
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(render_queries_file, "_write", short)
    with caplog.at_level(logging.WARNING, logger="wireview.queries"):
        render_queries_file.emit(_render("Failure"))
        waiting.join(10)
    assert not waiting.is_alive()
    assert [record["renders"][0]["name"] for record in lines(sink)] == ["Initial"]  # every line whole
    assert len([r for r in caplog.records if "no longer written" in r.getMessage()]) == 1


def test_the_format_examples_keep_their_own_rules():
    """The example in the format's reference: ``count`` is every statement, every ``by`` a render."""
    import re

    text = (Path(__file__).resolve().parent.parent / "docs/features/render-queries.md").read_text(encoding="utf-8")
    section = text.split("### 형식 1.0", 1)[1]
    [block] = re.findall(r"```json\n(.*?)```", section.split("\n## ", 1)[0], re.S)
    record = json.loads(block)
    assert record["version"] == render_queries_file.VERSION
    assert_whole(record)


# -- review A2: what decides the cut, a failure while preparing -----------------------------------


def _handler(noise: int, *, times: int = 3, head: str = "", a_first: bool = False) -> t.Any:
    """A handler's work: rows outside any render (``noise`` groups of ``times``), A's render, an empty render C.

    A's statement is long: cutting it must save more than the ``more`` and ``partial`` it adds.
    """
    work = render_queries.Scope("handler", stand_in("Host"), "save", None, sink=True)
    a = _render("A", work)
    c = render_queries.Scope("render", stand_in("C", id=head or "c"), "nested", work, sink=True)
    _run(a, "SELECT 'a' /* " + "y" * 800 + " */")
    noise_rows = []
    for n in range(noise):
        for _ in range(times):
            noise_rows.append(render_queries.Row(f"SELECT {n} /* " + "x" * 980 + " */", work))
    work.renders = [a, c]
    work.rows = a.rows + noise_rows if a_first else noise_rows + a.rows
    return work


def test_rows_outside_any_render_decide_the_cut_too(sink):
    """Review A2-1: the same A, cut short by a handler's own rows, then drawn whole."""
    render_queries_file.emit(_handler(150))
    [cut] = lines(sink)
    assert f"{__name__}.A" in cut["partial"] and not rows_of(cut, ".A")
    render_queries_file.emit(_handler(0))
    records = lines(sink)
    assert len(records) == 2 and rows_of(records[1], ".A") and "partial" not in records[1]


def test_the_head_decides_the_cut_too(sink):
    """The same rows; only another render's id grows the head and pushes A out."""
    limit = render_queries_file.LINE_LIMIT
    size = limit - 2000
    while size < limit:  # an id with which the line still fits, without A's row
        record = render_queries_file._Record(_handler(0, head="i" * size))
        line = record.line("p", 1)
        if line is not None and record.kept == 0:
            break
        size += 8
    assert size < limit
    render_queries_file.emit(_handler(0, head="i" * size))
    [cut] = lines(sink)
    assert not rows_of(cut, ".A") and f"{__name__}.A" in cut["partial"]
    render_queries_file.emit(_handler(0))
    records = lines(sink)
    assert len(records) == 2 and rows_of(records[1], ".A")


def test_the_order_of_rows_with_the_same_count_decides_the_cut_too(sink):
    """Rows of one count keep their order: A last is cut, A first is kept."""
    render_queries_file.emit(_handler(70, times=1))
    render_queries_file.emit(_handler(70, times=1, a_first=True))
    first, second = lines(sink)[:2]
    assert not rows_of(first, ".A")
    assert rows_of(second, ".A")


def test_a_writer_turned_off_in_the_process_writes_nothing(sink):
    """The writer's own lock also sees the process's failure, set by any path."""
    render_queries_file.emit(_render("Initial"))
    writer = render_queries_file._writer
    render_queries_file._failed = True  # as another path left it, before this writer was closed
    writer.emit(_render("After"))
    assert [record["renders"][0]["name"] for record in lines(sink)] == ["Initial"]


def test_a_failure_while_preparing_a_line_keeps_a_waiting_writer_out(sink, monkeypatch, caplog):
    """Review A2-2: the failure is reading the scope, not writing; the waiter is released as it is told."""
    render_queries_file.emit(_render("Initial"))
    writer = render_queries_file._writer
    arrived, resume, done = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(writer, "lock", _Gate(writer.lock, arrived, resume))

    def wait_and_write() -> None:
        render_queries_file.emit(_render("AfterFailure"))
        done.set()

    waiting = threading.Thread(name="waiting", target=wait_and_write)
    waiting.start()
    assert arrived.wait(10)
    seen: list[tuple[bool, bool]] = []

    class Pause(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append((render_queries_file._failed, writer.off))
            resume.set()
            assert done.wait(10)

    def getter(self: t.Any) -> int:
        return 1

    getter.__wrapped__ = getter  # type: ignore[attr-defined]  # inspect.unwrap raises ValueError on the cycle

    class Owner:
        total = property(getter)

    failing = _render("Failure")
    row = render_queries.Row("SELECT 1", failing)
    row.render = failing
    row.prop = "total"
    row.owner_cls = Owner
    failing.rows.append(row)
    handler = Pause()
    logger = logging.getLogger("wireview.queries")
    logger.addHandler(handler)
    try:
        render_queries_file.emit(failing)
    finally:
        logger.removeHandler(handler)
    waiting.join(10)
    assert not waiting.is_alive()
    assert seen == [(True, True)]  # both off before a word was said
    assert [record["renders"][0]["name"] for record in lines(sink)] == ["Initial"]
