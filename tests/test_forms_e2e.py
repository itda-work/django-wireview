"""Form handling in a real browser: feedback, Django form validation, debounce, throttle (#110).

``wire-feedback-for`` and ``.throttle`` had no test at all, and ``.debounce`` was
only checked for keeping two timers apart, not for coalescing. The Django form
pattern the documentation teaches did not run as written.

Fixture: tests/testproj/formprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, expect_text, open_live, wait_live
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


# A text of many lines' worth, longer than the name field takes
LONG = "긴 글을 쓴다 " * 40


def test_what_was_typed_stays_when_the_form_refuses_it(probe):
    """The render answering a submit writes the server's value into each input
    (static/wireview/values.mjs): the form keeps what was typed only if the state does (#267)."""
    by(probe, "name").fill(LONG)
    by(probe, "submit").click()  # email left empty: required

    expect(by(probe, "email-errors")).to_be_visible()
    expect(by(probe, "name-errors")).to_be_visible()
    expect(by(probe, "name")).to_have_value(LONG)
    expect(by(probe, "email")).to_have_value("")


def test_what_was_typed_stays_through_a_reconnect(page, server):
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/formprobe/")
    by(page, "name").fill(LONG)
    by(page, "submit").click()
    expect(by(page, "name-errors")).to_be_visible()

    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("[data-name=FormProbe]")).to_have_class(re.compile("wireview-disconnected"))
    page.evaluate("() => { window.__link.offline = false; }")
    wait_live(page, "[data-name=FormProbe][data-is-live='true']:not(.wireview-disconnected)")

    expect(by(page, "name")).to_have_value(LONG)


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


# -- feedback per form, and a form's reset (#244) ----------------------------
#
# Fixture: formprobe/feedback/ -- forms a and b both have an `email` field, b
# also owns `code` from outside it (form="fb"), and one `email` feedback sits
# outside every form.


@pytest.fixture
def feedback(page, server):
    open_live(page, f"{server}/formprobe/feedback/")
    return page


def test_a_field_touched_in_one_form_shows_that_forms_feedback_only(feedback):
    by(feedback, "a-email").fill("x")
    by(feedback, "a-email").press("Tab")

    expect(by(feedback, "a-errors")).not_to_have_class(HIDDEN)
    # Before #244 the touched names were the page's: form b showed a's
    expect(by(feedback, "b-errors")).to_have_class(HIDDEN)
    # Feedback outside every form follows the name wherever it was touched
    expect(by(feedback, "loose-errors")).not_to_have_class(HIDDEN)
    assert feedback.evaluate("""() => [
        wireview.feedback.isTouched("email", document.getElementById("fa")),
        wireview.feedback.isTouched("email", document.getElementById("fb")),
        wireview.feedback.isTouched("email"),
    ]""") == [True, False, True]


def test_a_submit_shows_its_own_forms_feedback_and_the_fields_it_owns_from_outside(feedback):
    by(feedback, "b-submit").click()
    expect_text(by(feedback, "heard"), "submit-b")

    expect(by(feedback, "b-errors")).not_to_have_class(HIDDEN)
    expect(by(feedback, "b-code-errors")).not_to_have_class(HIDDEN)
    expect(by(feedback, "a-errors")).to_have_class(HIDDEN)


def test_a_field_tied_to_a_form_from_outside_is_touched_in_that_form(feedback):
    by(feedback, "b-code").fill("zz")
    by(feedback, "b-code").press("Tab")

    expect(by(feedback, "b-code-errors")).not_to_have_class(HIDDEN)
    expect(by(feedback, "b-errors")).to_have_class(HIDDEN)


def test_a_reset_hides_its_forms_feedback_and_sends_what_it_left(feedback):
    by(feedback, "b-submit").click()
    by(feedback, "a-email").fill("typed")
    by(feedback, "a-email").press("Tab")
    expect_text(by(feedback, "heard"), "submit-b a:typed")
    expect(by(feedback, "a-errors")).not_to_have_class(HIDDEN)

    by(feedback, "a-reset").click()

    expect_text(by(feedback, "heard"), "submit-b a:typed a:seed")
    expect(by(feedback, "a-email")).to_have_value("seed")
    expect(by(feedback, "a-errors")).to_have_class(HIDDEN)
    # The other form keeps what was touched in it
    expect(by(feedback, "b-errors")).not_to_have_class(HIDDEN)


def test_a_reset_sends_the_values_it_put_back(feedback):
    """Typed but not committed: only the reset's change goes, with the drawn value."""
    by(feedback, "a-email").fill("typed")
    feedback.evaluate("() => document.getElementById('fa').reset()")

    expect_text(by(feedback, "heard"), "a:seed")
    expect(by(feedback, "a-email")).to_have_value("seed")


def test_a_cancelled_reset_sends_nothing_and_keeps_the_feedback(feedback):
    by(feedback, "a-email").fill("typed")
    by(feedback, "a-email").press("Tab")
    expect_text(by(feedback, "heard"), "a:typed")
    feedback.evaluate("() => document.getElementById('fa').addEventListener('reset', (e) => e.preventDefault())")

    by(feedback, "a-reset").click()
    by(feedback, "ping").click()

    # No change of the reset's between the two
    expect_text(by(feedback, "heard"), "a:typed ping")
    expect(by(feedback, "a-errors")).not_to_have_class(HIDDEN)
