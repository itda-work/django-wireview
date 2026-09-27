"""Form handling in a real browser: feedback, Django form validation, debounce, throttle (#110).

``wire-feedback-for`` and ``.throttle`` had no test at all, and ``.debounce`` was
only checked for keeping two timers apart, not for coalescing. The Django form
pattern the documentation teaches did not run as written.

Fixture: tests/testproj/formprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

HIDDEN = re.compile(r"\bwire-no-feedback\b")


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def probe(page, server):
    open_live(page, f"{server}/formprobe/")
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def test_a_valid_django_form_saves(probe):
    by(probe, "email").fill("a@example.com")
    by(probe, "name").fill("Ann")
    by(probe, "submit").click()
    expect_text(by(probe, "saved"), "a@example.com")


def test_feedback_waits_for_the_field_to_be_touched(probe):
    # Rendered by a live validation would be the usual case; a hidden error is
    # what a field the user has not reached yet must show.
    by(probe, "email").fill("not an email")
    by(probe, "email").press("Tab")  # leaves email: touched
    by(probe, "name").fill("far too long")
    # A submit would touch everything; call the handler the way a change binding would.
    probe.evaluate("""() => wireview.send(document.querySelector('[data-testid=form]'), "save", {
        email: "not an email", name: "far too long"})""")

    expect(by(probe, "email-errors")).not_to_have_class(HIDDEN)
    expect(by(probe, "name-errors")).to_have_class(HIDDEN)

    by(probe, "name").blur()
    expect(by(probe, "name-errors")).not_to_have_class(HIDDEN)


def test_a_submit_shows_every_fields_feedback(probe):
    by(probe, "email").fill("not an email")
    by(probe, "email").press("Enter")  # name never touched
    expect(by(probe, "email-errors")).to_be_visible()
    expect(by(probe, "name-errors")).to_be_visible()


def test_debounce_sends_once_after_the_typing_stops(probe):
    by(probe, "debounced").press_sequentially("abcde", delay=50)
    expect_text(by(probe, "typed"), "1")
    probe.wait_for_timeout(500)  # nothing else was waiting to go
    expect_text(by(probe, "typed"), "1")


def test_throttle_lets_one_through_per_window(probe):
    fire = """() => {
      const input = document.querySelector('[data-testid=throttled]');
      for (let i = 0; i < 5; i++) input.dispatchEvent(new Event("input", { bubbles: true }));
    }"""
    probe.evaluate(fire)
    expect_text(by(probe, "throttled-count"), "1")
    probe.wait_for_timeout(600)  # the 500ms window is over
    probe.evaluate(fire)
    expect_text(by(probe, "throttled-count"), "2")
