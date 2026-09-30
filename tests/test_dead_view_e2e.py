"""What a page promises a browser without JavaScript (GAP-034, #73, docs/features/dead-view.md).

The first render is complete HTML, links are links, and a form that names its
``action`` and ``method`` goes to that view as an ordinary POST. With
JavaScript the same form goes to its ``{% on "submit.prevent" %}`` handler
instead. Event handlers do nothing without JavaScript; that is not promised.

Fixture: tests/testproj/deadprobe/.
"""

import pytest
from playwright.sync_api import expect
from testproj.deadprobe.live import NOTES
from testproj.e2e_browser import expect_count, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    NOTES[:] = ["seeded"]
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def no_js(browser):
    context = browser.new_context(java_script_enabled=False)
    yield context.new_page()
    context.close()


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_without_javascript_the_first_render_is_readable(no_js, server):
    no_js.goto(f"{server}/deadprobe/")

    expect_count(by(no_js, "notes").locator("li"), 1)
    expect_text(by(no_js, "notes").locator("li"), "seeded")


def test_without_javascript_the_form_posts_to_its_view(no_js, server):
    no_js.goto(f"{server}/deadprobe/")

    by(no_js, "text").fill("by post")
    by(no_js, "add").click()

    expect_text(by(no_js, "notes").locator("li").nth(1), "by post")
    assert NOTES == ["seeded", "by post"]
    assert no_js.url.endswith("/deadprobe/"), "the view redirected back, so a reload does not post again"


def test_with_javascript_the_same_form_goes_to_the_handler(page, server):
    open_live(page, f"{server}/deadprobe/")
    page.evaluate("() => { window.__notReloaded = true; }")

    by(page, "text").fill("by handler")
    by(page, "add").click()

    expect_text(by(page, "notes").locator("li").nth(1), "by handler")
    assert page.evaluate("() => window.__notReloaded") is True, "the handler took it; no POST reloaded the page"


def test_an_event_handler_is_not_promised_without_javascript(no_js, server):
    no_js.goto(f"{server}/deadprobe/")

    by(no_js, "shout").click()

    expect(by(no_js, "shouted")).to_have_text("False")
