"""A LiveComponent a nested component hides by its drawer's prop, in a browser.

The server-side contract is tests/test_inline_pass_lifecycle.py. Here the page
applies the root's render, which carries the leaf's lifecycle: the leaf the box
hid has left, the one it shows again starts anew and answers its own events,
and a note the root passes through the box reaches it.

Fixture: tests/testproj/nestprobe/ (``?hidden=1``: the box starts with the leaf hidden;
the ``visit-shown`` link is a boosted visit to the page with it shown).
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


@pytest.fixture
def heard():
    from testproj.nestprobe.live import HEARD

    HEARD.clear()
    yield HEARD
    HEARD.clear()


def _bump(page, times: int) -> None:
    for count in range(1, times + 1):
        page.get_by_test_id("bump").click()
        expect_text(page.get_by_test_id("leaf-count"), str(count))


def test_the_leaf_the_box_hid_leaves_and_comes_back_anew(page, server, heard):
    open_live(page, f"{server}/nestprobe/")
    _bump(page, 3)

    page.get_by_test_id("toggle").click()
    expect_text(page.get_by_test_id("box-shown"), "False")
    expect_count(page.get_by_test_id("leaf-count"), 0)
    assert ("leaving", "nest-leaf", 3) in heard

    page.get_by_test_id("toggle").click()
    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 1)


def test_a_note_the_root_passes_through_the_box_reaches_the_leaf(page, server, heard):
    open_live(page, f"{server}/nestprobe/")
    _bump(page, 2)

    page.get_by_test_id("write").click()

    expect_text(page.get_by_test_id("leaf-note"), "!")
    expect_text(page.get_by_test_id("leaf-count"), "2")


def test_a_leaf_the_box_shows_first_is_drawn_and_answers(page, server, heard):
    open_live(page, f"{server}/nestprobe/?hidden=1")
    expect_count(page.get_by_test_id("leaf-count"), 0)

    page.get_by_test_id("toggle").click()

    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 2)
    assert [hook for hook, *_ in heard] == ["joined"]


def test_a_boosted_visit_joins_the_leaf_once(page, server, heard):
    # The visit brings the root and the box as new elements under the same ids
    # and the page joins both, the root first. The root's join render is not
    # the one to settle the box's leaf: the box's join right behind it is.
    open_live(page, f"{server}/nestprobe/?hidden=1")

    page.get_by_test_id("visit-shown").click()

    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 2)
    assert [hook for hook, *_ in heard].count("joined") == 1, heard

    # From then on, the root's render settles the box's leaf again
    page.get_by_test_id("toggle").click()
    expect_count(page.get_by_test_id("leaf-count"), 0)
    assert heard[-1] == ("leaving", "nest-leaf", 2), heard
