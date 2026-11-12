"""A page that was live and lost its connection (#97).

Two things went wrong while the socket was down:

- A server binding did nothing at all, ``.prevent`` included -- the rule for a
  page that was never live (#90) -- so Enter in a ``submit.prevent`` form
  submitted it natively and reloaded the page, and a ``click.prevent`` link
  navigated away.
- Messages sent meanwhile were queued without a bound and sent after the
  reconnect, to instances that no longer existed. A binding or
  ``wireview.send`` sends nothing without a joined component, so the path is
  what calls the socket directly: a hook's ``pushEvent``, the upload
  messages.

The socket is cut from inside the page (``OFFLINE_SHIM``), which can close it
and refuse reconnects for as long as the test wants.

Fixture: tests/testproj/offlineprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, VISIBILITY_SHIM, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

LIVE = "#box[data-is-live='true']:not(.wireview-disconnected)"


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


class Link:
    """The page's WebSocket, cut and restored on demand."""

    def __init__(self, page) -> None:
        self.page = page
        page.add_init_script(OFFLINE_SHIM)

    def cut(self) -> None:
        self.page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
        expect(self.page.locator("#box")).to_have_class(re.compile("wireview-disconnected"))

    def restore(self) -> None:
        self.page.evaluate("() => { window.__link.offline = false; }")
        wait_live(self.page, LIVE)


@pytest.fixture
def link(page, server):
    link = Link(page)
    open_live(page, f"{server}/offlineprobe/", selector=LIVE)
    # A marker a reload or a navigation would lose
    page.evaluate("window.__samePage = true")
    return link


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_enter_in_a_prevented_form_does_not_reload_a_disconnected_page(link):
    page = link.page
    link.cut()

    # The header's CSS takes pointer events from a disconnected component, so
    # a mouse cannot reach these. A keyboard can: that is the path left open.
    by(page, "item").fill("typed offline")
    by(page, "item").press("Enter")
    by(page, "link").press("Enter")

    assert page.evaluate("window.__samePage === true")
    assert "/offlineprobe/elsewhere/" not in page.url
    expect(by(page, "item")).to_have_value("typed offline")


def test_what_was_sent_while_disconnected_does_not_arrive_after_the_reconnect(link):
    page = link.page
    link.cut()

    # A hook keeps its instance across the drop and sends on the socket directly
    page.evaluate("() => { for (let i = 0; i < 3; i++) window.__pinger.pushEvent('ping', {}); }")
    link.restore()
    page.evaluate("() => window.__pinger.pushEvent('ping', {})")

    # 1: the three sent while the socket was down went nowhere
    expect_text(by(page, "pings"), "1")


def test_a_push_the_socket_will_not_answer_is_rejected(link):
    """Sent while down, or sent and then cut before the answer: rejected, not left hanging (#222)."""
    page = link.page

    cut_before = page.evaluate(
        """() => {
          const pending = window.__pinger.pushEvent("ping", {});
          window.__link.offline = true;
          window.__link.sockets.forEach((s) => s.close());
          return pending.then(() => "resolved", (error) => error.reason);
        }"""
    )
    assert cut_before == "disconnected"

    expect(page.locator("#box")).to_have_class(re.compile("wireview-disconnected"))
    while_down = page.evaluate(
        "() => window.__pinger.pushEvent('ping', {}).then(() => 'resolved', (error) => error.reason)"
    )
    assert while_down == "disconnected"

    link.restore()
    assert page.evaluate("() => window.__pinger.pushEvent('ping', {}).then(() => 'resolved')") == "resolved"


def test_the_page_works_again_after_the_reconnect(link):
    page = link.page
    link.cut()
    link.restore()

    by(page, "item").fill("after")
    by(page, "item").press("Enter")

    expect_text(by(page, "added"), "after")
    assert page.evaluate("window.__samePage === true")


# --- what a reconnect gives back (#110) --------------------------------------------------


def test_a_hook_hears_the_disconnect_and_the_reconnect(link):
    page = link.page
    link.cut()
    expect(page.locator("html")).to_have_attribute("data-disconnected", "1")
    link.restore()
    expect(page.locator("html")).to_have_attribute("data-reconnected", "1")


def test_a_hook_keeps_its_callbacks_after_the_reconnect(link):
    page = link.page
    html = page.locator("html")
    link.cut()
    link.restore()

    # handleEvent: the server pushes to it. Pushed after the rejoin's render, since
    # the server answers one socket in order, so that render is on the page now.
    by(page, "nudge").click()
    expect(html).to_have_attribute("data-nudge1", "1")
    # updated: counted from here, because the rejoin's render ran it already
    before = int(html.get_attribute("data-updated") or 0)
    by(page, "bump").click()
    expect_text(by(page, "count"), "1")
    expect(html).to_have_attribute("data-updated", str(before + 1))
    # pushEvent's reply callback
    page.evaluate(
        """() => window.__watcher.pushEvent("echo", {n: 7}, (reply) => {
             document.documentElement.dataset.echo = String(reply.echo);
           })"""
    )
    expect(html).to_have_attribute("data-echo", "7")


def test_auto_recover_sends_the_forms_values_to_its_handler(link):
    page = link.page
    link.cut()
    by(page, "draft").fill("half a sentence")
    link.restore()

    expect_text(by(page, "recovered"), "half a sentence")


def test_auto_recover_without_a_handler_replays_the_forms_change_binding(link):
    # What Phoenix does for phx-auto-recover's default: the form's own change event.
    page = link.page
    link.cut()
    by(page, "city").fill("Seoul")
    link.restore()

    expect_text(by(page, "city-value"), "Seoul")


# --- the reconnect backoff is the operator's (#124) ------------------------------------


@pytest.fixture
def quick_reconnect(settings):
    """Every 100 ms, no jitter. Listed before ``link`` so the page is served with it."""
    settings.WIREVIEW = {
        **getattr(settings, "WIREVIEW", {}),
        "RECONNECT_MIN_DELAY_MS": 100,
        "RECONNECT_JITTER_MS": 0,
        "RECONNECT_MAX_DELAY_MS": 100,
        "RECONNECT_GROW_FACTOR": 1,
    }


def test_the_page_retries_at_the_pace_the_settings_give(quick_reconnect, link):
    page = link.page
    before = page.evaluate("window.__link.sockets.length")
    link.cut()

    # The library's default waits 1 to 5 seconds before the first retry, so
    # five attempts inside two seconds can only come from the settings.
    page.wait_for_function(f"window.__link.sockets.length >= {before + 5}", timeout=2000)
    link.restore()


# --- the user coming back does not wait out the backoff (#228) --------------------------

#: The page's probe deadline (reconnect.mjs PROBE_TIMEOUT_MS) and how long a socket
#: has to be quiet before a return asks it (HEARD_RECENTLY_MS), in milliseconds.
PROBE_TIMEOUT_MS = 4000
HEARD_RECENTLY_MS = 1000


@pytest.fixture
def slow_reconnect(settings):
    """A minute before each retry: far past every wait here, so a page back inside
    one can only have skipped it. Listed before ``link`` so the page is served with it."""
    settings.WIREVIEW = {
        **getattr(settings, "WIREVIEW", {}),
        "RECONNECT_MIN_DELAY_MS": 60000,
        "RECONNECT_JITTER_MS": 0,
        "RECONNECT_MAX_DELAY_MS": 60000,
        "RECONNECT_GROW_FACTOR": 1,
    }


@pytest.fixture
def visibility(page):
    """Lets the test hide and show the tab. Listed before ``link``."""
    page.add_init_script(VISIBILITY_SHIM)


def sockets(page) -> int:
    return page.evaluate("window.__link.sockets.length")


def wait_quiet(page) -> None:
    """Until the socket has heard nothing for longer than a return lets pass unasked:
    the time measured is the page's own rule, not a guess at the machine's speed."""
    page.evaluate("window.__quietFrom = performance.now()")
    page.wait_for_function(f"performance.now() - window.__quietFrom > {HEARD_RECENTLY_MS + 100}")


def test_a_tab_shown_again_connects_without_waiting_out_the_backoff(slow_reconnect, visibility, link):
    page = link.page
    page.evaluate("window.__visibility('hidden')")
    link.cut()
    before = sockets(page)
    # The network is back while nobody looks: the page waits on, as it always did
    page.evaluate("window.__link.offline = false")

    page.evaluate("window.__visibility('visible')")

    # Inside the harness's budget, a quarter of the minute the backoff would take
    wait_live(page, LIVE)
    assert sockets(page) == before + 1
    by(page, "item").fill("back")
    by(page, "item").press("Enter")
    expect_text(by(page, "added"), "back")


def test_the_network_coming_back_connects_without_waiting_out_the_backoff(slow_reconnect, link):
    page = link.page
    link.cut()
    page.evaluate("window.__link.offline = false")

    page.context.set_offline(True)
    page.context.set_offline(False)  # the browser fires `online`

    wait_live(page, LIVE)


def test_shown_and_online_at_once_open_one_socket(slow_reconnect, visibility, link):
    page = link.page
    page.evaluate("window.__visibility('hidden')")
    link.cut()
    before = sockets(page)
    page.evaluate("window.__link.offline = false")

    page.evaluate("() => { window.__visibility('visible'); window.dispatchEvent(new Event('online')); }")

    wait_live(page, LIVE)
    by(page, "item").fill("once")
    by(page, "item").press("Enter")
    expect_text(by(page, "added"), "once")
    # The second signal neither opened another nor closed the one just opened
    assert sockets(page) == before + 1


def test_a_socket_that_looks_open_but_is_dead_is_replaced_when_the_tab_comes_back(visibility, link):
    """A phone tab frozen in the background: its socket is still OPEN to the page,
    but the connection under it is gone, and the first click got no answer."""
    page = link.page
    page.evaluate("window.__visibility('hidden')")
    page.evaluate("window.__link.deafen()")
    before = sockets(page)
    wait_quiet(page)

    page.evaluate("window.__visibility('visible')")

    # Asked with sync; no answer within the deadline, so another socket opens
    page.wait_for_function(f"window.__link.sockets.length > {before}", timeout=PROBE_TIMEOUT_MS + 10000)
    assert "sync" in page.evaluate("window.__link.sent")
    wait_live(page, LIVE)
    by(page, "bump").click()
    expect_text(by(page, "count"), "1")
    assert sockets(page) == before + 1


def test_a_socket_that_still_answers_is_kept_when_the_tab_comes_back(visibility, link):
    page = link.page
    before = sockets(page)
    page.evaluate("window.__visibility('hidden')")
    wait_quiet(page)

    page.evaluate("window.__visibility('visible')")
    page.evaluate("window.__shownAt = performance.now()")
    assert page.evaluate("window.__link.sent").count("sync") == 1
    # Past the probe's deadline: the answer came, so nothing was replaced
    page.wait_for_function(f"performance.now() - window.__shownAt > {PROBE_TIMEOUT_MS + 500}")

    assert sockets(page) == before
    wait_live(page, LIVE)
    by(page, "bump").click()
    expect_text(by(page, "count"), "1")


def test_a_tab_shown_again_right_after_its_socket_opened_is_not_asked(visibility, link):
    """A socket heard from a moment ago is alive; asking would only race its traffic."""
    page = link.page
    by(page, "bump").click()
    expect_text(by(page, "count"), "1")

    page.evaluate("() => { window.__visibility('hidden'); window.__visibility('visible'); }")

    assert "sync" not in page.evaluate("window.__link.sent")


def test_a_return_while_a_slow_handler_runs_keeps_the_socket_and_its_render(visibility, link):
    """The user starts something that takes the server seconds -- a report, an outside
    API -- and looks at another tab meanwhile. The socket has sent and not yet heard
    back: its silence is the server working, and asking it then took it for dead,
    replaced it, and the handler's render went to an instance no page holds."""
    page = link.page
    before = sockets(page)
    by(page, "slow-bump").click()
    page.evaluate("window.__visibility('hidden')")
    wait_quiet(page)

    page.evaluate("window.__visibility('visible')")

    # Past where an unanswered probe would have replaced the socket: the render lands
    expect_text(by(page, "count"), "100")
    assert "sync" not in page.evaluate("window.__link.sent")
    assert sockets(page) == before


def test_a_page_restored_from_the_back_forward_cache_connects_without_waiting(slow_reconnect, link):
    """WebKit closes the socket of a page it keeps in the back/forward cache and may
    bring the page back. Chromium keeps a page with an open socket out of that cache,
    so the restore is the event alone, as tests/test_history_e2e.py fires it."""
    page = link.page
    link.cut()
    page.evaluate("window.__link.offline = false")

    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))")

    wait_live(page, LIVE)
