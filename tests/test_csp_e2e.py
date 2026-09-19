"""Every event binding works under a Content Security Policy with no 'unsafe-inline' (#90).

The probe page is served with ``script-src 'self' 'nonce-…'`` and the same for
``style-src``. An inline ``onclick`` would be refused by ``script-src-attr``,
which takes no nonce, and an un-nonced ``<style>`` by ``style-src``. So the test
drives each binding shape the tag can render and then asks the browser whether
it refused anything. The control at the end proves the policy is really
enforced: an injected inline handler must be refused.

Three older defects the move to delegated bindings fixed are pinned here too
(docs/design/csp-event-binding.md §4): debounced inputs sharing one timer, the
same event bound twice on one element, and the binding's element being the
innermost node clicked instead of the element that carries the binding.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

COLLECT_VIOLATIONS = """
window.__violations = [];
document.addEventListener("securitypolicyviolation", (e) => {
  window.__violations.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample}`);
  // Also on the page, for a Playwright assertion to wait on: wait_for_function
  // evaluates a string, which this very policy refuses.
  document.documentElement.dataset.violations = String(window.__violations.length);
});
"""

# A socket that never opens: the bundle loads, the page is never live.
BLOCK_SOCKET = """
window.WebSocket = class {
  constructor() { this.readyState = 0; }
  addEventListener() {}
  removeEventListener() {}
  send() {}
  close() {}
};
"""


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_every_binding_works_under_a_strict_policy(page, server):
    page.add_init_script(COLLECT_VIOLATIONS)
    open_live(page, f"{server}/cspprobe/")

    by(page, "increment").click()
    expect_text(by(page, "count"), "1")

    # Two debounced inputs typed within one debounce window: each keeps its own timer.
    # Both in one tick, so neither timer can fire (and its render land) before the
    # other input has its value; a render resets inputs the server does not render
    # (#91), which is not what this checks.
    page.evaluate(
        """() => {
          for (const [id, value] of [["first", "one"], ["second", "two"]]) {
            const input = document.querySelector(`[data-testid="${id}"]`);
            input.value = value;
            input.dispatchEvent(new Event("input", { bubbles: true }));
          }
        }"""
    )
    expect_text(by(page, "first-value"), "one")
    expect_text(by(page, "second-value"), "two")

    # keyup.enter and keyup.esc on one input are two bindings, not one.
    by(page, "keys").press("Enter")
    expect_text(by(page, "keys-value"), "E")
    by(page, "keys").press("Escape")
    expect_text(by(page, "keys-value"), "EX")

    # .stop keeps the click from the outer element's binding.
    by(page, "inner").click()
    expect_text(by(page, "inner-count"), "1")
    expect_text(by(page, "outer-count"), "0")

    # Clicking the label inside the button acts on the button that carries the binding.
    by(page, "slow-label").click()
    expect(by(page, "slow")).to_have_text("Saving")
    expect(by(page, "slow")).to_be_disabled()
    expect(by(page, "slow")).to_have_text("Save")

    # A JS() chain runs without the server.
    by(page, "toggle").click()
    expect(by(page, "panel")).to_be_hidden()
    by(page, "toggle").click()
    expect(by(page, "panel")).to_be_visible()

    # submit.prevent on a live page goes to the handler, not to the form's action.
    by(page, "q").fill("hello")
    by(page, "q").press("Enter")
    expect_text(by(page, "submitted"), "hello")
    assert page.url.rstrip("/").endswith("/cspprobe")

    # The upload button opens the file picker through its attribute.
    with page.expect_file_chooser() as chooser:
        by(page, "pick").click()
    assert chooser.value.is_multiple()

    # A file chosen in the upload input goes up in chunks and reaches the handler.
    by(page, "file-input").set_input_files(
        files=[{"name": "note.txt", "mimeType": "text/plain", "buffer": b"hello upload"}]
    )
    expect_text(by(page, "received"), "note.txt:12")

    assert page.evaluate("window.__violations") == []

    # Control: the policy is enforced, so an inline handler would have been refused.
    page.evaluate(
        """() => document.body.insertAdjacentHTML('beforeend', '<img src="/nope" onerror="window.__ran = 1">')"""
    )
    expect(page.locator("html")).to_have_attribute("data-violations", re.compile(r"^[1-9]"))
    assert page.evaluate("window.__ran") is None
    assert any("script-src" in v for v in page.evaluate("window.__violations"))


def test_a_form_submits_to_its_action_when_the_page_is_not_live(page, server):
    """No policy here: an inline handler would run, and its preventDefault would keep the form from going anywhere."""
    page.add_init_script(BLOCK_SOCKET)
    page.goto(f"{server}/cspprobe/?csp=0")

    by(page, "q").fill("offline")
    by(page, "q").press("Enter")

    expect_text(by(page, "submitted-page"), "offline")
    assert "/cspprobe/submitted/" in page.url
