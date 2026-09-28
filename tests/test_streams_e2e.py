"""Streams in a real browser: dom_id, limit, and the viewport bindings (#110).

``dom_id`` was only checked through its default, ``limit`` only for surviving the
hop to the client, and ``wire-viewport-top``/``wire-viewport-bottom`` had neither a
test nor a page of documentation. Fixture: tests/testproj/streamprobe/.
"""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def probe(page, server):
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/")
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_dom_id_names_each_item(probe):
    rows = by(probe, "rows").locator("li")
    expect_count(rows, 15)
    expect(rows.first).to_have_id("row-0")
    expect(rows.last).to_have_id("row-14")


def test_limit_keeps_only_the_newest(probe):
    for _ in range(5):
        by(probe, "tick").click()
    ticks = by(probe, "ticks").locator("li")
    expect_text(ticks.first, "tick 5")
    expect_count(ticks, 3)
    expect(ticks).to_have_text(["tick 5", "tick 4", "tick 3"])


def test_scrolling_to_the_bottom_loads_more(probe):
    expect_count(by(probe, "rows").locator("li"), 15)
    by(probe, "sentinel").scroll_into_view_if_needed()
    expect_text(by(probe, "pages"), "2")
    expect_count(by(probe, "rows").locator("li"), 30)
    expect(by(probe, "rows").locator("li").last).to_have_id("row-29")


def test_scrolling_back_to_the_top_calls_the_top_binding(probe):
    expect_count(by(probe, "rows").locator("li"), 15)
    expect_text(by(probe, "newer"), "0")  # in view at load, but nobody scrolled up to it
    probe.mouse.wheel(0, 3000)
    expect_text(by(probe, "pages"), "2")
    probe.mouse.wheel(0, -100000)
    expect_text(by(probe, "newer"), "1")
