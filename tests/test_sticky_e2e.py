"""A sticky component survives a boosted navigation, in a browser (GAP-033, #72).

"Survives" means the same instance: its state carries on, the server keeps
handling its events without a fresh join, and the element -- with whatever the
page's JavaScript did to it -- is the one that was there. The plain counter
beside it has the same id on both pages and is the control: it starts over.

Fixture: tests/testproj/stickyprobe/.
"""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


def go(page, where: str) -> None:
    by(page, f"to-{where}").click()
    expect_text(by(page, "page"), where)


def test_the_sticky_component_carries_its_state_and_element_across_a_boosted_move(page, server):
    open_live(page, f"{server}/stickyprobe/a/")
    for _ in range(2):
        by(page, "player-inc").click()
    expect_text(by(page, "player-count"), "2")
    by(page, "plain-inc").click()
    expect_text(by(page, "plain-count"), "1")
    page.evaluate("() => { document.getElementById('player').dataset.mark = 'kept'; window.__notReloaded = true; }")

    go(page, "b")

    assert page.evaluate("() => window.__notReloaded") is True, "the move was a boosted one"
    expect_text(by(page, "player-count"), "2")
    expect(page.locator("#player")).to_have_attribute("data-mark", "kept")
    expect_text(by(page, "plain-count"), "0")

    # The server still has the instance: a fresh join would have started it at the page's 0
    by(page, "player-inc").click()
    expect_text(by(page, "player-count"), "3")


def test_a_page_without_it_ends_it_and_the_next_one_starts_fresh(page, server):
    open_live(page, f"{server}/stickyprobe/a/")
    by(page, "player-inc").click()
    expect_text(by(page, "player-count"), "1")

    go(page, "c")
    expect(page.locator("#player")).to_have_count(0)

    go(page, "a")
    expect_text(by(page, "player-count"), "0")
    by(page, "player-inc").click()
    expect_text(by(page, "player-count"), "1")


def test_its_own_renders_still_reach_it(page, server):
    """The morph leaves a sticky element alone only on a navigation."""
    open_live(page, f"{server}/stickyprobe/b/")
    by(page, "player-inc").click()
    expect_text(by(page, "player-count"), "1")


def test_a_sticky_component_without_an_id_sticks_all_the_same(page, server):
    """The template gave it no id; the one derived from its class pairs it across the move (#128)."""
    open_live(page, f"{server}/stickyprobe/a/")
    ticker = page.locator("[wire-sticky]", has=by(page, "ticker-count"))
    expect(ticker).to_have_id("sticky-testproj-stickyprobe-live-StickyTicker")
    by(page, "ticker-inc").click()
    expect_text(by(page, "ticker-count"), "1")

    go(page, "b")
    expect_text(by(page, "ticker-count"), "1")
    by(page, "ticker-inc").click()
    expect_text(by(page, "ticker-count"), "2")


def html_attr(page, name: str):
    return page.evaluate("(name) => document.documentElement.getAttribute(name)", name)


def test_a_sticky_hook_and_the_page_hear_each_navigation_once(page, server):
    """navigated() is how a sticky hook puts back what the next page's <body> dropped (#128)."""
    html = page.locator("html")
    open_live(page, f"{server}/stickyprobe/a/")
    expect(html).to_have_attribute("data-mounted-player", "1")
    expect(page.locator("body")).to_have_class("with-player")

    go(page, "b")
    expect(html).to_have_attribute("data-navigated-document", "1")
    expect(html).to_have_attribute("data-navigated-url", "/stickyprobe/b/")
    expect(html).to_have_attribute("data-navigated-from", "/stickyprobe/a/")
    expect(html).to_have_attribute("data-navigated-player", "1")
    # The hook put its class back on the new page's <body>
    expect(page.locator("body")).to_have_class("with-player")
    assert html_attr(page, "data-mounted-player") == "1", "the same hook, not a new one"
    assert html_attr(page, "data-destroyed-player") is None

    # Back paints the cached page and then the fetched one: still one navigation
    page.go_back()
    expect_text(by(page, "page"), "a")
    expect(html).to_have_attribute("data-navigated-document", "2")
    expect(html).to_have_attribute("data-navigated-from", "/stickyprobe/b/")
    expect(html).to_have_attribute("data-navigated-player", "2")
    page.wait_for_timeout(300)  # a second announcement would have landed by now
    assert html_attr(page, "data-navigated-document") == "2"
    assert html_attr(page, "data-navigated-player") == "2"

    # A page without the player: its hook is destroyed, not told it moved
    go(page, "c")
    expect(html).to_have_attribute("data-navigated-document", "3")
    expect(html).to_have_attribute("data-destroyed-player", "1")
    assert html_attr(page, "data-navigated-player") == "2"


@pytest.fixture
def late_gate():
    from testproj.stickyprobe.live import LateSticky

    LateSticky.gate.clear()
    try:
        yield LateSticky.gate
    finally:
        LateSticky.gate.set()


def test_a_page_that_draws_as_a_root_what_the_sticky_one_has_yet_to_draw(page, server, late_gate):
    # On "late" the sticky component's join carried the entry of the inner
    # counter the HTTP render drew inside it, and its render -- the work held --
    # leaves it out. The next page keeps the sticky one and draws the counter
    # as a root of its own: the server took that join for a nested one's the
    # sticky component had yet to draw and dropped it, and the counter was dead.
    open_live(page, f"{server}/stickyprobe/late/", selector="#late[data-is-live='true']")
    expect_text(by(page, "late-state"), "loading")
    expect(page.locator("#inner")).to_have_count(0)
    page.evaluate("() => { window.__notReloaded = true; }")

    go(page, "late-root")

    assert page.evaluate("() => window.__notReloaded") is True, "the move was a boosted one"
    expect_text(by(page, "late-state"), "loading")
    by(page, "inner-inc").click()
    expect_text(by(page, "inner-count"), "1")
