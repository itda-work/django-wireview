"""Back and Forward after each way a page moves (#168).

What the screen holds after Back or Forward, in a browser:

- The URL and the screen agree. Every history step fetches its URL again and the
  components join from that render: what the URL encodes comes back (``tab``,
  through ``params_changed``); what only events changed (``count``) does not --
  it is the state of a fresh page at that URL, not the one the user left.
- ``push_to`` is itself a fetch of its destination, so it builds the component
  again from that render: event-only state does not survive it either.
- ``replace_to`` and ``self.wire.params`` change the URL in place: the component
  keeps its state, and Back skips the entry they rewrote.

Crossing a live_session boundary is tests/test_live_session_e2e.py's (Back across
one reloads); Forward across one is checked here.

``rendered`` names the page render the component was built from: a new number
means the URL was fetched again. ``window.__samePage`` is lost on a page load
and kept by a boosted move.

Fixture: tests/testproj/historyprobe/.
"""

from __future__ import annotations

import pytest
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


def test_back_and_forward_after_push_to_show_each_url_as_a_fresh_page(box):
    page = box
    bump_to(page, 1)
    first = rendered(page)

    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    # push_to fetched ?tab=b and the component joined from that render
    screen(page, page_name="box", tab="b", count=0)
    assert rendered(page) > first
    bump_to(page, 1)

    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)

    page.go_forward()
    at(page, "/historyprobe/?tab=b")
    screen(page, page_name="box", tab="b", count=0)
    assert page.evaluate("window.__samePage === true"), "history steps are boosted moves, not page loads"


def test_replace_to_keeps_the_state_and_back_skips_the_rewritten_entry(box):
    page = box
    by(page, "push-b").click()
    at(page, "/historyprobe/?tab=b")
    expect_text(by(page, "tab"), "b")
    bump_to(page, 1)
    built = rendered(page)
    before = page.evaluate("history.length")

    by(page, "replace-z").click()
    at(page, "/historyprobe/?tab=z")
    # The same instance, told the new params
    screen(page, page_name="box", tab="z", count=1)
    assert rendered(page) == built
    assert page.evaluate("history.length") == before

    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)

    page.go_forward()
    at(page, "/historyprobe/?tab=z")
    screen(page, page_name="box", tab="z", count=0)


def test_back_and_forward_across_a_boosted_link_fetch_each_page_again(box):
    page = box
    bump_to(page, 1)
    bump_to(page, 2)

    by(page, "to-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")
    bump_to(page, 1)
    other = rendered(page)

    page.go_back()
    at(page, "/historyprobe/")
    # The cached copy of the page is painted first; the fetch that follows wins
    screen(page, page_name="box", tab="a", count=0)

    page.go_forward()
    at(page, "/historyprobe/other/")
    screen(page, page_name="other", tab="a", count=0)
    assert rendered(page) > other
    assert page.evaluate("window.__samePage === true")


def test_back_after_a_push_to_another_path_returns_to_the_url_before_it(box):
    page = box
    by(page, "push-other").click()
    at(page, "/historyprobe/other/")
    expect_text(by(page, "page"), "other")

    page.go_back()
    at(page, "/historyprobe/")
    screen(page, page_name="box", tab="a", count=0)


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
