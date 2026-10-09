"""An HTTP render does not close the request's database connection (#190).

The template reaches a component's async code -- its ``params_changed``, its
on_mount hooks, an async property -- with ``async_to_sync`` on the thread the
template renders on. Thread-sensitive work that code awaits comes back to that
same thread, and Channels' ``database_sync_to_async`` closes the thread's
connections around it, as hygiene for a worker thread. On the request's own
thread that is the request's connection, and inside ``ATOMIC_REQUESTS`` (or any
``transaction.atomic()``) Django closes it whatever ``CONN_MAX_AGE`` says: the
view's writes were rolled back without an exception, its ``on_commit``
callbacks dropped, and the response was a 200 that looked right.

``stream()`` did that on its own: it drew each item with
``database_sync_to_async``, for a page that had no socket to receive the items.
An HTTP render now draws no stream items -- the ``{% for %}`` fallback inside
``wire-stream`` draws the list -- and the bridges keep the caller's connections
open, so a hook that awaits ``database_sync_to_async`` itself is safe too.

SQLite's memory database ignores ``close()``; the test database is a file
(tests/testproj/settings.py), which is what makes these fail without the fix.
"""

import subprocess
import sys
import typing as t

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import close_old_connections, connection, connections, transaction
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from testproj.bookmarks.models import Bookmark

from wireview import Component, mount
from wireview.core.component import Component as ComponentBase
from wireview.core.connections import keep_connections
from wireview.core.meta import HTTP_RENDER

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

TEMPLATES = {
    "hrc/streamed.html": (
        "{% load wireview %}<ul {% tag_header %} wire-stream='hits'>"
        "{% for hit in this.hits %}<li>{{ hit.title }}</li>{% endfor %}</ul>"
    ),
    "hrc/streamed_item.html": "<li>{{ item.title }}</li>",
    "hrc/plain.html": "{% load wireview %}<p {% tag_header %}>[{{ this.q }}]</p>",
    "hrc/counted.html": "{% load wireview %}<p {% tag_header %}>total={{ total }}</p>",
}


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


class HrcStreamed(Component):
    """Streams its query's results, the way a search page does."""

    class Meta:
        template_name = "hrc/streamed.html"

    q: str = ""
    hits: list[Bookmark] = []

    async def params_changed(self, params, uri):
        self.q = params.get("q", "")
        self.hits = [hit async for hit in Bookmark.objects.filter(title__icontains=self.q)]
        await self.stream("hits", self.hits)


class HrcStreamsOneMore(Component):
    class Meta:
        template_name = "hrc/streamed.html"

    q: str = ""
    hits: list[Bookmark] = []

    async def params_changed(self, params, uri):
        self.q = params.get("q", "")
        hit = await Bookmark.objects.filter(title__icontains=self.q).afirst()
        await self.stream_insert("hits", hit)
        await self.stream_delete("hits", 1)


class HrcAwaitsChannelsDb(Component):
    """A hook that awaits Channels' ``database_sync_to_async`` itself."""

    class Meta:
        template_name = "hrc/plain.html"

    q: str = ""

    async def params_changed(self, params, uri):
        self.q = await database_sync_to_async(lambda: params.get("q", ""))()


class HrcAsyncProperty(Component):
    """An async property: the HTTP render resolves it with ``async_to_sync`` too."""

    class Meta:
        template_name = "hrc/counted.html"

    @property
    async def total(self) -> int:
        return await database_sync_to_async(Bookmark.objects.count)()


async def _unread() -> t.AsyncIterator[Bookmark]:
    raise AssertionError("an HTTP render read the stream's items")
    yield


class HrcPropertyStreams(Component):
    """Streams from an async property: the HTTP render resolves it after the hooks' bridge has closed."""

    class Meta:
        template_name = "hrc/counted.html"

    @property
    async def total(self) -> int:
        await self.stream("hits", _unread(), template="hrc/streamed_item.html")
        await self.stream_insert("hits", Bookmark(pk=1, title="x"), template="hrc/streamed_item.html")
        await self.stream_delete("hits", 1)
        return 0


def render(source: str, query: str) -> str:
    request = RequestFactory().get(f"/?{query}")
    request.user = AnonymousUser()
    return Template("{% load wireview %}" + source).render(Context({"request": request, "user": request.user}))


def view(source: str, query: str, committed: list[str]) -> str:
    """A view under ``ATOMIC_REQUESTS``: a write, then the page."""
    with transaction.atomic():
        Bookmark.objects.create(title="written by the view", url="https://example.com/view")
        transaction.on_commit(lambda: committed.append("on_commit"))
        return render(source, query)


SOURCES = [
    pytest.param("{% component 'HrcStreamed' id='s' %}", "<li>django docs</li>", id="stream"),
    pytest.param("{% component 'HrcStreamsOneMore' id='s' %}", "<ul", id="stream_insert-and-delete"),
    pytest.param("{% component 'HrcAwaitsChannelsDb' id='s' %}", "[django]", id="hook-awaits-channels-db"),
    pytest.param("{% component 'HrcAsyncProperty' id='s' %}", "total=2", id="async-property"),
]


@pytest.fixture
def bookmarks():
    Bookmark.objects.create(title="django docs", url="https://example.com/1")


class TestTheViewsWritesAreCommitted:
    @pytest.mark.parametrize("source,drawn", SOURCES)
    def test_a_sync_view(self, bookmarks, source, drawn):
        committed: list[str] = []

        html = view(source, "q=django", committed)

        assert drawn in html
        assert Bookmark.objects.filter(title="written by the view").exists()
        assert committed == ["on_commit"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source,drawn", SOURCES)
    async def test_a_sync_view_under_asgi(self, bookmarks, source, drawn):
        """Django's ASGI handler runs a sync view on a thread-sensitive worker, and the bridge comes back to it."""
        committed: list[str] = []

        html = await sync_to_async(view)(source, "q=django", committed)

        assert drawn in html
        assert await Bookmark.objects.filter(title="written by the view").aexists()
        assert committed == ["on_commit"]


class TestAnHttpRenderDrawsNoStreamItems:
    """Nothing receives them: the fallback draws the list, and the join streams it."""

    @pytest.fixture
    def drawn(self, monkeypatch) -> list[t.Any]:
        drawn: list[t.Any] = []

        async def spy(self, template_name, item):
            drawn.append(item)
            return ""

        monkeypatch.setattr(ComponentBase, "_render_stream_item", spy)
        return drawn

    @pytest.mark.parametrize("name", ["HrcStreamed", "HrcStreamsOneMore", "HrcPropertyStreams"])
    def test_the_items_are_not_rendered(self, bookmarks, drawn, name):
        html = render(f"{{% component '{name}' id='s' %}}", "q=django")

        assert "<ul" in html or "total=0" in html
        assert drawn == []
        assert HTTP_RENDER.get() is False

    @pytest.mark.asyncio
    async def test_nor_by_an_async_view_rendering_on_the_loop(self, bookmarks, drawn):
        """That render runs the hooks on a loop of its own, on a helper thread."""
        html = render("{% component 'HrcStreamed' id='s' %}", "q=django")

        assert "<li>django docs</li>" in html
        assert drawn == []

    @pytest.mark.asyncio
    async def test_a_live_render_still_streams(self, bookmarks, drawn):
        view = await mount(HrcStreamed, id="s", params={"q": "django"})

        assert [hit.title for hit in drawn] == ["django docs"]
        assert view.stream_items("hits")


class TestKeepConnections:
    def test_close_old_connections_leaves_them_open_inside(self):
        with transaction.atomic():
            Bookmark.objects.create(title="kept", url="https://example.com/kept")
            with keep_connections():
                close_old_connections()
            assert not connection.closed_in_transaction
        assert Bookmark.objects.filter(title="kept").exists()

    def test_it_lets_go_on_the_way_out(self):
        with keep_connections():
            pass
        assert all("close_if_unusable_or_obsolete" not in vars(conn) for conn in connections.all())

    def test_a_nested_one_leaves_the_outer_one_in_place(self):
        with keep_connections():
            with keep_connections():
                pass
            assert all("close_if_unusable_or_obsolete" in vars(conn) for conn in connections.all())
        assert all("close_if_unusable_or_obsolete" not in vars(conn) for conn in connections.all())

    def test_it_holds_on_this_thread_only(self):
        """Connections are per thread: a worker's own hygiene goes on as before."""
        seen: list[bool] = []

        def elsewhere():
            # The wrapper itself: ``django.db.connection`` is a proxy with a dict of its own
            seen.append("close_if_unusable_or_obsolete" in vars(connections["default"]))

        with keep_connections():
            async_to_sync(sync_to_async(elsewhere, thread_sensitive=False))()
            here = "close_if_unusable_or_obsolete" in vars(connections["default"])
        assert (here, seen) == (True, [False])


@pytest.mark.django_db(transaction=False)
def test_a_fresh_process_sets_django_up():
    """``core/meta.py`` imports the guard while ``wireview.utils`` is half imported (#190 review).

    The suite imports components before ``ready()`` runs, so only a fresh
    process with nothing imported yet sees the order a project's startup has.
    """
    script = (
        "from django.conf import settings;"
        "settings.configure(SECRET_KEY='x', INSTALLED_APPS=['django.contrib.contenttypes',"
        "'django.contrib.auth', 'wireview'], DATABASES={'default': {'ENGINE':"
        "'django.db.backends.sqlite3', 'NAME': ':memory:'}});"
        "import django; django.setup()"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
