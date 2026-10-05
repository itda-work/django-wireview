"""The first HTTP render hears the page's query, as a join does (#177).

Phoenix's dead render runs mount -> handle_params -> render, and so does the
join since #170. The HTTP render ran mount -> render: a page opened at
``?q=...`` went out drawn as if it had no query, and a browser without
JavaScript, or a search engine, saw it empty. Every component an HTTP render
builds now hears ``params_changed`` before it is drawn, once per instance and
only when the page has a query -- the root, a ``{% component %}`` nested in
it, one in a slot, one a function component's template draws, a sticky one and
a LiveComponent.

The refusals (a halted mount, a component outside the page's boundary) are in
the path x reason table, tests/test_live_session_contract.py. The browser is in
tests/test_dead_view_e2e.py and examples/search/tests.py.
"""

import logging
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from wireview import AsyncResult, Component, LiveComponent, function_component, mount
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

TEMPLATES = {
    "hp/query.html": "{% load wireview %}<p {% tag_header %}>[{{ this.q }}]</p>",
    "hp/live.html": "{% load wireview %}<p {% live_tag_header %}>live[{{ this.q }}]</p>",
    "hp/host.html": (
        "{% load wireview %}<main {% tag_header %}>host[{{ this.q }}]"
        "{% component 'HpQuery' id='nested' %}"
        "{% live_component 'HpLive' id='child' %}"
        "</main>"
    ),
    "hp/frame.html": "{% load wireview %}<section {% tag_header %}>{% render_slot 'body' %}</section>",
    "hp/func.html": "{% load wireview %}<div>{% component 'HpQuery' id=cid %}</div>",
    "hp/slow.html": (
        "{% load wireview %}<p {% tag_header %}>"
        "{% if this.rows.loading %}loading{% elif this.rows.ok %}rows={{ this.rows.result }}{% endif %}"
        "</p>"
    ),
    "hp/livehost.html": (
        "{% load wireview %}<main {% tag_header %}>{% live_component 'HpLiveCrashes' id='bad' %}</main>"
    ),
}

#: ``(component id, what ran)``, in order
CALLS: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def _templates():
    CALLS.clear()
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


class RecordMount:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append((component.id, "mount"))
        return {"cont": True}


class HpQuery(Component):
    class Meta:
        template_name = "hp/query.html"
        on_mount = [RecordMount]

    q: str = ""

    async def joined(self):
        CALLS.append((self.id, "joined"))

    async def params_changed(self, params, uri):
        CALLS.append((self.id, f"params {uri}"))
        self.q = params.get("q", "")


class HpLive(LiveComponent):
    class Meta:
        template_name = "hp/live.html"

    q: str = ""

    async def params_changed(self, params, uri):
        CALLS.append((self.id, "params"))
        self.q = params.get("q", "")


class HpHost(Component):
    class Meta:
        template_name = "hp/host.html"

    q: str = ""

    async def params_changed(self, params, uri):
        CALLS.append((self.id, "params"))
        self.q = params.get("q", "")


class HpFrame(Component):
    class Meta:
        template_name = "hp/frame.html"


class HpSticky(Component):
    class Meta:
        template_name = "hp/query.html"
        sticky = True

    q: str = ""

    async def params_changed(self, params, uri):
        self.q = params.get("q", "")


class HpDeaf(Component):
    """Does not listen: the query cannot change it."""

    class Meta:
        template_name = "hp/query.html"

    q: str = "deaf"


class HpCrashes(Component):
    class Meta:
        template_name = "hp/query.html"

    q: str = ""

    async def params_changed(self, params, uri):
        raise RuntimeError("params_changed blew up")


class HpLiveCrashes(LiveComponent):
    class Meta:
        template_name = "hp/live.html"

    q: str = "kept"

    async def params_changed(self, params, uri):
        raise RuntimeError("params_changed blew up")


class HpLiveHost(Component):
    class Meta:
        template_name = "hp/livehost.html"


class HpHooked(Component):
    """Hears the query only through a handle_params hook: no params_changed, no mount hook."""

    class Meta:
        template_name = "hp/query.html"

    q: str = ""

    def model_post_init(self, context: t.Any) -> None:
        super().model_post_init(context)

        async def hear(params, uri):
            self.q = f"hooked {params.get('q', '')}"
            return {"cont": True}

        self.attach_hook("hear", "handle_params", hear)


#: What the async work of an HTTP render got to do
WORK: list[str] = []


class HpSlow(Component):
    """Loads its rows for the query with assign_async, and starts a side job with start_async."""

    class Meta:
        template_name = "hp/slow.html"

    rows: AsyncResult[str] | None = None

    async def params_changed(self, params, uri):
        self.rows = await self.assign_async(self._load(params.get("q", "")))
        await self.start_async("side", self._side())

    async def _load(self, q: str) -> str:
        WORK.append(f"load {q}")
        return q

    async def _side(self) -> None:
        WORK.append("side")


@function_component(template="hp/func.html")
def hp_func(cid: str):
    return {"cid": cid}


def render(source: str, query: str = "") -> tuple[str, ComponentRepository]:
    """An HTTP response's render of ``source``, for a request at ``/?{query}``."""
    request = RequestFactory().get(f"/?{query}" if query else "/")
    request.user = AnonymousUser()
    context = Context({"request": request, "user": request.user})
    html = Template("{% load wireview %}" + source).render(context)
    return html, context["wireview_repository"]


def calls(component_id: str) -> list[str]:
    return [what for cid, what in CALLS if cid == component_id]


class TestTheFirstRenderShowsTheQuery:
    def test_the_root_hears_it_after_its_mount_and_before_its_render(self):
        html, _ = render("{% component 'HpQuery' id='root' %}", "q=django")

        assert "[django]" in html
        # mount -> handle_params -> render, and no joined(): that is the socket's
        assert calls("root") == ["mount", "params ?q=django"]

    def test_the_signed_state_carries_what_it_heard(self):
        """The join restores it and hears the query again, as Phoenix's connected mount does."""
        _, repo = render("{% component 'HpQuery' id='root' %}", "q=django")

        assert repo.get("root").q == "django"

    def test_without_a_query_nothing_is_heard(self):
        """As a join: params_changed runs only when the address has a query."""
        html, _ = render("{% component 'HpQuery' id='root' %}")

        assert "[]" in html
        assert calls("root") == ["mount"]

    def test_a_nested_component_and_a_live_component_hear_it_too(self):
        html, _ = render("{% component 'HpHost' id='host' %}", "q=pony")

        assert "host[pony]" in html
        assert "[pony]" in html.split("host[pony]", 1)[1]
        assert "live[pony]" in html
        assert calls("host") == ["params"]
        assert calls("nested") == ["mount", "params ?q=pony"]
        assert calls("child") == ["params"]

    def test_a_component_in_a_slot_hears_it(self):
        html, _ = render(
            "{% component_block 'HpFrame' id='frame' %}{% fill body %}"
            "{% component 'HpQuery' id='in-slot' %}"
            "{% endfill %}{% endcomponent %}",
            "q=slot",
        )

        assert "[slot]" in html
        assert calls("in-slot") == ["mount", "params ?q=slot"]

    def test_a_component_a_function_component_draws_hears_it(self):
        html, _ = render("{% func 'hp_func' cid='in-func' %}", "q=func")

        assert "[func]" in html
        assert calls("in-func") == ["mount", "params ?q=func"]

    def test_a_sticky_component_hears_it(self):
        html, _ = render("{% component 'HpSticky' %}", "q=sticky")

        assert "[sticky]" in html

    def test_a_handle_params_hook_hears_it(self):
        html, _ = render("{% component 'HpHooked' id='hooked' %}", "q=x")

        assert "[hooked x]" in html

    def test_the_uri_is_the_query_a_join_would_hear(self):
        render("{% component 'HpQuery' id='root' %}", "b=2&a=1")

        assert calls("root") == ["mount", "params ?b=2&a=1"]

    def test_a_query_sent_unencoded_is_read_as_the_request_reads_it(self):
        """Under WSGI ``QUERY_STRING`` is the raw bytes read as latin-1: parsed as is, ``?q=파이썬`` was mojibake."""
        html, repo = render("{% component 'HpQuery' id='root' %}", "q=파이썬")

        assert "[파이썬]" in html
        assert repo.params == {"q": "파이썬"}


class TestAsyncWorkWaitsForTheJoin:
    def test_the_page_draws_the_loading_state_and_no_work_runs(self):
        """Phoenix starts no async work on a dead render; nothing connected would hear it finish."""
        WORK.clear()
        html, repo = render("{% component 'HpSlow' id='slow' %}", "q=x")

        assert "loading" in html
        component = repo.get("slow")
        assert component.rows.loading
        assert not component._async_tasks and not component._assign_tasks
        assert WORK == []

    @pytest.mark.asyncio
    async def test_on_the_event_loop_too(self):
        """An async view renders on the loop: the hooks get a loop of their own, and it cancels there too."""
        WORK.clear()
        html = Template("{% load wireview %}{% component 'HpSlow' id='slow' %}").render(
            Context({"wireview_repository": ComponentRepository(is_live=False, params={"q": "x"})})
        )

        assert "loading" in html
        assert WORK == []


class TestOncePerInstance:
    def test_an_instance_drawn_twice_hears_it_once(self):
        html, _ = render("{% component 'HpQuery' id='same' %}{% component 'HpQuery' id='same' %}", "q=once")

        assert html.count("[once]") == 2
        assert calls("same") == ["mount", "params ?q=once"]

    def test_a_component_that_does_not_listen_costs_no_bridge(self, monkeypatch):
        """The mount bridge is ~220us; a page full of components that ignore the query pays nothing."""
        from wireview.templatetags import wireview as tags

        crossings: list[str] = []
        original = tags.async_to_sync
        monkeypatch.setattr(tags, "async_to_sync", lambda fn: crossings.append("bridge") or original(fn))

        html, _ = render("{% component 'HpDeaf' id='deaf' %}", "q=x")

        assert "[deaf]" in html
        assert crossings == []

    def test_mount_and_params_cross_one_bridge(self, monkeypatch):
        from wireview.templatetags import wireview as tags

        crossings: list[str] = []
        original = tags.async_to_sync
        monkeypatch.setattr(tags, "async_to_sync", lambda fn: crossings.append("bridge") or original(fn))

        render("{% component 'HpQuery' id='root' %}", "q=x")

        assert crossings == ["bridge"]


class TestALiveRenderLeavesItToTheJoin:
    def test_a_live_pass_does_not_run_it(self):
        """On a socket the join (or the parent's render, for a LiveComponent) runs it, once."""
        repo = ComponentRepository(is_live=True, params={"q": "live"})
        Template("{% load wireview %}{% component 'HpQuery' id='root' %}").render(
            Context({"wireview_repository": repo})
        )

        assert calls("root") == ["mount"]


class TestFailures:
    def test_a_component_whose_params_changed_raises_fails_the_page(self):
        """As its join would fail: nothing of it is drawn, and it is not left in the repository."""
        request = RequestFactory().get("/?q=x")
        context = Context({"request": request})

        with pytest.raises(RuntimeError, match="blew up"):
            Template("{% load wireview %}{% component 'HpCrashes' id='bad' %}").render(context)
        assert context["wireview_repository"].get("bad") is None

    def test_a_live_components_is_logged_and_it_still_renders(self, caplog):
        """As on a socket, where the parent's render logs it and draws the child (_render_tree)."""
        with caplog.at_level(logging.ERROR, logger="wireview"):
            html, _ = render("{% component 'HpLiveHost' id='host' %}", "q=x")

        assert "live[kept]" in html
        assert "HpLiveCrashes.params_changed()" in caplog.text


@pytest.mark.asyncio
class TestTheTestingHelpersAgree:
    async def test_render_draws_children_that_heard_the_query(self):
        """``render()`` draws children the way the first response does, on the event loop (the pool branch)."""
        view = await mount(HpHost, params={"q": "same"})
        html = view.render() or ""

        assert "host[same]" in html
        assert "live[same]" in html
        assert html.count("[same]") == 3

    async def test_mount_with_params_matches_the_http_render(self):
        view = await mount(HpQuery, id="root", params={"q": "same"})
        http_html, _ = await _render_off_loop("{% component 'HpQuery' id='root' %}", "q=same")

        assert "[same]" in (view.render() or "")
        assert "[same]" in http_html


async def _render_off_loop(source: str, query: str) -> tuple[str, ComponentRepository]:
    from asgiref.sync import sync_to_async

    return await sync_to_async(render)(source, query)
