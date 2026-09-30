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
