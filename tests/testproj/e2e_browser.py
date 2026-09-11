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

from playwright.sync_api import expect

from .e2e_server import server_errors

__all__ = ["WAIT_TIMEOUT", "LIVE_SELECTOR", "open_live", "wait_live", "expect_text", "expect_count"]

#: The budget for anything the browser has to wait for, in seconds. The same one
#: ``e2e_server.STARTUP_TIMEOUT`` gives the server, and for the same reason.
WAIT_TIMEOUT = 15.0

#: What a component sets once it has joined.
LIVE_SELECTOR = '[data-is-live="true"]'

# Every Playwright assertion gets the same budget, including the ones a suite
# writes directly (`to_be_visible`, `to_contain_text`). Otherwise the budget is
# whatever each call site remembered to pass, which is how five different
# numbers ended up in five suites.
expect.set_options(timeout=WAIT_TIMEOUT * 1000)


def _with_server_errors(check: t.Callable[[], None]) -> None:
    """Run an assertion, and if it fails say what the server said meanwhile."""
    try:
        check()
    except AssertionError as failure:
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
