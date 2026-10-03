"""Back and Forward after each way a page moves (#168, #169).

What the screen holds after a move, in a browser:

- ``push_to`` on the page's own path (another query or fragment) is a patch:
  nothing is fetched, the connection stays, and the components on the page --
  its LiveComponents too -- hear ``params_changed`` with the state events gave
  them. Back and Forward between such entries are patches too, while the page
  that made them is the one on screen.
- ``push_to`` to another path fetches it and its components join from that
  render; so does Back or Forward to an entry another page made, or one made
  before a reload. What events changed is the state of a fresh page there.
- ``replace_to`` is the same as ``push_to`` without the history entry.
- ``redirect_to`` always fetches: the way out for a page whose template reads the
  query outside every component.
- Crossing a live_session boundary is a full page load.
- While a move fetches its page, nothing is a patch: the screen holds the page
  being left, or a cached copy Back painted, and not the page an entry or a
  push would patch.

``rendered`` names the page render the component was built from: a new number
means the URL was fetched again. ``window.__samePage`` is lost on a page load
and kept by a boosted move. ``Traffic`` counts the page fetches and sockets.

Fixture: tests/testproj/historyprobe/.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


@pytest.fixture
def box(page, server):
    open_live(page, f"{server}/historyprobe/")
    page.evaluate("window.__samePage = true")
    return page


def rendered(page) -> int:
    return int(by(page, "rendered").inner_text())


def at(page, path: str) -> None:
    page.wait_for_function(
        "path => location.pathname + location.search === path", arg=path, timeout=WAIT_TIMEOUT * 1000
    )


def bump_to(page, count: int) -> None:
    by(page, "bump").click()
    expect_text(by(page, "count"), str(count))


def screen(page, *, page_name: str, tab: str, count: int) -> None:
    expect_text(by(page, "page"), page_name)
    expect_text(by(page, "tab"), tab)
    expect_text(by(page, "count"), str(count))


class Traffic:
    """The page fetches a boosted move makes, and the sockets the page opens."""

    def __init__(self, page) -> None:
        self.fetches: list[str] = []
        self.sockets = 0
        page.on("request", self._request)
        page.on("websocket", self._socket)

    def _request(self, request) -> None:
        if request.resource_type == "fetch" and "/historyprobe/" in request.url:
            self.fetches.append(request.url)

    def _socket(self, _socket) -> None:
        self.sockets += 1


@pytest.fixture
def traffic(box):
    return Traffic(box)


def patched(page, traffic: Traffic, built: int) -> None:
    """Nothing was fetched or reconnected, and the component is the one the page built."""
    assert traffic.fetches == [], traffic.fetches
    assert traffic.sockets == 0
    assert rendered(page) == built
    assert page.evaluate("window.__samePage === true")


# --- push_to on the same path: a patch --------------------------------------------------


def test_push_to_on_the_same_path_keeps_the_page_and_its_state(box, traffic):
    page = box
    bump_to(page, 1)
    by(page, "leaf-bump").click()
    expect_text(by(page, "leaf-count"), "1")
    built = rendered(page)
    before = page.evaluate("history.length")

    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    screen(page, page_name="box", tab="b", count=1)
    # The LiveComponent keeps its state and hears the params too
    expect_text(by(page, "leaf-count"), "1")
    expect_text(by(page, "leaf-heard"), "b")
    assert page.evaluate("history.length") == before + 1
    # The page outside the components was not fetched: it still shows the old query
    expect_text(by(page, "page-tab"), "-")
    patched(page, traffic, built)

    bump_to(page, 2)


def test_back_and_forward_between_patches_only_tell_the_params(box, traffic):
    page = box
    built = rendered(page)
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    by(page, "push-c").click()
    at(page, "/historyprobe/?tab=c")
    bump_to(page, 1)

    page.go_back()
    at(page, "/historyprobe/?tab=b")
    screen(page, page_name="box", tab="b", count=1)
    expect_text(by(page, "leaf-heard"), "b")

    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=1)
    bump_to(page, 2)

    page.go_forward()
    page.go_forward()
    at(page, "/historyprobe/?tab=c")
    screen(page, page_name="box", tab="c", count=2)
    patched(page, traffic, built)


# --- the test helper says what the browser does ---------------------------------------------


def helper_says(handler: str, **kwargs) -> tuple[str, int, bool]:
    """``tab``, ``count`` and whether the instance is the one mounted, after a bump
    and ``handler``, followed with ``follow_push()`` the way it asks to be.

    On its own thread: the Playwright thread has an event loop running already.
    """
    from testproj.historyprobe.live import HistoryBox

    from wireview.testing import mount

    async def run() -> tuple[str, int, bool]:
        view = await mount(HistoryBox, path="/historyprobe/")
        await view.call("bump")
        await view.call(handler, **kwargs)
        nav = view.navigations[-1]
        followed = view
        if nav.path and nav.path != "/historyprobe/":
            followed = await view.follow_push(HistoryBox)
        else:
            await view.follow_push()
        return followed.component.tab, followed.component.count, followed is view

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, run()).result()


@pytest.mark.parametrize(
    ("button", "handler", "kwargs", "path"),
    [
        ("push-b", "push", {"tab": "b"}, "/historyprobe/?tab=b"),
        ("replace-z", "replace", {"tab": "z"}, "/historyprobe/?tab=z"),
        ("push-other", "push_other", {}, "/historyprobe/other/?tab=o"),
        ("replace-other", "replace_other", {}, "/historyprobe/other/?tab=o"),
    ],
)
def test_follow_push_and_the_browser_agree(box, traffic, button, handler, kwargs, path):
    page = box
    bump_to(page, 1)
    first = rendered(page)
    by(page, button).click()
    at(page, path)

    tab, count, same = helper_says(handler, **kwargs)
    if same:
        # The params_changed render. A fetch would have been asked for before it.
        expect_text(by(page, "leaf-heard"), tab)
        assert traffic.fetches == [], "the helper kept the instance; the browser fetched the page"
        assert rendered(page) == first
    else:
        # The fetched page's component, once it is on screen; its join then
        # renders the params (the HTTP render shows the default)
        expect(by(page, "rendered")).not_to_have_text(str(first), timeout=WAIT_TIMEOUT * 1000)
    expect_text(by(page, "tab"), tab)
    # The fetched page's LiveComponent hears the params after joining, whatever
    # the old page's one under its id last heard (#170)
    expect_text(by(page, "leaf-heard"), tab)
    assert by(page, "count").inner_text() == str(count)


# --- replace_to ---------------------------------------------------------------------------


def test_replace_to_on_the_same_path_keeps_the_state_and_the_history_length(box, traffic):
    page = box
    built = rendered(page)
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    bump_to(page, 1)
    before = page.evaluate("history.length")

    by(page, "replace-z").click()
    at(page, "/historyprobe/?tab=z")
    screen(page, page_name="box", tab="z", count=1)
    assert page.evaluate("history.length") == before

    # The rewritten entry is the page's own: Back and Forward stay patches
    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=1)

    page.go_forward()
    at(page, "/historyprobe/?tab=z")
    screen(page, page_name="box", tab="z", count=1)
    patched(page, traffic, built)


def test_replace_to_another_path_fetches_it_in_place(box, traffic):
    page = box
    bump_to(page, 1)
    before = page.evaluate("history.length")

    by(page, "replace-other").click()
    at(page, "/historyprobe/other/?tab=o")
    screen(page, page_name="other", tab="o", count=0)
    expect_text(by(page, "page-tab"), "o")
    assert page.evaluate("history.length") == before
    assert traffic.fetches, "another path is another page"
    assert traffic.sockets == 0
    assert page.evaluate("window.__samePage === true")


# --- push_to to another path, redirect_to: a fetch -------------------------------------


def test_push_to_another_path_fetches_it(box, traffic):
    page = box
    bump_to(page, 1)
    first = rendered(page)

    by(page, "push-other").click()
    at(page, "/historyprobe/other/?tab=o")
    screen(page, page_name="other", tab="o", count=0)
    expect_text(by(page, "page-tab"), "o")
    assert rendered(page) > first
    assert traffic.fetches
    assert traffic.sockets == 0

    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)
    assert page.evaluate("window.__samePage === true")


def test_redirect_to_on_the_same_path_fetches_the_page(box, traffic):
    """The way out for a page whose template reads the query outside its components."""
    page = box
    bump_to(page, 1)
    first = rendered(page)

    by(page, "redirect-d").click()
    at(page, "/historyprobe/?tab=d")
    screen(page, page_name="box", tab="d", count=0)
    expect_text(by(page, "page-tab"), "d")
    assert rendered(page) > first
    assert traffic.fetches
    assert page.evaluate("window.__samePage === true")


# --- history that a boosted move or a reload interrupted ---------------------------------


def test_entries_another_page_made_are_fetched_again(box):
    page = box
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    bump_to(page, 1)
    box_built = rendered(page)

    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    bump_to(page, 1)

    # ?tab=b was the box page's patch; the page on screen is another one now
    page.go_back()
    at(page, "/historyprobe/?tab=b")
    screen(page, page_name="box", tab="b", count=0)
    assert rendered(page) > box_built
    bump_to(page, 1)
    fetched = rendered(page)

    # The box page that made "/" is gone too: the one landed now is a new page
    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)
    assert rendered(page) > fetched
    bump_to(page, 1)
    landed = rendered(page)

    # ... and makes patches of its own
    by(page, "push-c").click()
    at(page, "/historyprobe/?tab=c")
    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=1)
    assert rendered(page) == landed

    page.go_forward()
    at(page, "/historyprobe/?tab=c")
    screen(page, page_name="box", tab="c", count=1)
    assert rendered(page) == landed
    assert page.evaluate("window.__samePage === true")


def test_back_after_a_reload_fetches_the_entry(box):
    page = box
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    bump_to(page, 1)

    page.reload()
    wait_live(page)
    screen(page, page_name="box", tab="b", count=0)
    expect_text(by(page, "page-tab"), "b")
    page.evaluate("window.__samePage = true")
    reloaded = rendered(page)

    # Made by the page before the reload, which is not the page on screen
    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)
    expect_text(by(page, "page-tab"), "-")
    assert rendered(page) > reloaded
    assert page.evaluate("window.__samePage === true"), "fetched by a boosted move, not loaded"


# --- while a page is being fetched --------------------------------------------------------


class Held:
    """Holds the page fetches of one URL until released: a slow network, on cue."""

    def __init__(self, page, url: str) -> None:
        self.page = page
        self.url = url
        self.routes: list = []
        page.route(url, self._hold)

    def _hold(self, route) -> None:
        if route.request.resource_type == "fetch":
            self.routes.append(route)
        else:
            route.continue_()

    @contextmanager
    def requested(self):
        """The block makes the page fetch the URL; wait until it has asked."""
        with self.page.expect_request(lambda r: r.url == self.url and r.resource_type == "fetch"):
            yield

    def release(self) -> None:
        """Let the held fetches answer, and the page run what it does with them."""
        assert self.routes, "nothing was held"
        with self.page.expect_response(lambda r: r.url == self.url):
            for route in self.routes:
                route.continue_()
        self.routes.clear()
        # The answer is read, parsed and judged before anything is morphed in a
        # frame; two frames later a stale one has been dropped or painted
        self.page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def test_forward_after_a_cached_paint_fetches_the_page_it_returns_to(box):
    """Back paints its cached copy of the box page while the fetch is still out; the
    page on screen is a copy now, not the "other" page that made the next entry.
    Forward to it is a fetch -- were it a patch, the copy would stay under the
    other page's address (#169)."""
    page = box
    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")

    held = Held(page, urljoin(page.url, "/historyprobe/"))
    with held.requested():
        page.go_back()
    at(page, "/historyprobe/")
    expect_text(by(page, "page"), "box")  # the cached copy

    page.go_forward()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    held.release()
    expect_text(by(page, "page"), "other")
    at(page, "/historyprobe/other/")
    bump_to(page, 1)  # its component joined


def test_a_push_from_the_page_being_left_is_judged_by_that_page(box):
    """push-other's fetch is out and the box page is still on screen when its
    component pushes "?tab=b". The address bar names the other page already; the
    push is not a patch of either (#169)."""
    page = box
    held = Held(page, urljoin(page.url, "/historyprobe/other/?tab=o"))
    with held.requested():
        by(page, "push-other").click()
    at(page, "/historyprobe/other/?tab=o")
    expect_text(by(page, "page"), "box")  # still the page being left

    by(page, "push-b").click()
    at(page, "/historyprobe/other/?tab=b")
    expect_text(by(page, "page"), "other")
    expect_text(by(page, "page-tab"), "b")
    held.release()
    at(page, "/historyprobe/other/?tab=b")
    screen(page, page_name="other", tab="b", count=0)
    expect_text(by(page, "page-tab"), "b")


# --- live_session ---------------------------------------------------------------------------


@pytest.fixture
def members_box(page, server):
    page.goto(f"{server}/livesession/sign-in/?staff=0&next=/historyprobe/members/")
    wait_live(page)
    expect_text(by(page, "page"), "members")
    page.evaluate("window.__samePage = true")
    return page


def test_a_patch_inside_a_boundary_stays_inside_it(members_box):
    page = members_box
    traffic = Traffic(page)
    bump_to(page, 1)
    built = rendered(page)

    by(page, "push-b").click()
    at(page, "/historyprobe/members/?tab=b")
    screen(page, page_name="members", tab="b", count=1)
    page.go_back()
    at(page, "/historyprobe/members/")
    screen(page, page_name="members", tab="a", count=1)
    patched(page, traffic, built)


def test_push_to_a_path_outside_the_boundary_loads_the_page(members_box):
    page = members_box
    by(page, "push-other").click()
    at(page, "/historyprobe/other/?tab=o")
    wait_live(page)
    screen(page, page_name="other", tab="o", count=0)
    assert not page.evaluate("window.__samePage === true"), "leaving the boundary is a page load"


def test_forward_across_a_live_session_boundary_reloads(page, server):
    """Back across one is tests/test_live_session_e2e.py's; Forward is the same
    popstate the other way, and must not carry the page into the boundary either."""
    page.goto(f"{server}/livesession/sign-in/?next=/livesession/public/")
    wait_live(page)
    by(page, "to-members").click()
    expect_text(by(page, "page"), "members")
    page.go_back()
    expect_text(by(page, "page"), "public")
    page.evaluate("window.__samePage = true")

    page.go_forward()
    expect_text(by(page, "page"), "members")
    wait_live(page)
    assert not page.evaluate("window.__samePage === true"), "crossing into the boundary is a page load"


# --- who hears a navigation's params (#170) -------------------------------------------------

DOCK = "sticky-testproj-historyprobe-live-HistoryDock"
TRAY = "sticky-testproj-historyprobe-live-HistoryTray"


@pytest.fixture
def heard():
    """Every params_changed the historyprobe components heard on the server, from here on."""
    from testproj.historyprobe.live import HEARD

    HEARD.clear()
    yield HEARD
    HEARD.clear()


def settled(page) -> None:
    """A round trip after everything the page sent so far: the server has handled it all."""
    count = int(by(page, "count").inner_text())
    bump_to(page, count + 1)


def test_a_navigation_tells_its_params_to_the_new_page_and_the_sticky_dock_only(box, heard):
    page = box
    old = rendered(page)

    by(page, "push-other").click()
    at(page, "/historyprobe/other/?tab=o")
    screen(page, page_name="other", tab="o", count=0)
    expect_text(by(page, "leaf-heard"), "o")
    expect_text(by(page, "dock-heard"), "o")
    settled(page)

    landed = rendered(page)
    destination = urljoin(page.url, "/historyprobe/other/?tab=o")
    # The page left never heard where it went -- the tray, sticky but not on
    # the next page, neither; the one landed heard it through its join, the box
    # before its LiveComponent; the dock was carried and told
    assert [entry for entry in heard if entry[1] == old or entry[0] == TRAY] == []
    assert heard == [
        (DOCK, 0, destination),
        ("obox", landed, "?tab=o"),
        ("leaf", 0, "?tab=o"),
    ]
    expect_text(by(page, "dock-times"), "1")


def test_a_back_with_a_cached_paint_tells_the_dock_once(box, heard):
    page = box
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    expect_text(by(page, "dock-times"), "1")  # a patch tells everyone

    cached = rendered(page)
    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    expect_text(by(page, "dock-times"), "2")
    left = rendered(page)

    # The cached box page is painted and joins, then the fetched one lands and
    # joins: two navigations' worth of joins under one URL, one for the dock
    page.go_back()
    at(page, "/historyprobe/?tab=b")
    page.wait_for_function(
        "seen => !seen.includes(document.querySelector('[data-testid=rendered]').textContent)",
        arg=[str(left), str(cached)],
        timeout=WAIT_TIMEOUT * 1000,
    )
    screen(page, page_name="box", tab="b", count=0)
    settled(page)
    expect_text(by(page, "dock-heard"), "b")
    assert by(page, "dock-times").inner_text() == "3"
    assert [uri for component, _rendered, uri in heard if component == DOCK].count(
        urljoin(page.url, "/historyprobe/?tab=b")
    ) == 2  # the patch, and the Back


def test_a_live_component_hears_the_first_params_of_the_page(page, server, heard):
    open_live(page, f"{server}/historyprobe/?tab=b")
    expect_text(by(page, "tab"), "b")
    expect_text(by(page, "leaf-heard"), "b")
    built = rendered(page)
    assert [entry for entry in heard if entry[0] in ("hbox", "leaf")] == [
        ("hbox", built, "?tab=b"),
        ("leaf", 0, "?tab=b"),
    ]


# --- a push to the URL on screen (#170) -------------------------------------------------------


def test_a_push_to_the_url_on_screen_makes_no_entry_and_still_tells_the_params(box, traffic, heard):
    page = box
    built = rendered(page)
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    bump_to(page, 1)
    before = page.evaluate("history.length")
    told = len(heard)

    by(page, "push-same").click()
    settled(page)
    assert page.evaluate("history.length") == before
    assert len(heard) > told, "the components hear their params, as Phoenix's handle_params runs"

    # Back leaves the URL rather than landing on a copy of it
    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=2)
    patched(page, traffic, built)


def test_a_link_to_the_url_on_screen_fetches_it_in_place(box, traffic):
    page = box
    first = rendered(page)
    before = page.evaluate("history.length")

    by(page, "to-box").click()
    expect(by(page, "rendered")).not_to_have_text(str(first), timeout=WAIT_TIMEOUT * 1000)
    at(page, "/historyprobe/")
    assert page.evaluate("history.length") == before, "as the browser reloads a link to its own URL"
    assert traffic.fetches
    assert page.evaluate("window.__samePage === true")


# --- a link to a fragment of the page (#170) ---------------------------------------------------


def hashed(page, url: str) -> None:
    page.wait_for_function("url => location.href === url", arg=url, timeout=WAIT_TIMEOUT * 1000)


def test_a_fragment_link_is_the_browsers(box, traffic, heard):
    page = box
    built = rendered(page)
    here = page.url
    before = page.evaluate("history.length")

    by(page, "to-section").click()
    hashed(page, f"{here}#section")
    assert page.evaluate("history.length") == before + 1
    # Back and Forward over it move only the fragment
    page.go_back()
    hashed(page, here)
    page.go_forward()
    hashed(page, f"{here}#section")
    settled(page)
    assert heard == [], "nothing to tell: only the fragment moved"

    # A patch from there, and Back to the entry the fragment link made: the
    # page on screen made it, so it is a patch too
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    page.go_back()
    hashed(page, f"{here}#section")
    screen(page, page_name="box", tab="a", count=1)
    patched(page, traffic, built)
