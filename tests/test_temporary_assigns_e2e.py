"""A reset temporary assign stays on the page, in a browser (#111).

The server-side contract is tests/test_temporary_assigns_diff.py. Here the
client applies what the server sent: before #111 an event that had nothing to
do with the list sent it emptied, and the list vanished.

Fixture: tests/testproj/tempprobe/ (``?nest=1``: the probe in a host's pass and in a slot).
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


@pytest.mark.parametrize(
    ("where", "render"), [("nested", "host-bump"), ("slotted", "frame-bump")], ids=["host-pass", "slot-owner"]
)
def test_the_list_stays_when_the_component_around_it_renders(page, server, where, render):
    """The host's pass draws the probe again, and the frame its slot; their diffs morph the probe."""
    open_live(page, f"{server}/tempprobe/?nest=1")
    probe = page.get_by_test_id(where)
    probe.get_by_test_id("load").click()
    expect_count(probe.locator("[data-testid=messages] li"), 3)

    page.get_by_test_id(render).click()
    expect_text(page.get_by_test_id(render.replace("bump", "count")), "1")

    expect_count(probe.locator("[data-testid=messages] li"), 3)
    expect_text(probe.get_by_test_id("how-many"), "3")
