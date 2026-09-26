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
from testproj.e2e_browser import expect_text, open_live, wait_live
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


#: Wraps the page's WebSocket so a test can drop the connection and refuse
#: reconnects: while offline, a new socket goes to a port nothing listens on,
#: which fails the way a lost network does.
OFFLINE_SHIM = """
(() => {
  const Native = window.WebSocket;
  window.__link = { offline: false, sockets: [] };
  window.WebSocket = class extends Native {
    constructor(url, protocols) {
      super(window.__link.offline ? "ws://127.0.0.1:9/" : url, protocols);
      window.__link.sockets.push(this);
    }
  };
})();
"""


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


def test_the_page_works_again_after_the_reconnect(link):
    page = link.page
    link.cut()
    link.restore()

    by(page, "item").fill("after")
    by(page, "item").press("Enter")

    expect_text(by(page, "added"), "after")
    assert page.evaluate("window.__samePage === true")
