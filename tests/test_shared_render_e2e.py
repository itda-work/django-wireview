"""``Meta.shared_render`` in browsers: several viewers, one broadcast (#176, stage 2).

The server-side contract is tests/test_shared_render.py. Here three browser
contexts, each signed in as another user, open the same page: a board that
declares ``shared_render`` and a greeting that names its viewer and does not.
One of them announces; every page must show the new board, and every greeting
its own user.

Fixture: tests/testproj/shareprobe/.
"""

import pytest
from testproj.e2e_browser import expect_count, expect_text, wait_live
from testproj.e2e_server import serve, server_errors
from testproj.shareprobe import live
from testproj.wireview_setting import set_wireview

pytestmark = pytest.mark.e2e

VIEWERS = ("ada", "grace", "linus")


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture(autouse=True)
def _board():
    live.BOARD.update(headline="quiet", items=["one", "two"])
    live.RENDERS.clear()
    yield


def open_viewers(browser, server: str) -> list:
    pages = []
    for name in VIEWERS:
        page = browser.new_context().new_page()
        page.goto(f"{server}/shareprobe/sign-in/{name}/")
        wait_live(page, '[data-name="ShareBoard"][data-is-live="true"]')
        wait_live(page, '[data-name="ShareGreeting"][data-is-live="true"]')
        pages.append(page)
    return pages


def announce_and_check(pages: list) -> None:
    pages[0].get_by_test_id("announce").click()
    for name, page in zip(VIEWERS, pages, strict=True):
        expect_text(page.get_by_test_id("headline"), "breaking")
        expect_count(page.locator("[data-testid=items] li"), 3)
        expect_text(page.locator("[data-testid=items] li").last, "breaking")
        expect_text(page.get_by_test_id("greeting"), f"{name} reads breaking")


@pytest.mark.parametrize("verify", [True, False], ids=["verified", "unverified"])
def test_every_viewer_sees_the_broadcast_and_their_own_greeting(browser, server, monkeypatch, verify):
    """Verified, each viewer that took the board renders it again and compares; unverified, it only takes it."""
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=verify)
    pages = open_viewers(browser, server)
    for name, page in zip(VIEWERS, pages, strict=True):
        expect_text(page.get_by_test_id("greeting"), f"{name} reads quiet")
    live.RENDERS.clear()

    announce_and_check(pages)

    assert server_errors() == []
    if not verify:
        # The clicker's own render of its event, then one render of the broadcast for all three
        assert live.RENDERS["ShareBoard"] == 2
    assert live.RENDERS["ShareGreeting"] == 3, "the undeclared greeting renders on every connection"


def test_a_viewer_who_joins_after_the_broadcast_sees_the_same_board(browser, server, monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    pages = open_viewers(browser, server)
    announce_and_check(pages)

    late = browser.new_context().new_page()
    late.goto(f"{server}/shareprobe/sign-in/margaret/")
    wait_live(late, '[data-name="ShareGreeting"][data-is-live="true"]')
    expect_text(late.get_by_test_id("headline"), "breaking")
    expect_text(late.get_by_test_id("greeting"), "margaret reads breaking")
    # Another broadcast reaches all four alike
    late.get_by_test_id("announce").click()
    for page in [*pages, late]:
        expect_count(page.locator("[data-testid=items] li"), 4)
    assert server_errors() == []
