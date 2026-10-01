"""Streams in a real browser: dom_id, limit, and the viewport bindings (#110).

``dom_id`` was only checked through its default, ``limit`` only for surviving the
hop to the client, and ``wire-viewport-top``/``wire-viewport-bottom`` had neither a
test nor a page of documentation. Fixture: tests/testproj/streamprobe/.
"""

import json

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, OFFLINE_SHIM, expect_count, expect_text, open_live, wait_live
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


def _settled(page) -> None:
    """Let the joined list be judged: `joined` has started the observer by the time
    the rows are there, and its first callback comes within a few frames. Then an
    event's round trip -- the server answers in order, so a load_more sent before
    it is answered first."""
    page.evaluate(
        """() => new Promise((done) => {
          let n = 0;
          const frame = () => (++n < 5 ? requestAnimationFrame(frame) : done());
          requestAnimationFrame(frame);
        })"""
    )
    by(page, "tick").click()
    expect_count(by(page, "ticks").locator("li"), 1)


def test_a_list_that_fills_the_viewport_loads_nothing_more(page, server):
    """The bottom binding sits under the rows once they arrive. Watched from the
    join, it saw an empty list and asked for a second page -- a race that a slow
    first page (``delay``) always loses (#112)."""
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?delay=0.5")
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)
    expect_text(by(page, "pages"), "1")
    expect_count(by(page, "rows").locator("li"), 15)


def test_a_short_list_loads_one_more_page(page, server):
    """Short means the bottom binding is in the upper half of the viewport once the
    rows are there (``detectOverran``): one 80px row is, three are not."""
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?size=1")
    expect_text(by(page, "pages"), "2")
    expect_count(by(page, "rows").locator("li"), 2)
    _settled(page)
    expect_text(by(page, "pages"), "2")


def test_a_list_the_page_left_and_came_back_to_loads_one_more_page(page, server):
    """Back on the page, the element and its component are new, and their join is
    one the page names with a ref: infinite scroll starts on that join's
    ``joined``, as it did on the first (#146)."""
    sockets, sent = [], []

    def opened(ws):
        sockets.append(ws)
        ws.on("framesent", lambda frame: sent.append(json.loads(frame)))

    page.on("websocket", opened)
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?size=1")
    expect_text(by(page, "pages"), "2")
    by(page, "away").click()
    expect(by(page, "rows")).to_have_count(0)
    by(page, "back").click()
    expect_text(by(page, "pages"), "2")
    expect_count(by(page, "rows").locator("li"), 2)
    assert len(sockets) == 1, "boosted, on the socket the page opened"
    assert "ref" in [m for m in sent if m["command"] == "join"][-1]["payload"], "the join back is named"


def test_scrolling_to_the_bottom_after_a_reconnect_loads_one_page(page, server):
    """The reconnect's join makes a new component with an observer of its own, which
    its ``joined`` starts. The old component's observer was never disconnected: it
    kept watching the same sentinel and sent ``load_more`` to whatever instance held
    the id -- the new one, which was asked for two pages at once."""
    page.add_init_script(OFFLINE_SHIM)
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/")
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)

    page.evaluate("window.__link.offline = true; window.__link.sockets.forEach((s) => s.close())")
    expect(page.locator("#probe.wireview-disconnected")).to_have_count(1)
    page.evaluate("window.__link.offline = false")
    wait_live(page, "#probe[data-is-live='true']:not(.wireview-disconnected)")
    page.evaluate(
        """() => new Promise((done) => {
          let n = 0;
          const frame = () => (++n < 5 ? requestAnimationFrame(frame) : done());
          requestAnimationFrame(frame);
        })"""
    )
    by(page, "tick").click()
    expect_count(by(page, "ticks").locator("li"), 2)

    by(page, "sentinel").scroll_into_view_if_needed()
    expect_text(by(page, "pages"), "2")
    # Answered after any load_more the scroll sent
    by(page, "tick").click()
    expect_count(by(page, "ticks").locator("li"), 3)
    expect_text(by(page, "pages"), "2")
    expect_count(by(page, "rows").locator("li"), 30)


def test_scrolling_back_to_the_top_calls_the_top_binding(probe):
    expect_count(by(probe, "rows").locator("li"), 15)
    expect_text(by(probe, "newer"), "0")  # in view at load, but nobody scrolled up to it
    probe.mouse.wheel(0, 3000)
    expect_text(by(probe, "pages"), "2")
    probe.mouse.wheel(0, -100000)
    expect_text(by(probe, "newer"), "1")


def test_two_components_with_a_stream_of_one_name_keep_their_own_lists(page, server):
    # The page looked for the first `wire-stream="ticks"` anywhere: the second
    # probe's ticks went into the first probe's list.
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?pair=1")
    second = by(page, "second")
    second.get_by_test_id("tick").click()
    second.get_by_test_id("tick").click()

    expect(second.get_by_test_id("ticks").locator("li")).to_have_text(["tick 2", "tick 1"])
    expect_count(page.locator("#probe [data-testid=ticks] li"), 0)

    page.locator("#probe [data-testid=tick]").click()
    expect(page.locator("#probe [data-testid=ticks] li")).to_have_text(["tick 1"])
    expect(second.get_by_test_id("ticks").locator("li")).to_have_text(["tick 2", "tick 1"])


def test_a_nested_component_with_a_stream_of_one_name_keeps_its_own_list(page, server):
    # The LiveComponent's `ticks` list comes first in the page, ahead of the probe's.
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?nest=1")
    by(page, "tick").click()
    by(page, "child-tick").click()
    by(page, "child-tick").click()

    expect(by(page, "ticks").locator("li")).to_have_text(["tick 1"])
    expect(by(page, "child-ticks").locator("li")).to_have_text(["tick 2", "tick 1"])


def test_a_delete_leaves_another_components_item_of_the_same_id(page, server):
    # Both probes hold a `tick-1`. The delete looked the id up in the whole page,
    # so the second probe's delete took the first probe's item.
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?pair=1")
    second = by(page, "second")
    page.locator("#probe [data-testid=tick]").click()
    second.get_by_test_id("tick").click()
    expect(page.locator("#probe [data-testid=ticks] li")).to_have_text(["tick 1"])
    expect(second.get_by_test_id("ticks").locator("li")).to_have_text(["tick 1"])

    second.get_by_test_id("tick-item").click()

    expect_count(second.get_by_test_id("ticks").locator("li"), 0)
    expect(page.locator("#probe [data-testid=ticks] li")).to_have_text(["tick 1"])


def test_an_item_a_stream_op_removes_does_not_send_its_blur(probe):
    # A focused element the page removes is blurred while it is still in the
    # document. The stream op is the server's, so that blur is nobody's.
    by(probe, "tick").click()
    item = by(probe, "tick-item")
    item.focus()
    by(probe, "tick").click()  # the user leaves the item: that blur is theirs
    expect_text(by(probe, "blurs"), "1")
    expect(by(probe, "tick-item")).to_have_text(["tick 2", "tick 1"])

    by(probe, "tick-item").first.click()  # focused, then deleted by the answer
    expect(by(probe, "tick-item")).to_have_text(["tick 1"])
    # Events are handled in order, so once this answer is in, a blur sent
    # during the delete would have been handled too.
    by(probe, "tick").click()
    expect(by(probe, "tick-item")).to_have_text(["tick 3", "tick 1"])

    expect_text(by(probe, "blurs"), "1")


def test_a_new_live_component_shows_what_its_joined_streams(probe):
    # The child's joined() streams go out right after the render that brings its
    # element, and the page patches that render in on the next frame: the ops
    # looked for an element that was not there yet and were dropped.
    expect_count(by(probe, "rows").locator("li"), 15)
    by(probe, "seed").click()
    expect(by(probe, "seeds").locator("li")).to_have_text(["tick 1", "tick 2"])
    expect(by(probe, "seed-ticks").locator("li")).to_have_text(["tick 3"])
    expect_count(by(probe, "ticks").locator("li"), 0)
    # The morph that brought the child in, ahead of the probe's lists, paired
    # them by position and removed the `rows` list it passed over: its 15 rows
    # went, the bottom binding rose into view and two more pages were asked for.
    _settled(probe)
    expect(by(probe, "rows").locator("li").first).to_have_id("row-0")
    expect_count(by(probe, "rows").locator("li"), 15)
    expect_text(by(probe, "pages"), "1")

    # Gone and back: a new instance, whose joined() streams into the new element
    by(probe, "seed").click()
    expect_count(by(probe, "seeds"), 0)
    by(probe, "seed").click()
    expect(by(probe, "seeds").locator("li")).to_have_text(["tick 1", "tick 2"])
    expect(by(probe, "seed-ticks").locator("li")).to_have_text(["tick 3"])


def test_a_stream_container_a_render_reveals_ahead_of_others_leaves_them_alone(probe):
    # The new, empty `late` list took the place of the live `ticks` list, which
    # a render does not morph: `late` never appeared, `ticks` showed twice and
    # the `rows` list further down was removed with its items.
    expect_count(by(probe, "rows").locator("li"), 15)
    by(probe, "tick").click()
    expect(by(probe, "ticks").locator("li")).to_have_text(["tick 1"])

    by(probe, "reveal").click()

    expect_count(by(probe, "late"), 1)
    expect_count(by(probe, "ticks"), 1)
    expect(by(probe, "ticks").locator("li")).to_have_text(["tick 1"])
    _settled(probe)
    expect_count(by(probe, "rows").locator("li"), 15)
    expect_text(by(probe, "pages"), "1")
    expect(by(probe, "ticks").locator("li")).to_have_text(["tick 2", "tick 1"])


def test_js_a_handler_aims_at_what_its_render_reveals_finds_it(probe):
    # The probe's element is on the page, so the command ran at once, ahead of the
    # frame that patches `late` in, and found nothing to add the class to.
    expect_count(by(probe, "rows").locator("li"), 15)

    by(probe, "reveal").click()

    expect(by(probe, "late")).to_have_class("revealed")


def test_a_new_live_component_runs_the_js_its_joined_pushes(probe):
    # Like its streams, the command arrives before the element it runs on
    probe.evaluate(
        """() => window.addEventListener("seed-joined", () => {
          const html = document.documentElement;
          html.dataset.seeded = String(Number(html.dataset.seeded || "0") + 1);
        })"""
    )
    by(probe, "seed").click()
    expect(by(probe, "seeds").locator("li")).to_have_text(["tick 1", "tick 2"])
    expect(probe.locator("html")).to_have_attribute("data-seeded", "1")


def test_a_new_live_component_judges_its_list_once_its_joined_page_is_in(page, server):
    # Its first page fills the window and pushes its bottom binding out of view.
    # The render that draws it lands a frame before that page does, as the network
    # can deliver it: an observer started with the render saw the binding in view
    # under an empty list and asked for more (#112).
    page.add_init_script(INBOX_SHIM)
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?fill=1")
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)

    page.evaluate("window.__inbox.holding = true")
    by(page, "seed").click()
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'seed-child')")
    page.evaluate(
        """() => new Promise((done) => {
          window.__inbox.release(1);
          requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(done, 100)));
        })"""
    )
    page.evaluate("window.__inbox.release()")

    expect_count(by(page, "seeds").locator("li"), 15)
    _settled(page)
    expect_text(by(page, "mores"), "0")


def test_a_join_that_replaces_the_instance_judges_the_new_list_once_it_is_in(page, server):
    # A boosted move to the probe's page joins `probe` again under its id. The
    # observer the first join started kept running: its scan of the new DOM saw
    # the bottom binding over a list not there yet and asked the new instance
    # for a second page before that instance's own first page came (#112).
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?fill=1")
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)

    by(page, "reseed").click()

    expect_count(by(page, "seeds").locator("li"), 15)
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)
    expect_text(by(page, "pages"), "1")
    expect_count(by(page, "rows").locator("li"), 15)


def test_a_live_components_joined_from_an_instance_a_join_replaced_starts_nothing_early(page, server):
    # The joined for a LiveComponent an event drew, from the instance a boosted
    # move's join replaces, arrives held behind that move: the child's element
    # is the new page's, whose own first page has not come. It is the replaced
    # join's and starts nothing; the new join's joined starts the child.
    page.add_init_script(INBOX_SHIM)
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?fill=1")
    expect_count(by(page, "rows").locator("li"), 15)
    _settled(page)

    page.evaluate("window.__inbox.holding = true")
    by(page, "seed").click()
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'seed-child')")
    by(page, "reseed").click()
    page.wait_for_function("document.location.search.includes('seeded')")
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'joined' && m.payload.id === 'probe')")
    page.evaluate("window.__inbox.release()")

    expect_count(by(page, "seeds").locator("li"), 15)
    _settled(page)
    expect_text(by(page, "mores"), "0")
    expect_text(by(page, "pages"), "1")


def test_a_new_live_component_watches_its_own_viewport_bindings(probe):
    # A LiveComponent the probe brings in after its join: nothing started its
    # observer, and the probe's own scan sent its `more` to the probe.
    by(probe, "seed").click()
    expect(by(probe, "seeds").locator("li")).to_have_text(["tick 1", "tick 2"])
    expect_text(by(probe, "mores"), "1")
    _settled(probe)
    expect_text(by(probe, "stolen"), "0")
    expect_text(by(probe, "mores"), "1")


def test_a_binding_the_parents_patch_draws_in_a_live_component_is_watched(page, server):
    # The probe and the child change in one render, which the probe's patch
    # draws: the child's new binding is the child's to watch
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?seeded=1")
    expect_text(by(page, "mores"), "1")
    by(page, "strip").click()
    expect_count(by(page, "seed-sentinel"), 0)
    by(page, "strip").click()
    expect_count(by(page, "seed-sentinel"), 1)
    expect_text(by(page, "mores"), "2")
    expect_text(by(page, "stolen"), "0")


def test_a_live_component_on_the_page_at_join_watches_its_own_viewport_bindings(page, server):
    # Both the probe and the child scanned the child's binding: it went to both
    page.set_viewport_size({"width": 800, "height": 600})
    open_live(page, f"{server}/streamprobe/?seeded=1")
    expect(by(page, "seeds").locator("li")).to_have_text(["tick 1", "tick 2"])
    expect_text(by(page, "mores"), "1")
    _settled(page)
    expect_text(by(page, "stolen"), "0")
    expect_text(by(page, "mores"), "1")
