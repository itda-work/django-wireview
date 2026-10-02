"""A reset temporary assign stays on the page, in a browser (#111).

The server-side contract is tests/test_temporary_assigns_diff.py. Here the
client applies what the server sent: before #111 an event that had nothing to
do with the list sent it emptied, and the list vanished.

Fixture: tests/testproj/tempprobe/ (``?nest=1``: the probe in a host's pass, in a slot, and from a
``{% component_block %}`` with a fill; ``?rows=1``: rows a nested component draws; ``?live=1``: a
LiveComponent kept with the list in a block the host's pass draws; ``?notes=1``: a nested component
kept with the list whose own temporary assign changes).
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
    ("where", "render"),
    [("nested", "host-bump"), ("slotted", "frame-bump"), ("blocked", "host-bump")],
    ids=["host-pass", "slot-owner", "block-with-fill"],
)
def test_the_list_stays_when_the_component_around_it_renders(page, server, where, render):
    """The host's pass draws the probe again, and the frame its slot; their diffs morph the probe.

    ``block-with-fill``: the host's pass draws the fill with its own markers, the probe's own render as text.
    """
    open_live(page, f"{server}/tempprobe/?nest=1")
    probe = page.get_by_test_id(where)
    probe.get_by_test_id("load").click()
    expect_count(probe.locator("[data-testid=messages] li"), 3)

    page.get_by_test_id(render).click()
    expect_text(page.get_by_test_id(render.replace("bump", "count")), "1")

    expect_count(probe.locator("[data-testid=messages] li"), 3)
    expect_text(probe.get_by_test_id("how-many"), "3")
    if where == "blocked":
        expect_text(probe.get_by_test_id("fill"), "filled")


def test_rows_a_nested_component_draws_stay_until_one_moves(page, server):
    """Kept as drawn while no row changed on its own; a row that did is not put back as it was."""
    open_live(page, f"{server}/tempprobe/?rows=1")
    rows = page.get_by_test_id("row-text")
    page.get_by_test_id("load").click()
    expect_count(rows, 3)

    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "1")
    expect_count(rows, 3)

    page.get_by_test_id("shout").first.click()
    expect_text(rows.first, "ONE")
    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "2")
    expect_count(page.get_by_test_id("row-text").filter(has_text="one"), 0)


def test_a_nested_component_that_moved_outside_its_signed_state_is_not_put_back(page, server):
    """Its own temporary assign changed, so its data-state did not; the kept block drew it as before that (I6)."""
    open_live(page, f"{server}/tempprobe/?notes=1")
    items = page.locator("[data-testid=messages] li")
    notes = page.get_by_test_id("notes")
    page.get_by_test_id("load").click()
    expect_count(items, 3)

    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "1")
    expect_count(items, 3)  # its join moved nothing
    page.get_by_test_id("note").click()
    expect_text(notes, "2")

    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "2")
    expect_count(notes.filter(has_text="0"), 0)


def test_a_live_component_the_hosts_render_keeps_stays_with_the_list(page, server):
    """The host's render keeps the block whole, so the LiveComponent in it answers, render after render."""
    open_live(page, f"{server}/tempprobe/?live=1")
    items = page.locator("[data-testid=messages] li")
    hits = page.get_by_test_id("hits")
    page.get_by_test_id("load").click()
    expect_count(items, 3)
    page.get_by_test_id("hit").click()
    expect_text(hits, "1")

    page.get_by_test_id("host-bump").click()
    expect_text(page.get_by_test_id("host-count"), "1")
    expect_count(items, 3)
    page.get_by_test_id("hit").click()
    expect_text(hits, "2")

    page.get_by_test_id("bump").click()
    expect_text(page.get_by_test_id("count"), "1")
    expect_count(items, 3)
    page.get_by_test_id("hit").click()
    expect_text(hits, "3")

    page.get_by_test_id("load").click()
    expect_count(items, 3)
    page.get_by_test_id("hit").click()
    expect_text(hits, "4")
