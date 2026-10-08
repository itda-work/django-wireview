"""The page may keep a boosted move from happening (#154).

``wireview:before-navigate`` goes to ``document`` before each move boost makes,
cancelable: a form with unsaved input asks first, as ``beforeunload`` does for a
page load, which never sees a boosted ``pushState``. ``detail`` is
``{url, kind, patch}`` -- and ``form`` for a form. ``wireview:navigated`` names the
same ``kind``.

Cancelled, nothing moves: the address bar, the history, the screen and the
server are as they were -- no fetch, no ``params_changed``, no ``navigated``. Back
and Forward have moved the address bar before anything runs, so a cancelled one
goes back as far as it came, and that return neither asks nor tells.

Not cancelled, every move is what it was before: tests/test_history_e2e.py.

Fixture: tests/testproj/historyprobe/.
"""

from __future__ import annotations

import json
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

#: Records both events; ``window.__cancel`` names the kind to cancel, or true for all
LISTENER = """
(() => {
  window.__before = [];
  window.__landed = [];
  window.__cancel = null;
  document.addEventListener("wireview:before-navigate", (e) => {
    const { url, kind, patch, form } = e.detail;
    window.__before.push({ url, kind, patch, form: form ? form.dataset.testid : null, cancelable: e.cancelable });
    if (window.__cancel === true || window.__cancel === kind) e.preventDefault();
  });
  document.addEventListener("wireview:navigated", (e) => window.__landed.push(e.detail));
})();
"""


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


class Sent:
    """The commands the page sent over its sockets."""

    def __init__(self, page) -> None:
        self.commands: list[str] = []
        page.on("websocket", lambda ws: ws.on("framesent", self._frame))

    def _frame(self, frame) -> None:
        self.commands.append(json.loads(frame)["command"])

    def since(self, mark: int) -> list[str]:
        return self.commands[mark:]


class Fetches:
    """The page fetches a boosted move makes."""

    def __init__(self, page) -> None:
        self.urls: list[str] = []
        page.on("request", self._request)

    def _request(self, request) -> None:
        if request.resource_type == "fetch" and "/historyprobe/" in request.url:
            self.urls.append(request.url)


@pytest.fixture
def box(page, server):
    page.add_init_script(LISTENER)
    page.sent = Sent(page)
    open_live(page, f"{server}/historyprobe/")
    page.evaluate("window.__samePage = true")
    page.fetches = Fetches(page)
    return page


@pytest.fixture
def heard():
    from testproj.historyprobe.live import HEARD

    HEARD.clear()
    yield HEARD
    HEARD.clear()


def at(page, path: str) -> None:
    page.wait_for_function(
        "path => location.pathname + location.search === path", arg=path, timeout=WAIT_TIMEOUT * 1000
    )


def asked(page, count: int) -> list[dict]:
    page.wait_for_function("n => window.__before.length >= n", arg=count, timeout=WAIT_TIMEOUT * 1000)
    return page.evaluate("window.__before")


def landed(page, count: int) -> list[dict]:
    page.wait_for_function("n => window.__landed.length >= n", arg=count, timeout=WAIT_TIMEOUT * 1000)
    return page.evaluate("window.__landed")


def bump_to(page, count: int) -> None:
    by(page, "bump").click()
    expect_text(by(page, "count"), str(count))


def settled(page) -> None:
    """A round trip after everything the page sent so far: the server has handled it all."""
    bump_to(page, int(by(page, "count").inner_text()) + 1)


def unmoved(page, *, path: str, length: int, mark: int, heard: list, rendered: str) -> None:
    """The page, its history and the server are where they were before ``mark``."""
    settled(page)
    at(page, path)
    assert page.evaluate("history.length") == length
    assert page.evaluate("window.__samePage === true")
    assert page.fetches.urls == [], page.fetches.urls
    assert by(page, "rendered").inner_text() == rendered, "the page was not fetched again"
    # Only the bump a settle sends: no params, no navigation, no joins
    assert set(page.sent.since(mark)) == {"user_event"}, page.sent.since(mark)
    assert heard == []
    assert page.evaluate("window.__landed") == []


# --- what each move says -------------------------------------------------------------


def url(page, path: str) -> str:
    return urljoin(page.url, path)


def click(testid):
    return lambda page: by(page, testid).click()


@pytest.mark.parametrize(
    ("move", "kind", "goes", "lands", "form"),
    [
        (click("to-other"), "link", "/historyprobe/other/", "/historyprobe/other/", None),
        (click("get-send"), "form", "/historyprobe/other/?tab=g", "/historyprobe/other/?tab=g", "get-form"),
        # A POST names its action; it lands where the server redirected it
        (click("post-send"), "form", "/historyprobe/post/", "/historyprobe/other/?tab=p", "post-form"),
        (
            lambda page: page.evaluate("wireview.visit('/historyprobe/other/?tab=v')"),
            "visit",
            "/historyprobe/other/?tab=v",
            "/historyprobe/other/?tab=v",
            None,
        ),
        (click("push-other"), "push", "/historyprobe/other/?tab=o", "/historyprobe/other/?tab=o", None),
        (click("replace-other"), "replace", "/historyprobe/other/?tab=o", "/historyprobe/other/?tab=o", None),
        (click("redirect-d"), "redirect", "/historyprobe/?tab=d", "/historyprobe/?tab=d", None),
    ],
    ids=["link", "get-form", "post-form", "visit", "push", "replace", "redirect"],
)
def test_a_move_says_what_started_it_before_and_after(box, move, kind, goes, lands, form):
    page = box
    move(page)
    assert asked(page, 1) == [
        {"url": url(page, goes), "kind": kind, "patch": False, "form": form, "cancelable": kind != "redirect"}
    ]
    detail = landed(page, 1)[0]
    assert detail["kind"] == kind
    assert detail["url"] == url(page, lands)
    assert len(page.evaluate("window.__before")) == 1, "asked once"


@pytest.mark.parametrize(("testid", "kind", "path"), [("push-b", "push", "?tab=b"), ("replace-z", "replace", "?tab=z")])
def test_a_patch_asks_too_and_lands_nothing_to_announce(box, heard, testid, kind, path):
    page = box
    by(page, testid).click()
    at(page, f"/historyprobe/{path}")
    expect_text(by(page, "tab"), path[-1])
    assert asked(page, 1) == [
        {"url": url(page, f"/historyprobe/{path}"), "kind": kind, "patch": True, "form": None, "cancelable": True}
    ]
    settled(page)
    assert page.evaluate("window.__landed") == [], "a patch is not a navigation (#169)"


def test_back_and_forward_say_popstate(box):
    page = box
    by(page, "to-other").click()
    assert landed(page, 1)[0]["kind"] == "link"
    by(page, "push-b").click()
    at(page, "/historyprobe/other/?tab=b")

    page.go_back()
    at(page, "/historyprobe/other/")
    # Between two entries the page on screen made: a patch
    assert asked(page, 3)[2] == {
        "url": url(page, "/historyprobe/other/"),
        "kind": "popstate",
        "patch": True,
        "form": None,
        "cancelable": True,
    }
    page.go_back()
    at(page, "/historyprobe/")
    assert asked(page, 4)[3]["patch"] is False
    detail = landed(page, 2)[1]
    assert detail["kind"] == "popstate"
    assert detail["url"] == url(page, "/historyprobe/")


def test_a_fragment_link_is_the_browsers_and_asks_nothing(box):
    page = box
    here = page.url
    by(page, "to-section").click()
    page.wait_for_function("url => location.href === url", arg=f"{here}#section", timeout=WAIT_TIMEOUT * 1000)
    page.go_back()
    page.wait_for_function("url => location.href === url", arg=here, timeout=WAIT_TIMEOUT * 1000)
    settled(page)
    assert page.evaluate("window.__before") == []


# --- cancelled -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "testid",
    ["to-other", "get-send", "post-send", "push-other", "replace-other", "push-b", "replace-z"],
)
def test_a_cancelled_move_leaves_everything_where_it_was(box, heard, testid):
    page = box
    from testproj.historyprobe.urls import POSTS

    POSTS.clear()
    bump_to(page, 1)
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate("window.__cancel = true")
    mark = len(page.sent.commands)

    by(page, testid).click()
    asked(page, 1)
    unmoved(page, path="/historyprobe/", length=length, mark=mark, heard=heard, rendered=rendered)
    # Every move a button asks the server for sends that event, and nothing after it
    expect_text(by(page, "tab"), "a")
    expect_text(by(page, "page"), "box")
    assert POSTS == [], "the form was not sent"

    # Nothing is held back: the next one goes
    page.evaluate("window.__cancel = null")
    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")


def test_a_redirect_is_told_and_goes(box):
    """``redirect_to`` froze the component that sent it: a page that stayed would
    keep a component that answers nothing, so the event cannot hold it back."""
    page = box
    page.evaluate("window.__cancel = true")
    by(page, "redirect-d").click()
    at(page, "/historyprobe/?tab=d")
    assert asked(page, 1)[0]["cancelable"] is False
    assert landed(page, 1)[0]["kind"] == "redirect"
    expect_text(by(page, "page-tab"), "d")


def test_a_cancelled_visit_resolves_false_and_stays(box, heard):
    page = box
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate("window.__cancel = 'visit'")
    mark = len(page.sent.commands)
    assert page.evaluate("wireview.visit('/historyprobe/other/')") is False
    unmoved(page, path="/historyprobe/", length=length, mark=mark, heard=heard, rendered=rendered)


def test_a_cancelled_back_returns_to_the_entry_it_left(box, heard):
    page = box
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    by(page, "push-c").click()
    at(page, "/historyprobe/?tab=c")
    expect_text(by(page, "tab"), "c")
    settled(page)
    heard.clear()
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate("window.__cancel = 'popstate'")
    mark = len(page.sent.commands)
    before = len(page.evaluate("window.__before"))

    page.go_back()
    asked(page, before + 1)
    unmoved(page, path="/historyprobe/?tab=c", length=length, mark=mark, heard=heard, rendered=rendered)
    expect_text(by(page, "tab"), "c")
    # The return asked nothing
    assert len(page.evaluate("window.__before")) == before + 1

    # Two back at once, cancelled: two forward again
    page.evaluate("history.go(-2)")
    asked(page, before + 2)
    unmoved(page, path="/historyprobe/?tab=c", length=length, mark=mark, heard=heard, rendered=rendered)

    # History still works both ways afterwards
    page.evaluate("window.__cancel = null")
    page.go_back()
    at(page, "/historyprobe/?tab=b")
    expect_text(by(page, "tab"), "b")
    page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "tab"), "a")
    page.go_forward()
    at(page, "/historyprobe/?tab=b")
    expect_text(by(page, "tab"), "b")


def test_a_cancelled_forward_returns_to_the_entry_it_left(box, heard):
    page = box
    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "page"), "box")
    landed(page, 2)
    settled(page)
    heard.clear()
    page.evaluate("window.__landed = []")
    page.fetches.urls.clear()
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate("window.__cancel = 'popstate'")
    mark = len(page.sent.commands)

    # Forward to another page: it would fetch, and is not let
    page.go_forward()
    unmoved(page, path="/historyprobe/", length=length, mark=mark, heard=heard, rendered=rendered)
    expect_text(by(page, "page"), "box")

    page.evaluate("window.__cancel = null")
    page.go_forward()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    assert landed(page, 1)[0]["kind"] == "popstate"
    page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "page"), "box")


# --- entries boost never stamped, and a traversal queued before the undo (review of #154) ---------


def entries_the_app_made(page) -> None:
    """``?tab=x`` and ``?tab=y`` pushed by page code, not boost, and a reload on y:
    nothing tells how far y is from x, and no guess may be written down."""
    page.evaluate("history.pushState({}, '', '?tab=x'); history.pushState({}, '', '?tab=y')")
    page.reload()
    wait_live(page)
    expect_text(by(page, "tab"), "y")
    page.evaluate("window.__samePage = true")


def tab_at(page, tab: str) -> None:
    at(page, f"/historyprobe/?tab={tab}")
    expect_text(by(page, "tab"), tab)


def test_a_cancelled_forward_to_an_entry_the_app_made_returns_to_it(box, heard):
    page = box
    entries_the_app_made(page)
    page.go_back()
    tab_at(page, "x")
    landed(page, 1)
    settled(page)
    heard.clear()
    page.fetches.urls.clear()
    page.evaluate("window.__landed = []")
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    before = len(page.evaluate("window.__before"))
    page.evaluate("window.__cancel = 'popstate'")
    mark = len(page.sent.commands)

    page.go_forward()
    assert asked(page, before + 1)[-1]["cancelable"] is True
    unmoved(page, path="/historyprobe/?tab=x", length=length, mark=mark, heard=heard, rendered=rendered)
    expect_text(by(page, "tab"), "x")

    # History is whole: Forward, then a patch, Back and Forward over it
    page.evaluate("window.__cancel = null")
    page.go_forward()
    tab_at(page, "y")
    by(page, "push-b").click()
    tab_at(page, "b")
    page.go_back()
    tab_at(page, "y")
    page.go_forward()
    tab_at(page, "b")


def test_a_cancelled_back_to_an_entry_the_app_made_returns_to_it(box, heard):
    page = box
    entries_the_app_made(page)
    settled(page)
    heard.clear()
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate("window.__cancel = 'popstate'")
    mark = len(page.sent.commands)

    page.go_back()
    assert asked(page, 1)[0]["cancelable"] is True
    unmoved(page, path="/historyprobe/?tab=y", length=length, mark=mark, heard=heard, rendered=rendered)
    expect_text(by(page, "tab"), "y")

    page.evaluate("window.__cancel = null")
    page.go_back()
    tab_at(page, "x")
    page.go_forward()
    tab_at(page, "y")
    by(page, "push-b").click()
    tab_at(page, "b")
    page.go_back()
    tab_at(page, "y")


def test_a_back_queued_before_the_undo_does_not_take_the_page_elsewhere(box, heard):
    """A second Back that runs before the undo -- as a fast double press can --
    is overridden by it: the page ends on the entry it never left."""
    page = box
    for tab in ("b", "c"):
        by(page, f"push-{tab}").click()
        tab_at(page, tab)
    settled(page)
    heard.clear()
    rendered = by(page, "rendered").inner_text()
    length = page.evaluate("history.length")
    page.evaluate(
        """() => {
          window.__before = [];
          window.__cancel = "popstate";
          window.__left = navigation.currentEntry.key;
          window.__pops = [];
          window.addEventListener("popstate", () => window.__pops.push(location.search));
          // Queued before the undo the cancel starts
          document.addEventListener("wireview:before-navigate", (e) => {
            if (e.detail.kind === "popstate" && !window.__again) {
              window.__again = true;
              history.back();
            }
          });
        }"""
    )
    mark = len(page.sent.commands)

    page.go_back()
    # Back on the very entry it left. Whether the second Back is seen on the way
    # is the browser's: Chromium runs it (b, then a, then c), WebKit folds it
    # into the undo (b, then c). The order TraversalUndo is told is checked in
    # tests/js/navigation.test.mjs.
    page.wait_for_function(
        "navigation.currentEntry.key === window.__left && window.__pops.at(-1) === '?tab=c'",
        timeout=WAIT_TIMEOUT * 1000,
    )
    pops = page.evaluate("window.__pops")
    assert pops[0] == "?tab=b" and pops[-1] == "?tab=c" and set(pops[1:-1]) <= {""}, pops
    unmoved(page, path="/historyprobe/?tab=c", length=length, mark=mark, heard=heard, rendered=rendered)
    expect_text(by(page, "tab"), "c")
    assert len(page.evaluate("window.__before")) == 1, "the Back the undo overrode asked nothing"

    page.evaluate("window.__cancel = null")
    page.go_back()
    tab_at(page, "b")


def test_an_undo_the_page_refuses_lands_where_the_address_bar_is(box, heard):
    """Page code may refuse the undo's traversal (a Navigation API ``navigate``
    listener): the page then arrives where the address bar went, as a traversal
    it could not stop, and nothing escapes as an unhandled rejection."""
    page = box
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for tab in ("b", "c"):
        by(page, f"push-{tab}").click()
        tab_at(page, tab)
    settled(page)
    heard.clear()
    page.evaluate(
        """() => {
          window.__before = [];
          window.__cancel = "popstate";
          window.__rejections = [];
          addEventListener("unhandledrejection", (e) => window.__rejections.push(String(e.reason)));
          const left = navigation.currentEntry.key;
          navigation.addEventListener("navigate", (e) => {
            if (e.destination.key === left) e.preventDefault();
          });
        }"""
    )

    page.go_back()
    # Announced twice: cancelled, then -- the undo refused -- going ahead
    assert [(each["url"], each["cancelable"]) for each in asked(page, 2)] == [
        (url(page, "/historyprobe/?tab=b"), True),
        (url(page, "/historyprobe/?tab=b"), False),
    ]
    tab_at(page, "b")
    settled(page)
    assert [uri for _id, _rendered, uri in heard if _id == "hbox"] == [url(page, "/historyprobe/?tab=b")]

    # History goes on as usual. (WebKit, after a refused traversal, takes the
    # next Back to the entry it is on and reloads it -- a bare page with no
    # wireview does the same -- so this part holds where history does.)
    page.evaluate("window.__cancel = null")
    page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "tab"), "a")
    settled(page)
    assert page.evaluate("window.__rejections") == []
    assert errors == []


def test_without_the_navigation_api_back_is_announced_and_not_cancelable(page, server):
    page.add_init_script("Object.defineProperty(window, 'navigation', { value: undefined })")
    page.add_init_script(LISTENER)
    open_live(page, f"{server}/historyprobe/")
    by(page, "push-b").click()
    tab_at(page, "b")
    page.evaluate("window.__cancel = true")

    page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "tab"), "a")
    assert asked(page, 2)[1] == {
        "url": urljoin(page.url, "/historyprobe/"),
        "kind": "popstate",
        "patch": True,
        "form": None,
        "cancelable": False,
    }


def test_a_guard_that_asks_the_user(box, heard):
    """The documented guard (docs/features/boost.md): ``confirm()`` while the form is dirty."""
    page = box
    page.evaluate(
        """() => {
          window.__cancel = null;
          window.__dirty = true;
          document.addEventListener("wireview:before-navigate", (e) => {
            if (window.__dirty && !confirm("저장하지 않은 변경이 있습니다. 떠날까요?")) e.preventDefault();
          });
        }"""
    )
    answers = iter([False, True])
    page.on("dialog", lambda dialog: dialog.accept() if next(answers) else dialog.dismiss())

    by(page, "to-other").click()
    asked(page, 1)
    settled(page)
    expect_text(by(page, "page"), "box")
    at(page, "/historyprobe/")

    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect(by(page, "page")).to_have_text("other", timeout=WAIT_TIMEOUT * 1000)
