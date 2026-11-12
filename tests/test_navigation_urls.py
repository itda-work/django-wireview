"""Where a navigation the server asks for may go (#262, GAP-066).

``redirect_to``, ``push_to``, ``replace_to`` and ``JS().navigate`` resolve their
destination through ``wireview.core.destinations.check_destination``: a scheme
other than http or https, a control character, a backslash or whitespace
around the URL raises ``DisallowedRedirect`` at the call -- before anything is
sent, and before an HTTP render's meta refresh can carry it. Another site's
http(s) URL is allowed: the browser goes there (the client's half,
navigation.mjs ``destination``, never draws another origin in the page).

The vectors are one table, ``tests/js/navigation-urls.json``: the server's
verdict here, and what the browser's URL parser makes of the same string in
tests/js/navigation.test.mjs. Every string the server lets through, the client
reads as an http(s) URL -- it never has to refuse what the server allowed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.exceptions import DisallowedRedirect

from wireview import JS, Component
from wireview.core.destinations import check_destination
from wireview.core.meta import WireviewMeta, resolve_destination
from wireview.testing import mount

pytestmark = pytest.mark.unit

TABLE = json.loads((Path(__file__).parent / "js" / "navigation-urls.json").read_text(encoding="utf-8"))
VECTORS = TABLE["vectors"]


def _id(vector: dict) -> str:
    return repr(vector["url"])[1:-1][:40]


@pytest.mark.parametrize("vector", VECTORS, ids=[_id(v) for v in VECTORS])
def test_the_server_judges_each_vector_as_the_table_says(vector):
    if vector["server"] == "ok":
        assert check_destination(vector["url"]) == vector["url"]
    else:
        with pytest.raises(DisallowedRedirect, match="Refused to navigate"):
            check_destination(vector["url"])


def test_nothing_the_server_allows_is_refused_by_the_browser():
    # The two halves agree where it matters: a URL the call let through is a
    # web page to the client, never one it has to refuse
    assert [v["url"] for v in VECTORS if v["server"] == "ok" and v["client"] == "refused"] == []


def test_every_url_the_browser_refuses_the_server_refuses_too():
    assert [v["url"] for v in VECTORS if v["client"] == "refused" and v["server"] == "ok"] == []


def test_the_table_covers_the_kinds_of_url_django_guards_against():
    urls = [v["url"] for v in VECTORS]
    kinds = {
        "scheme-relative": any(u.startswith("//") for u in urls),
        "upper-case scheme": any(u.startswith("HTTP") for u in urls),
        "leading space": any(u.startswith(" ") for u in urls),
        "control character": any(any(ord(c) < 0x20 for c in u) for u in urls),
        "backslash": any("\\" in u for u in urls),
        "javascript": any(u.lower().lstrip().startswith("javascript:") for u in urls),
        "data": any(u.startswith("data:") for u in urls),
        "vbscript": any(u.startswith("vbscript:") for u in urls),
        "userinfo": any("@" in u and "://" in u for u in urls),
    }
    assert [kind for kind, covered in kinds.items() if not covered] == []


def test_a_url_that_is_not_a_string_is_refused():
    with pytest.raises(DisallowedRedirect):
        check_destination(None)  # type: ignore[arg-type]


# --- what resolve_destination still resolves (unchanged) --------------------------------


class _Article:
    def get_absolute_url(self) -> str:
        return "/articles/7/"


class _Elsewhere:
    def get_absolute_url(self) -> str:
        return "javascript:alert(1)"


@pytest.mark.parametrize(
    ("to", "url"),
    [
        ("?page=2", "?page=2"),
        ("#top", "#top"),
        ("/historyprobe/other/", "/historyprobe/other/"),
        ("historyprobe:other", "/historyprobe/other/"),
        (_Article(), "/articles/7/"),
        ("http://testserver/historyprobe/", "http://testserver/historyprobe/"),
        ("https://pay.example/checkout?order=1", "https://pay.example/checkout?order=1"),
        ("//pay.example/checkout", "//pay.example/checkout"),
    ],
    ids=["query", "fragment", "path", "view name", "model", "same origin", "external", "scheme-relative"],
)
def test_the_destinations_an_app_names_resolve_as_before(to, url):
    assert resolve_destination(to) == url


def test_what_a_model_names_is_judged_as_well():
    # get_absolute_url is the app's code, but its answer is a destination all the same
    with pytest.raises(DisallowedRedirect, match="javascript"):
        resolve_destination(_Elsewhere())


def test_a_string_without_a_dot_or_a_slash_is_still_a_view_name():
    # resolve_url's rule, unchanged: "javascript:alert(1)" never was a URL to it
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        resolve_destination("javascript:alert(1)")


def test_a_query_or_fragment_with_a_control_character_is_refused():
    with pytest.raises(DisallowedRedirect, match="control character"):
        resolve_destination("?next=\n/x")


# --- every call that names a destination ------------------------------------------------


class Aimer(Component):
    class Meta:
        template_name = "todo/counter.html"

    count: int = 0

    async def go(self, how: str = "push", to: str = ""):
        if how == "redirect":
            await self.wire.redirect_to(to)
        elif how == "replace":
            await self.wire.replace_to(to)
        elif how == "navigate":
            await self.wire.push_js(self.id, JS().navigate(to))
        else:
            await self.wire.push_to(to)


HOWS = ["push", "replace", "redirect", "navigate"]


@pytest.mark.parametrize("how", HOWS)
@pytest.mark.parametrize(
    "to",
    # With a "." or a "/": resolve_url takes a string without either for a view
    # name, and a bare "javascript:alert(1)" was a NoReverseMatch already
    [
        "javascript:alert(document.cookie)",
        "data:text/html,x",
        "java\tscript:alert(document.cookie)",
        "/\\evil.example/",
    ],
)
@pytest.mark.asyncio
async def test_a_refused_destination_raises_at_the_call_and_sends_nothing(how, to):
    view = await mount(Aimer)
    sent = len(view.sent_messages)
    with pytest.raises(DisallowedRedirect):
        await view.call("go", how=how, to=to)
    assert view.sent_messages[sent:] == [], "nothing reached the page"


@pytest.mark.parametrize("how", HOWS)
@pytest.mark.asyncio
async def test_an_external_destination_is_still_sent(how):
    # A payment page, a sign-in: the browser goes there (the client never draws it here)
    view = await mount(Aimer)
    await view.call("go", how=how, to="https://pay.example/checkout")
    assert "https://pay.example/checkout" in json.dumps(view.sent_messages[-1])


def test_js_navigate_refuses_at_the_builder():
    with pytest.raises(DisallowedRedirect, match="javascript"):
        JS().navigate("javascript:alert(1)")
    assert JS().navigate("/dashboard", replace=True)


@pytest.mark.asyncio
async def test_an_http_render_never_carries_a_refused_destination_to_its_meta_refresh():
    # The meta refresh of an HTTP render writes what redirect_to stored: a
    # refused URL raises before it is stored
    wire = WireviewMeta(params={})
    with pytest.raises(DisallowedRedirect):
        await wire.redirect_to("javascript:alert(document.cookie)")
    assert wire._redirected_to is None
    await wire.redirect_to("/login/")
    assert wire._redirected_to == "/login/"
