"""The first HTTP render hears the page's query, as a join does (#177).

Phoenix's dead render runs mount -> handle_params -> render, and so does the
join since #170. The HTTP render ran mount -> render: a page opened at
``?q=...`` went out drawn as if it had no query, and a browser without
JavaScript, or a search engine, saw it empty. Every component an HTTP render
builds now hears ``params_changed`` before it is drawn, once per instance and
only when the page has a query -- the root, a ``{% component %}`` nested in
it, one in a slot, one a function component's template draws, a sticky one and
a LiveComponent.

``data-state`` carries the state from before the query, so the join starts
from the mounted state and hears it again (TestTheJoinStartsFromTheMountedState).

The refusals (a halted mount, a component outside the page's boundary) are in
the path x reason table, tests/test_live_session_contract.py. The browser is in
examples/search/tests.py and tests/test_history_e2e.py
(test_the_first_response_already_heard_the_params,
test_the_join_hears_the_params_again_from_the_mounted_state).
"""

import logging
import re
import typing as t
from html import unescape

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from testproj.outbound import RecordingOutbound
from testproj.waiting import eventually

from wireview import AsyncResult, Component, LiveComponent, function_component, mount
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import page_drawing
from wireview.core.state import sign_state, unsign_state
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
    "hp/items.html": "{% load wireview %}<p {% tag_header %}>q={{ this.q }} items={{ this.items|join:',' }}</p>",
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


class HpGuarded(Component):
    """Returns early when the query matches its own field: it would skip a second hearing of the same query."""

    class Meta:
        template_name = "hp/slow.html"

    q: str = ""
    rows: AsyncResult[str] | None = None

    async def params_changed(self, params, uri):
        q = params.get("q", "")
        if q == self.q:
            return
        self.q = q
        self.rows = await self.assign_async(self._load(q))

    async def _load(self, q: str) -> str:
        return q.upper()


class HpGuardedTemporary(Component):
    """The same guard, with its result in a temporary assign the signed state leaves out."""

    class Meta:
        template_name = "hp/items.html"
        temporary_assigns = ["items"]

    q: str = ""
    items: list[str] = []

    async def params_changed(self, params, uri):
        q = params.get("q", "")
        if q == self.q:
            return
        self.q = q
        self.items = [q, q * 2]


class HpGuardedExcluded(HpGuardedTemporary):
    """The same, with its result in a field ``Meta.exclude_fields`` leaves out."""

    class Meta:
        template_name = "hp/items.html"
        temporary_assigns = []
        exclude_fields = ["items"]


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

    def test_the_instance_keeps_what_it_heard(self):
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


def signed_state(html: str, component_id: str) -> str:
    """The ``data-state`` the HTTP render gave ``component_id``, as the page sends it back."""
    match = re.search(rf'id="{component_id}" data-name="[^"]*" data-state="([^"]*)"', html)
    assert match, f"no data-state for {component_id}"
    return unescape(match.group(1))


def state_of(html: str, component_id: str, name: str) -> dict[str, t.Any]:
    return unsign_state(signed_state(html, component_id), name)


def drawn(diff: dict[str, t.Any]) -> str:
    """The HTML a join's render draws, from a diff of plain dynamics."""
    statics, dynamics = diff["s"], diff["d"]
    out = [statics[0]]
    for value, static in zip(dynamics, statics[1:], strict=True):
        out.append(drawn(value) if isinstance(value, dict) else str(value))
        out.append(static)
    return "".join(out)


def as_the_page_shows(html: str) -> str:
    return re.sub(r' data-state="[^"]*"| data-is-live="[^"]*"', "", page_drawing(html))


async def join(html: str, name: str, component_id: str, params: dict[str, str]):
    """The join the page sends for ``component_id`` with the state the HTTP render signed."""
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), params=dict(params))
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = RecordingOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    await consumer.command_join(name, signed_state(html, component_id))
    return consumer.repo.get(component_id), outbound


class TestTheJoinStartsFromTheMountedState:
    """``data-state`` carries the state before the HTTP render heard the query (#177).

    The HTML shows what the query made of the component; the join starts from
    the mounted state and hears the query again, as Phoenix's connected mount
    starts afresh rather than from the dead render's assigns. Signed as drawn,
    a ``params_changed`` that returns early when the query matches its own
    field did nothing on the join: the work the HTTP render cancelled never
    restarted, and a result outside the signed state came back empty.
    """

    def test_data_state_is_the_state_before_the_query(self):
        html, repo = render("{% component 'HpQuery' id='root' %}", "q=django")

        assert "[django]" in html
        assert repo.get("root").q == "django"
        assert state_of(html, "root", "HpQuery") == {"id": "root", "q": ""}

    def test_a_nested_component_and_a_live_component_too(self):
        html, _ = render("{% component 'HpHost' id='host' %}", "q=pony")

        assert state_of(html, "host", "HpHost")["q"] == ""
        assert state_of(html, "nested", "HpQuery")["q"] == ""
        assert state_of(html, "child", "HpLive")["q"] == ""

    def test_without_a_query_it_is_the_state_drawn(self):
        """Nothing heard, nothing held back: a page without params signs exactly what it signed before."""
        html, repo = render("{% component 'HpSticky' id='s' %}")
        component = repo.get("s")

        assert component.wire._unheard_state is None
        assert signed_state(html, "s") == sign_state(component)

    def test_a_change_after_the_query_signs_the_new_state(self):
        """Held back only while the instance is still as the query left it."""
        _, repo = render("{% component 'HpQuery' id='root' %}", "q=django")
        component = repo.get("root")
        component.q = "edited"

        assert unsign_state(sign_state(component), "HpQuery")["q"] == "edited"

    def test_the_join_restarts_the_work_a_guard_would_have_skipped(self):
        html, _ = render("{% component 'HpGuarded' id='guarded' %}", "q=abc")
        assert "loading" in html

        async def scenario():
            component, outbound = await join(html, "HpGuarded", "guarded", {"q": "abc"})
            # The join's first render draws what the HTTP render drew, and its
            # params_changed started the work again.
            assert as_the_page_shows(drawn(outbound.renders()[0]["diff"])) == as_the_page_shows(html)
            await eventually(lambda: component.rows.ok)
            return component.rows.result

        assert async_to_sync(scenario)() == "ABC"

    @pytest.mark.parametrize("name", ["HpGuardedTemporary", "HpGuardedExcluded"])
    def test_the_join_fills_again_what_the_signed_state_leaves_out(self, name):
        html, _ = render(f"{{% component '{name}' id='list' %}}", "q=ab")
        assert "q=ab items=ab,abab" in html

        async def scenario():
            _, outbound = await join(html, name, "list", {"q": "ab"})
            return drawn(outbound.renders()[0]["diff"])

        first = async_to_sync(scenario)()
        assert "q=ab items=ab,abab" in first
        assert as_the_page_shows(first) == as_the_page_shows(html)


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
        """A bridge costs some hundred microseconds; a page of components that ignore the query pays nothing."""
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
