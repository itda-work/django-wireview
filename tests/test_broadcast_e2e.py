"""``Broadcast`` in browsers: one page publishes, every page that hears the topic shows it (#178).

The server-side contract is tests/test_broadcast.py. Here two browser contexts
open the same feed and one of them posts, removes, and pings: both pages show
the item, lose it, run the hook and the JS command. A component of another
class that draws a list of the same name and hears the same topic shows none of
it, a target whose join failed gets nothing, and a page that was offline while
an item was posted has it after it reconnects, and hears the next one.

Fixture: tests/testproj/broadcastprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.broadcastprobe import live
from testproj.e2e_browser import OFFLINE_SHIM, expect_count, expect_text, open_live, wait_live
from testproj.e2e_server import serve, server_errors

pytestmark = pytest.mark.e2e

FEED = '[data-name="BroadcastFeed"][data-is-live="true"]:not(.wireview-disconnected)'


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture(autouse=True)
def _posts():
    live.reset()
    yield
    live.reset()


def open_pages(browser, url: str, count: int = 2) -> list:
    pages = []
    for _ in range(count):
        page = browser.new_context().new_page()
        page.add_init_script(OFFLINE_SHIM)
        open_live(page, url, selector=FEED)
        wait_live(page, '[data-name="BroadcastLookalike"][data-is-live="true"]')
        pages.append(page)
    return pages


def post(page, text: str) -> None:
    page.get_by_test_id("text").fill(text)
    page.get_by_test_id("text").press("Enter")


def items(page):
    return page.locator("[data-testid=items] [data-testid=item]")


def test_every_page_shows_removes_and_hears_what_one_page_broadcasts(browser, server):
    first, second = open_pages(browser, f"{server}/broadcastprobe/")

    post(first, "hello")
    post(second, "world")
    for page in (first, second):
        expect_count(items(page), 2)
        expect(items(page).first).to_contain_text("world")
        expect(items(page).last).to_contain_text("hello")

    # A binding inside an item the Broadcast rendered, clicked on the other page
    second.get_by_test_id("remove-1").click()
    for page in (first, second):
        expect_count(items(page), 1)
        expect(items(page).first).to_contain_text("world")

    first.get_by_test_id("ping").click()
    for page in (first, second):
        expect_text(page.get_by_test_id("heard"), "1:1")
        expect(page.get_by_test_id("flag")).to_have_class(re.compile("flagged"))

    # The lookalike draws a list called items and hears the topic: it is not the target
    for page in (first, second):
        expect_count(page.locator("[data-testid=lookalike-items] li"), 0)
    assert server_errors() == []


def test_a_target_whose_join_failed_gets_nothing(browser, server):
    (page,) = open_pages(browser, f"{server}/broadcastprobe/?failing=1", count=1)
    expect(page.locator('[data-name="BroadcastFailing"]')).to_have_class(re.compile("wireview-error"))

    post(page, "hello")
    expect_count(items(page), 1)
    # The feed's own frame came after the failing one's would have, through the same queue
    expect_count(page.locator("[data-testid=failing-items] li"), 0)


def test_a_page_that_was_offline_gets_what_it_missed_and_what_comes_next(browser, server):
    first, second = open_pages(browser, f"{server}/broadcastprobe/")
    post(first, "before")
    expect_count(items(second), 1)

    second.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(second.locator('[data-name="BroadcastFeed"]')).to_have_class(re.compile("wireview-disconnected"))
    post(first, "while away")
    expect_count(items(first), 2)

    second.evaluate("() => { window.__link.offline = false; }")
    wait_live(second, FEED)
    # The join's stream reset reads the rows: what the page missed is there
    expect_count(items(second), 2)
    expect(items(second).first).to_contain_text("while away")

    post(first, "after")
    for page in (first, second):
        expect_count(items(page), 3)
        expect(items(page).first).to_contain_text("after")
    assert server_errors() == []
