"""Where a navigation may go, in a browser (#262, GAP-066).

Every way a page moves judges its destination with the browser's URL parser
(navigation.mjs ``destination``): this document's origin is boost's to fetch
and draw, another origin is a page load the browser makes, and a URL that is
not http(s) goes nowhere. The other origin is the test server under its other
name: the page is at ``127.0.0.1``, ``localhost`` is another origin, and its
``/historyprobe/foreign/`` lets any origin read it (CORS ``*``), as a CDN or a
public API does -- so nothing but the policy keeps its HTML out of the page.

What it was: a destination that did not start with ``http://`` or ``https://``
-- ``//localhost/``, ``HTTP://localhost/``, `` http://localhost/`` -- was taken
for this origin and pushed to history, which threw a ``SecurityError``: the
move went nowhere. A same-origin URL that redirected there was fetched, the
other origin's answer read, and the move died the same way on the history
entry. ``push_to`` and ``replace_to`` to another site did too. And with boost
off, ``wireview.visit("javascript:...")`` ran the script.

Fixture: tests/testproj/historyprobe/ (``aim/``, ``foreign/``, ``hop/``).
"""

from __future__ import annotations

import pytest
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live
from testproj.e2e_server import serve
from testproj.wireview_setting import set_wireview

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture
def foreign():
    from testproj.historyprobe.urls import FOREIGN

    FOREIGN.clear()
    return FOREIGN


def other_origin(base: str) -> str:
    return base.replace("127.0.0.1", "localhost")


class Watch:
    """What the page said went wrong, and whether a script it was sent to ran."""

    def __init__(self, page) -> None:
        self.page_errors: list[str] = []
        self.console_errors: list[str] = []
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on(
            "console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None
        )


@pytest.fixture
def aim(page, server):
    open_live(page, f"{server}/historyprobe/aim/")
    page.evaluate("window.__samePage = true; window.__ran = null")
    return page


def on_the_other_origin(page, server: str) -> None:
    """The browser loaded the other origin's page: its own document, not this one with its HTML."""
    page.wait_for_url(f"{other_origin(server)}/historyprobe/foreign/", timeout=WAIT_TIMEOUT * 1000)
    expect_text(page.get_by_test_id("page"), "foreign")
    assert not page.evaluate("window.__samePage === true"), "a page load, not a morph"


def visit(page, url: str) -> None:
    page.evaluate(
        "url => { window.__rejected = null;"
        " window.wireview.visit(url).catch((e) => { window.__rejected = String(e); }); }",
        url,
    )


SPELLINGS = {
    "scheme-relative": lambda other: "//" + other.split("//", 1)[1],
    "upper-case": lambda other: other.replace("http://", "HTTP://"),
    "leading-space": lambda other: " " + other,
    "leading-tab": lambda other: "\t" + other,
    "plain": lambda other: other,
}


@pytest.mark.parametrize("spelling", list(SPELLINGS))
def test_another_origin_however_spelled_is_a_page_load(aim, server, foreign, spelling):
    page = aim
    watch = Watch(page)
    visit(page, SPELLINGS[spelling](f"{other_origin(server)}/historyprobe/foreign/"))
    on_the_other_origin(page, server)
    assert foreign == [("GET", "")], "the browser's navigation, not a fetch from this page"
    assert watch.page_errors == []


@pytest.mark.parametrize("start", ["link", "visit"])
def test_a_redirect_to_another_origin_that_allows_the_read_is_not_drawn_here(aim, server, foreign, start):
    # The other origin would let the boosted fetch read its answer (CORS): it is
    # still another origin's page, and the browser loads it -- following the
    # redirect itself
    page = aim
    watch = Watch(page)
    hop = f"/historyprobe/hop/?to={other_origin(server)}/historyprobe/foreign/"
    if start == "link":
        page.evaluate(
            "url => { const a = document.createElement('a'); a.href = url; a.textContent = 'hop';"
            " a.dataset.testid = 'hop'; document.body.append(a); }",
            hop,
        )
        page.get_by_test_id("hop").click()
    else:
        visit(page, hop)
    on_the_other_origin(page, server)
    # The page's fetch does not follow the redirect there (same-origin mode):
    # the other origin hears only the browser's load
    assert foreign == [("GET", "")]
    assert watch.page_errors == []


def aim_at(page, to: str) -> None:
    page.get_by_test_id("aim-to").fill(to)
    page.get_by_test_id("aim-to").dispatch_event("change")
    page.wait_for_function(
        "to => document.querySelector('[data-testid=aim-at]').innerText === to", arg=to, timeout=WAIT_TIMEOUT * 1000
    )


@pytest.mark.parametrize("how", ["push", "replace", "redirect", "navigate"])
@pytest.mark.parametrize("spelling", ["scheme-relative", "plain"])
def test_the_server_sending_the_page_to_another_site_is_a_page_load(aim, server, foreign, how, spelling):
    # A payment page, a sign-in: push_to and replace_to there threw in
    # pushState and went nowhere; redirect_to and JS().navigate to //host did
    page = aim
    watch = Watch(page)
    aim_at(page, SPELLINGS[spelling](f"{other_origin(server)}/historyprobe/foreign/"))
    page.get_by_test_id(f"aim-{how}").click()
    on_the_other_origin(page, server)
    assert foreign == [("GET", "")]
    assert watch.page_errors == []


@pytest.mark.parametrize("boost", [True, False], ids=["boost", "no-boost"])
@pytest.mark.parametrize(
    "url", ["javascript:window.__ran=1;void 0", "JavaScript:window.__ran=1;void 0", " javascript:window.__ran=1;void 0"]
)
def test_a_javascript_url_handed_to_visit_does_not_run(page, server, monkeypatch, boost, url):
    # With boost off, visit() handed it to location.assign, which ran it
    set_wireview(monkeypatch, BOOST_PAGES=boost)
    open_live(page, f"{server}/historyprobe/aim/")
    page.evaluate("window.__samePage = true; window.__ran = null")
    watch = Watch(page)

    visit(page, url)
    page.wait_for_function("() => window.__rejected !== undefined", timeout=WAIT_TIMEOUT * 1000)  # visit() settled
    page.wait_for_timeout(300)  # a javascript: navigation runs a task later

    assert page.evaluate("window.__ran") is None
    assert page.evaluate("window.__samePage === true"), "the page stays"
    assert any("refused to navigate" in text for text in watch.console_errors), watch.console_errors
    assert watch.page_errors == []


@pytest.mark.parametrize("how", ["push", "replace", "redirect", "navigate"])
def test_the_server_refuses_a_javascript_destination_at_the_call(aim, how):
    page = aim
    aim_at(page, "javascript:window.__ran=1;void 0")
    page.get_by_test_id(f"aim-{how}").click()
    expect_text(page.get_by_test_id("aim-refused"), "DisallowedRedirect")
    assert page.evaluate("window.__ran") is None
    assert page.evaluate("window.__samePage === true")


def test_a_same_origin_destination_is_still_boosted(aim, server):
    # The control: nothing of the policy reaches a move inside the site
    page = aim
    aim_at(page, f"{server}/historyprobe/other/?tab=o")
    page.get_by_test_id("aim-push").click()
    page.wait_for_url(f"{server}/historyprobe/other/?tab=o", timeout=WAIT_TIMEOUT * 1000)
    expect_text(page.get_by_test_id("page"), "other")
    assert page.evaluate("window.__samePage === true"), "boosted"
