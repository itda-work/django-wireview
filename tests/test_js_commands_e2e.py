"""Every JS() command, loading class and page-level command, run in a real browser (#110).

``tests/test_js.py`` checks what the builder serializes; nothing checked that the
client does anything with it. The optimistic-UI half -- loading classes and
``wire-disabled-with`` -- had no test at all.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

SLOW = re.compile(r"\bwireview-loading\b")


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def probe(page, server):
    open_live(page, f"{server}/jsprobe/")
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_show_and_hide(probe):
    by(probe, "hide").click()
    expect(by(probe, "box")).to_be_hidden()
    by(probe, "show").click()
    expect(by(probe, "box")).to_be_visible()


def test_add_and_remove_class(probe):
    by(probe, "add-class").click()
    expect(by(probe, "box")).to_have_class("hot big")
    by(probe, "remove-class").click()
    expect(by(probe, "box")).not_to_have_class(re.compile(r"\b(hot|big)\b"))


def test_toggle_class(probe):
    by(probe, "toggle-class").click()
    expect(by(probe, "box")).to_have_class("hot")
    by(probe, "toggle-class").click()
    expect(by(probe, "box")).not_to_have_class(re.compile(r"\bhot\b"))


def test_set_and_remove_attribute(probe):
    by(probe, "set-attr").click()
    expect(by(probe, "box")).to_have_attribute("data-mark", "on")
    by(probe, "remove-attr").click()
    expect(by(probe, "box")).not_to_have_attribute("data-mark", re.compile(".*"))


def test_transition_adds_its_classes_for_the_duration(probe):
    by(probe, "transition").click()
    expect(by(probe, "box")).to_have_class(re.compile(r"\bpulse\b"))
    expect(by(probe, "box")).not_to_have_class(re.compile(r"\bpulse\b"))


def test_focus(probe):
    by(probe, "focus").click()
    expect(by(probe, "name")).to_be_focused()


def test_focus_first_skips_what_cannot_take_focus(probe):
    by(probe, "focus-first").click()
    expect(by(probe, "first-field")).to_be_focused()


def test_dispatch_fires_a_dom_event_with_its_detail(probe):
    probe.evaluate(
        """document.addEventListener("probe:ping", (e) => {
             document.documentElement.dataset.pinged = JSON.stringify(e.detail);
           })"""
    )
    by(probe, "dispatch").click()
    expect(probe.locator("html")).to_have_attribute("data-pinged", '{"n":1}')


def test_navigate_pushes_a_history_entry(probe):
    before = probe.evaluate("history.length")
    by(probe, "navigate").click()
    expect(by(probe, "landed")).to_be_visible()
    assert probe.url.endswith("/jsprobe/landed/")
    assert probe.evaluate("history.length") == before + 1


def test_navigate_with_replace_goes_there_in_place_of_this_entry(probe):
    before = probe.evaluate("history.length")
    by(probe, "navigate-replace").click()
    expect(by(probe, "landed")).to_be_visible()
    assert probe.url.endswith("/jsprobe/landed/")
    assert probe.evaluate("history.length") == before


def test_a_chain_runs_every_command_in_order(probe):
    by(probe, "chain").click()
    expect(by(probe, "box")).to_have_class(re.compile(r"\bchained\b"))
    expect(by(probe, "box")).to_have_attribute("data-step", "2")
    expect_text(by(probe, "count"), "1")


def test_a_click_marks_its_element_loading_until_the_answer(probe):
    button = by(probe, "slow")
    button.click()
    expect(button).to_have_class(re.compile(r"\bwireview-click-loading\b"))
    expect(button).to_have_class(SLOW)
    expect_text(by(probe, "saved"), "1")
    expect(button).not_to_have_class(re.compile(r"wireview-"))


def test_a_submit_marks_the_form_loading_until_the_answer(probe):
    by(probe, "q").fill("hello")
    by(probe, "q").press("Enter")
    form = by(probe, "form")
    expect(form).to_have_class(re.compile(r"\bwireview-submit-loading\b"))
    expect_text(by(probe, "submitted"), "hello")
    expect(form).not_to_have_class(re.compile(r"wireview-"))


def test_a_change_marks_its_element_loading_until_the_answer(probe):
    pick = by(probe, "pick")
    pick.select_option("b")
    expect(pick).to_have_class(re.compile(r"\bwireview-change-loading\b"))
    expect_text(by(probe, "changed"), "b")
    expect(pick).not_to_have_class(re.compile(r"wireview-"))


def test_a_binding_on_the_component_root_is_cleared_too(probe):
    card = by(probe, "card")
    card.click()
    expect(card).to_have_class(re.compile(r"\bwireview-click-loading\b"))
    expect_text(by(probe, "clicks"), "1")
    expect(card).not_to_have_class(re.compile(r"wireview-"))


def test_the_submit_button_of_a_form_is_disabled_with_its_text(probe):
    # docs/features/optimistic-ui.md puts wire-disabled-with on the submit button, not the form.
    button = by(probe, "submit")
    by(probe, "q").fill("hello")
    button.click()
    expect(button).to_be_disabled()
    expect(button).to_have_text("Sending")
    expect_text(by(probe, "submitted"), "hello")
    expect(button).to_be_enabled()
    expect(button).to_have_text("send")


def test_a_chain_that_pushes_is_disabled_with_its_text(probe):
    # The doc's JS() example: a client-side toggle, then a push to the server.
    button = by(probe, "slow-chain")
    button.click()
    expect(by(probe, "box")).to_have_class(re.compile(r"\bopened\b"))
    expect(button).to_be_disabled()
    expect(button).to_have_text("Opening")
    expect_text(by(probe, "saved"), "1")
    expect(button).to_be_enabled()
    expect(button).to_have_text("open")


def test_push_title_sets_the_documents_title(probe):
    by(probe, "announce").click()
    expect(probe).to_have_title("Announced")


def test_put_flash_shows_a_dismissible_message(probe):
    by(probe, "announce").click()
    flash = by(probe, "flashes").get_by_role("alert")
    expect(flash).to_have_text(re.compile("Saved"))
    expect(flash).to_have_attribute("data-flash-type", "info")
    flash.get_by_role("button", name="Dismiss").click()
    expect(flash).to_have_count(0)


KEEP_DATA_JS = """wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
  for (const attr of fromEl.attributes) {
    if (attr.name.startsWith("data-js-")) toEl.setAttribute(attr.name, attr.value);
  }
})"""


def test_a_render_drops_what_javascript_added_to_an_element(probe):
    # The control for the next test: the server's HTML wins the morph.
    by(probe, "hide").evaluate("el => el.setAttribute('data-js-seen', 'yes')")
    by(probe, "increment-server").click()
    expect_text(by(probe, "count"), "1")
    expect(by(probe, "hide")).not_to_have_attribute("data-js-seen", "yes")


def test_on_before_el_updated_keeps_it(probe):
    probe.evaluate(KEEP_DATA_JS)
    by(probe, "hide").evaluate("el => el.setAttribute('data-js-seen', 'yes')")
    by(probe, "increment-server").click()
    expect_text(by(probe, "count"), "1")
    expect(by(probe, "hide")).to_have_attribute("data-js-seen", "yes")


# --- server-sent navigation ----------------------------------------------------------------


def test_redirect_to_navigates(probe):
    by(probe, "redirect").click()
    expect(by(probe, "landed")).to_be_visible()
    assert probe.url.endswith("/jsprobe/landed/")


def test_push_to_adds_an_entry_and_runs_params_changed(probe):
    before = probe.evaluate("history.length")
    by(probe, "push").click()
    expect_text(by(probe, "page"), "2")
    assert probe.url.endswith("/jsprobe/?page=2")
    assert probe.evaluate("history.length") == before + 1


def test_replace_to_changes_the_url_in_place_and_runs_params_changed(probe):
    before = probe.evaluate("history.length")
    by(probe, "replace-url").click()
    expect_text(by(probe, "page"), "3")
    assert probe.url.endswith("/jsprobe/?page=3")
    assert probe.evaluate("history.length") == before


# --- wireview.debug -----------------------------------------------------------------------


def test_debug_enable_logs_what_the_socket_carries_until_disabled(probe):
    logged: list[str] = []
    probe.on("console", lambda message: logged.append(message.text))
    probe.evaluate("wireview.debug.enable()")
    by(probe, "increment-server").click()
    expect_text(by(probe, "count"), "1")
    assert any("recv:" in line for line in logged), logged

    probe.evaluate("wireview.debug.disable()")
    logged.clear()
    by(probe, "increment-server").click()
    expect_text(by(probe, "count"), "2")
    assert not any("recv:" in line for line in logged), logged


def test_debug_latency_holds_back_what_the_page_sends(probe):
    import time

    probe.evaluate("wireview.debug.latency(800)")
    started = time.monotonic()
    by(probe, "increment-server").click()
    expect_text(by(probe, "count"), "1")
    assert time.monotonic() - started >= 0.8


def test_debug_profiling_counts_events_and_patches(probe):
    probe.evaluate("wireview.debug.enableProfiling()")
    for n in ("1", "2"):
        by(probe, "increment-server").click()
        expect_text(by(probe, "count"), n)
    report = probe.evaluate("wireview.debug.profilingReport()")
    assert report["enabled"] is True
    assert report["eventCount"] >= 2
    assert report["roundTrip"]["count"] >= 2
    assert report["patch"]["count"] >= 2
