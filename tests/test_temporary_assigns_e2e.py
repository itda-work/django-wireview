"""A reset temporary assign stays on the page, in a browser (#111).

The server-side contract is tests/test_temporary_assigns_diff.py. Here the
client applies what the server sent: before #111 an event that had nothing to
do with the list sent it emptied, and the list vanished.

Fixture: tests/testproj/tempprobe/.
"""

import pytest
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


def test_the_list_stays_on_the_page_after_an_unrelated_event(page, server):
    open_live(page, f"{server}/tempprobe/")
    page.get_by_test_id("load").click()
    expect_count(page.locator("[data-testid=messages] li"), 3)

    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "1")

    expect_count(page.locator("[data-testid=messages] li"), 3)
    expect_text(page.get_by_test_id("how-many"), "3")


def test_loading_again_replaces_it(page, server):
    open_live(page, f"{server}/tempprobe/")
    page.get_by_test_id("load").click()
    expect_count(page.locator("[data-testid=messages] li"), 3)
    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "1")

    page.get_by_test_id("load").click()

    expect_count(page.locator("[data-testid=messages] li"), 3)
    expect_text(page.locator("[data-testid=messages] li").first, "one")
