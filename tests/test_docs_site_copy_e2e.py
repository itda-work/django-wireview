"""The documentation site's copy buttons, in a browser (#173).

The built site is served the way itda.work serves it -- ``script-src 'self'`` and no inline
style allowance -- so a button that needed an inline script or handler would do nothing here,
and the test asks the browser whether it refused anything. Pretendard is the one thing a page
loads from elsewhere; it is refused here, as the tests use no network.

What site.js decides without a browser (the trailing newline, which way to the clipboard) is
tests/js/docs-site-copy.test.mjs. That every ``<pre>`` of the build sits in a ``.code-block``
is tests/test_docs_site_build.py.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import WAIT_TIMEOUT, expect_count, expect_text

from scripts.docs_site import nav

pytestmark = pytest.mark.e2e

pytest.importorskip("markdown_it")

from scripts.docs_site.build import build  # noqa: E402
from scripts.docs_site.render import MD  # noqa: E402

CSP = "default-src 'self'; script-src 'self'; style-src 'self' https://cdn.jsdelivr.net; img-src 'self'"

COLLECT_VIOLATIONS = """
window.__violations = [];
document.addEventListener("securitypolicyviolation", (e) => {
  window.__violations.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample}`);
});
"""

# The page's own clipboard is kept where the test can still read it, then taken away or made to
# refuse: site.js must fall back to a selection and execCommand("copy").
KEEP_CLIPBOARD = "window.__clipboard = navigator.clipboard;"
NO_CLIPBOARD = KEEP_CLIPBOARD + "Object.defineProperty(Navigator.prototype, 'clipboard', { get: () => undefined });"
REFUSING_CLIPBOARD = KEEP_CLIPBOARD + (
    "navigator.clipboard.writeText = () => Promise.reject(new DOMException('denied', 'NotAllowedError'));"
)
NO_COPY_AT_ALL = REFUSING_CLIPBOARD + "document.execCommand = () => false;"


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "docs-site"
    build(out=out)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(out)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def docs(page, site):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=site)
    page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    page.add_init_script(COLLECT_VIOLATIONS)
    expect.set_options(timeout=WAIT_TIMEOUT * 1000)
    yield page
    assert page.evaluate("window.__violations") == []


def _code_blocks(source: str) -> list[str]:
    """A document's code blocks as written, the newline that ends each left off as site.js leaves it."""
    tokens = MD.parse((nav.ROOT / source).read_text(encoding="utf-8"))
    return [token.content.rstrip("\n") for token in tokens if token.type in ("fence", "code_block")]


def _clipboard(page, reader: str = "navigator.clipboard") -> str:
    return page.evaluate(f"{reader}.readText()")


def test_every_code_block_has_a_copy_button(docs, site):
    docs.goto(f"{site}/wireview/")
    blocks = docs.locator(".code-block")
    expect_count(docs.locator(".code-block > .copy-button"), blocks.count())
    assert blocks.count() == len(_code_blocks("README.md")) > 0
    button = docs.locator(".copy-button").first
    expect(button).to_be_visible()
    expect(button).to_have_attribute("aria-label", "코드 복사")
    expect(button).to_have_attribute("type", "button")


def test_a_click_copies_the_code_as_written_and_says_so(docs, site):
    """A highlighted block copies its text, not its markup, and the note goes away again."""
    docs.goto(f"{site}/wireview/")
    expected = _code_blocks("README.md")
    highlighted = next(i for i in range(len(expected)) if docs.locator(".code-block").nth(i).locator("span").count())
    button = docs.locator(".copy-button").nth(highlighted)
    button.click()
    expect(button).to_have_attribute("data-state", "copied")
    expect_text(button.locator(".copy-button__note"), "복사됨")
    expect_text(docs.get_by_role("status"), "복사했습니다")
    assert _clipboard(docs) == expected[highlighted]
    expect(button).not_to_have_attribute("data-state", "copied")
    expect_text(docs.get_by_role("status"), "")


def test_every_block_of_a_page_copies_its_own_code(docs, site):
    docs.goto(f"{site}/wireview/")
    expected = _code_blocks("README.md")
    for at, code in enumerate(expected):
        button = docs.locator(".copy-button").nth(at)
        button.click()
        expect(button).to_have_attribute("data-state", "copied")
        assert _clipboard(docs) == code, at


def test_the_keyboard_reaches_and_presses_the_button(docs, site):
    docs.goto(f"{site}/wireview/")
    button = docs.locator(".copy-button").first
    button.focus()
    expect(button).to_be_focused()
    assert docs.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"
    docs.keyboard.press("Enter")
    expect(button).to_have_attribute("data-state", "copied")
    assert _clipboard(docs) == _code_blocks("README.md")[0]


@pytest.mark.parametrize("shim", [NO_CLIPBOARD, REFUSING_CLIPBOARD], ids=["no-clipboard-api", "refused"])
def test_without_the_clipboard_api_the_fallback_copies(docs, site, shim):
    docs.add_init_script(shim)
    docs.goto(f"{site}/wireview/")
    button = docs.locator(".copy-button").first
    button.click()
    expect(button).to_have_attribute("data-state", "copied")
    assert _clipboard(docs, "window.__clipboard") == _code_blocks("README.md")[0]
    # The fallback's textarea is gone again, and the focus is where it was.
    expect_count(docs.locator("textarea.copy-scratch"), 0)
    expect(button).to_be_focused()


def test_a_copy_that_fails_says_so(docs, site):
    docs.add_init_script(NO_COPY_AT_ALL)
    docs.goto(f"{site}/wireview/")
    button = docs.locator(".copy-button").first
    button.click()
    expect(button).to_have_attribute("data-state", "failed")
    expect_text(button.locator(".copy-button__note"), "복사 실패")
    expect_text(docs.get_by_role("status"), "복사하지 못했습니다")
    expect(button).not_to_have_attribute("data-state", "failed")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_button_is_seen_in_either_theme(docs, site, theme):
    docs.add_init_script(f"localStorage.setItem('theme', '{theme}');")
    docs.goto(f"{site}/wireview/")
    expect(docs.locator("html")).to_have_class("dark" if theme == "dark" else "")
    button = docs.locator(".copy-button").first
    expect(button).to_be_visible()
    colors = button.evaluate(
        "b => [getComputedStyle(b).color,"
        " getComputedStyle(b.closest('.code-block').querySelector('pre')).backgroundColor]"
    )
    assert colors[0] != colors[1], colors


def test_the_button_stays_in_the_corner_of_a_block_scrolled_sideways(docs, site):
    docs.set_viewport_size({"width": 420, "height": 800})
    docs.goto(f"{site}/wireview/")
    wide = docs.evaluate(
        "[...document.querySelectorAll('.code-block')]"
        ".findIndex(b => b.querySelector('pre').scrollWidth > b.clientWidth + 100)"
    )
    assert wide >= 0, "no code block on the page is wider than a phone"
    block = docs.locator(".code-block").nth(wide)
    block.scroll_into_view_if_needed()
    before = block.locator(".copy-button").bounding_box()
    block.locator("pre").evaluate("pre => { pre.scrollLeft = pre.scrollWidth; }")
    expect(block.locator("pre")).not_to_have_js_property("scrollLeft", 0)
    after = block.locator(".copy-button").bounding_box()
    box = block.bounding_box()
    assert before and after and box
    assert after == before
    assert abs((box["x"] + box["width"]) - (after["x"] + after["width"])) < 12
    assert abs(after["y"] - box["y"]) < 12
