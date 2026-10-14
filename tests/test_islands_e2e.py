"""Islands in a browser (#221).

- An island is mounted once, with the props its element carries. A render that
  changes them hands them over once (``update``); one that does not, never.
  What the island drew is the same node throughout: no render touches it, though
  the island gave the element it draws in an id the render does not have.
- Its ``ctx`` reaches the component: ``push`` resolves behind the render its
  event caused, with the props already handed over; ``on`` hears the
  component's ``push_event``.
- It lives as long as its element: a handler that raises and the join again
  that follows, a dropped connection and the reconnect (``onStatus`` hears
  both) leave it mounted. A boosted move unmounts the island of a component
  the next page does not have, and keeps a sticky component's.
- A definition registered after the page loaded mounts the islands waiting for it.

Fixture: tests/testproj/islandprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

LIVE = "#board[data-is-live='true']:not(.wireview-disconnected)"


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def board(page, server):
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/islandprobe/", selector=LIVE)
    wait_live(page, "#dock[data-is-live='true']")
    page.wait_for_function("() => window.__islands && window.__islands.board && window.__islands.dock")
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def log(page, who: str) -> list:
    return [[what, value] for what, each, value in page.evaluate("window.__islandLog") if each == who]


def test_it_is_mounted_once_and_handed_only_props_that_changed(board):
    page = board
    page.evaluate("window.__drawn = window.__islands.board.drawn")

    by(page, "write-note").click()
    expect_text(by(page, "note"), "x")
    by(page, "zoom-in").click()
    expect_text(by(page, "zoom"), "4")
    by(page, "write-note").click()
    expect_text(by(page, "note"), "xx")

    assert log(page, "board") == [["mount", {"zoom": 3}], ["update", {"zoom": 4}]]
    # The island's own node, kept by every morph since, in the element it was given
    assert page.evaluate("window.__drawn === document.querySelector('#board .drawn')")
    assert page.evaluate("window.__drawn.parentElement === window.__islands.board.ctx.root")
    expect(page.locator("#board [wire-island] > *")).to_have_count(1)
    expect_text(page.locator("#board .drawn"), '{"zoom":4}')


def test_its_context_pushes_to_the_component_and_hears_it(board):
    page = board
    seen = page.evaluate(
        """async () => {
          const { ctx } = window.__islands.board;
          window.__heard = [];
          window.__off = ctx.on("greeted", (payload) => window.__heard.push(payload.to));
          const reply = await ctx.push("bump");
          const greeted = await ctx.push("greet", { to: "map" });
          return { reply, props: ctx.props, greeted };
        }"""
    )
    # The reply comes once the render is on the page, the props handed over
    assert seen["reply"] == {"zoom": 4} and seen["props"] == {"zoom": 4}
    assert seen["greeted"] == {"greeted": True}
    expect_text(by(page, "zoom"), "4")
    # What the handler pushed comes behind its reply: the session sends it once the event is handled
    page.wait_for_function("() => window.__heard.length === 1")
    page.evaluate(
        """async () => {
          window.__off();
          await window.__islands.board.ctx.push("greet", { to: "nobody" });
          await window.__islands.board.ctx.push("bump");
        }"""
    )
    expect_text(by(page, "zoom"), "5")
    assert page.evaluate("window.__heard") == ["map"]


def test_a_handler_that_raises_leaves_it_mounted(board):
    page = board
    by(page, "boom").click()
    expect(page.locator("#board")).not_to_have_class(re.compile("wireview-error"))
    by(page, "zoom-in").click()
    expect_text(by(page, "zoom"), "4")

    assert log(page, "board") == [["mount", {"zoom": 3}], ["update", {"zoom": 4}]]


def test_a_reconnect_leaves_it_mounted_and_it_hears_the_connection(board):
    page = board
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#board")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate("() => { window.__link.offline = false; }")
    wait_live(page, LIVE)
    by(page, "zoom-in").click()
    expect_text(by(page, "zoom"), "4")

    assert log(page, "board") == [
        ["mount", {"zoom": 3}],
        ["status", "disconnected"],
        ["status", "reconnected"],
        ["update", {"zoom": 4}],
    ]


def test_a_boosted_move_unmounts_what_the_next_page_lacks_and_keeps_the_sticky_one(board):
    page = board
    by(page, "dock-increment").click()
    expect_text(by(page, "dock-count"), "1")

    by(page, "leave").click()
    expect(page.locator("#board")).to_have_count(0)
    by(page, "dock-increment").click()
    expect_text(by(page, "dock-count"), "2")

    assert log(page, "board") == [["mount", {"zoom": 3}], ["unmount", None]]
    assert log(page, "dock") == [["mount", 0], ["update", 1], ["update", 2]]


def test_a_boosted_move_to_the_same_component_keeps_its_island_and_what_it_drew(board):
    """Not sticky: the next page's board, under the same id, joins again over the element the page kept."""
    page = board
    page.evaluate("window.__drawn = window.__islands.board.drawn")
    by(page, "zoom-in").click()
    expect_text(by(page, "zoom"), "4")

    by(page, "again").click()
    page.wait_for_url("**/islandprobe/again/")
    wait_live(page, LIVE)
    expect_text(page.locator("#board .drawn"), '{"zoom":3}')

    assert log(page, "board") == [["mount", {"zoom": 3}], ["update", {"zoom": 4}], ["update", {"zoom": 3}]]
    assert page.evaluate("window.__drawn === document.querySelector('#board .drawn')")
    assert page.evaluate("window.__drawn.parentElement === window.__islands.board.ctx.root")
    by(page, "zoom-in").click()
    expect_text(page.locator("#board .drawn"), '{"zoom":4}')


def test_an_island_a_move_hands_to_another_component_is_that_ones(page, server):
    """The same island id under components of different ids: idiomorph moves the element across. The old
    component's leaving unmounts its island; the new one mounts its own on the element, which its hooks
    took for mounted while the element still carried the old one's marks."""
    open_live(page, f"{server}/islandprobe/holder/a/", selector="#holder-a[data-is-live='true']")
    page.wait_for_function("() => window.__islands && window.__islands['holder-a']")

    by(page, "holder-b").click()
    wait_live(page, "#holder-b[data-is-live='true']")
    page.wait_for_function("() => window.__islands['holder-b']")

    events = [(what, who) for what, who, _ in page.evaluate("window.__islandLog")]
    assert events == [("mount", "holder-a"), ("unmount", "holder-a"), ("mount", "holder-b")]
    assert page.evaluate("() => window.__islands['holder-b'].ctx.push('whose')") == {"id": "holder-b"}
    # What the old island drew is not under the new one's
    expect(page.locator("#shared-island .drawn")).to_have_count(1)
    expect_text(page.locator("#shared-island .drawn"), '"holder-b"')


def test_an_island_a_move_hands_across_while_both_components_stay_is_the_new_ones(page, server):
    """Neither component leaves, so nothing destroys the old one's hooks: the new one's scan has to."""
    open_live(page, f"{server}/islandprobe/sides/a/", selector="#side-b[data-is-live='true']")
    page.wait_for_function("() => window.__islands && window.__islands['side-a']")

    by(page, "sides-b").click()
    page.wait_for_url("**/islandprobe/sides/b/")
    page.wait_for_function("() => window.__islands['side-b']")

    events = [(what, who) for what, who, _ in page.evaluate("window.__islandLog")]
    assert events == [("mount", "side-a"), ("unmount", "side-a"), ("mount", "side-b")]
    assert page.evaluate("() => window.__islands['side-b'].ctx.push('whose')") == {"id": "side-b"}
    expect(page.locator("#side-b #moving-island .drawn")).to_have_count(1)
    expect(page.locator("#side-a [wire-island]")).to_have_count(0)


def test_a_back_navigation_mounts_the_island_on_a_clean_element(board):
    """The page shows the HTML it left -- the old island's drawing in it -- and mounts a new island there."""
    page = board
    by(page, "leave").click()
    expect(page.locator("#board")).to_have_count(0)
    page.go_back()
    wait_live(page, LIVE)
    page.wait_for_function("() => window.__islandLog.filter(([what, who]) => who === 'board').length === 3")

    assert [what for what, _ in log(page, "board")] == ["mount", "unmount", "mount"]
    expect(page.locator("#board .drawn")).to_have_count(1)
    by(page, "zoom-in").click()
    expect_text(page.locator("#board .drawn"), '{"zoom":4}')


def test_a_definition_registered_late_mounts_what_waits_for_it(page, server):
    open_live(page, f"{server}/islandprobe/?late=1", selector=LIVE)
    by(page, "zoom-in").click()
    expect_text(by(page, "zoom"), "4")
    assert page.evaluate("window.__islandLog") is None

    page.add_script_tag(url=f"{server}/static/islandprobe/islands.js")
    page.wait_for_function("() => window.__islands && window.__islands.board")

    # Mounted with the props the element carries now
    assert log(page, "board") == [["mount", {"zoom": 4}]]
    assert log(page, "dock") == [["mount", 0]]
