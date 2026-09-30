"""What every browser suite waits for, written once.

Five suites had their own copy of "poll until the page says what I expect", and
all five carried the same two defects (#85).

**A wall-clock deadline reports the machine, not the code.** A hand-rolled
``while time.time() < deadline`` loop with a five-second budget fails a cold
first run -- the first interaction pays for the browser, the first template
compile and the first database connection at once -- and passes every time
afterwards. Playwright already retries an assertion for as long as it is given,
so the retrying belongs to the assertion; what is left to decide is the budget,
and it is the same one the harness gives the server to start.

**A failure has to say whether anything went wrong on the server.**
``receive_json`` has no catch, so a handler that raises tears the socket down
and the page simply stops updating. From a browser waiting for an element that
is indistinguishable from a slow machine, and the difference exists only in the
log. So every wait here reports what the server logged while it waited
(``e2e_server.server_errors``).

``tests/test_e2e_harness.py`` guards against a sixth copy.
"""

from __future__ import annotations

import typing as t

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from .e2e_server import server_errors

__all__ = [
    "WAIT_TIMEOUT",
    "LIVE_SELECTOR",
    "OFFLINE_SHIM",
    "INBOX_SHIM",
    "open_live",
    "wait_live",
    "expect_text",
    "expect_count",
]

#: The budget for anything the browser has to wait for, in seconds. The same one
#: ``e2e_server.STARTUP_TIMEOUT`` gives the server, and for the same reason.
WAIT_TIMEOUT = 15.0

#: What a component sets when it sends its join -- not when the join succeeds: a
#: component whose ``joined()`` raises matches too, and is marked ``wireview-error``.
LIVE_SELECTOR = '[data-is-live="true"]'

#: An init script that wraps the page's WebSocket so a test can drop the connection
#: and refuse reconnects: ``window.__link.offline = true`` sends every new socket to
#: a port nothing listens on, which fails the way a lost network does, and
#: ``window.__link.sockets`` lists every socket the page opened.
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

#: An init script that lets a test hold what the server sends until it says so, to
#: put the page's answers in an order the network could: ``window.__inbox.holding
#: = true`` keeps every message from the page's socket, ``window.__inbox.release(n)``
#: hands it the first ``n`` of them, and ``release()`` all of them and stops
#: holding. ``window.__inbox.seen`` and ``held`` are the messages (parsed) the page
#: has been handed and the ones it is still waiting for, in order.
INBOX_SHIM = """
(() => {
  const Native = window.WebSocket;
  const inbox = { holding: false, seen: [], held: [] };
  const hands = [];
  const deliver = (message, hand) => {
    inbox.seen.push(message);
    hand();
  };
  inbox.release = (count = Infinity) => {
    if (count === Infinity) inbox.holding = false;
    const messages = inbox.held.splice(0, count);
    hands.splice(0, messages.length).forEach((hand, i) => deliver(messages[i], hand));
  };
  window.__inbox = inbox;
  window.WebSocket = class extends Native {
    addEventListener(type, listener, options) {
      if (type !== "message") return super.addEventListener(type, listener, options);
      return super.addEventListener(type, (event) => {
        const message = JSON.parse(event.data);
        const hand = () => listener.call(this, event);
        if (inbox.holding || inbox.held.length) {
          inbox.held.push(message);
          hands.push(hand);
        } else {
          deliver(message, hand);
        }
      }, options);
    }
  };
})();
"""

# Every Playwright assertion gets the same budget, including the ones a suite
# writes directly (`to_be_visible`, `to_contain_text`). Otherwise the budget is
# whatever each call site remembered to pass, which is how five different
# numbers ended up in five suites.
expect.set_options(timeout=WAIT_TIMEOUT * 1000)


def _with_server_errors(check: t.Callable[[], None]) -> None:
    """Run an assertion, and if it fails say what the server said meanwhile.

    Both kinds of failure: ``expect`` raises AssertionError, but
    ``page.wait_for_selector`` times out with Playwright's own error, and that is
    the wait every suite starts with.
    """
    try:
        check()
    except (AssertionError, PlaywrightError) as failure:
        errors = server_errors()
        if not errors:
            raise
        reported = "\n".join(f"  {line}" for line in errors)
        raise AssertionError(f"{failure}\n\nThe server logged, while this was waiting:\n{reported}") from None


def wait_live(page: t.Any, selector: str = LIVE_SELECTOR) -> None:
    """Wait until a component on the page has joined.

    Args:
        page: the Playwright page.
        selector: narrow it when a page holds several components and the test
            means a particular one (``[data-name=XTodoList][data-is-live="true"]``).
    """
    _with_server_errors(lambda: page.wait_for_selector(selector, timeout=WAIT_TIMEOUT * 1000))


def open_live(page: t.Any, url: str, selector: str = LIVE_SELECTOR) -> None:
    """Go to ``url`` and wait until it is live."""
    page.goto(url)
    wait_live(page, selector)


def expect_text(locator: t.Any, text: str) -> None:
    """Assert a locator's text, retried by Playwright for the whole budget."""
    _with_server_errors(lambda: expect(locator).to_have_text(text))


def expect_count(locator: t.Any, count: int) -> None:
    """Assert how many elements a locator resolves to, retried for the whole budget."""
    _with_server_errors(lambda: expect(locator).to_have_count(count))
