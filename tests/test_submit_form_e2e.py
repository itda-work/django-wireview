"""A form the server has the page submit, and a form locked while it is submitted, in a browser (#246, GAP-050).

``wire.submit_form()`` rides on the render that answers the handler: the page
paints it and posts the form as the browser would -- its fields, its
``{% csrf_token %}``, the button the user submitted it with and that button's
``formaction`` -- to a view Django's CSRF check guards, once, and only to the
page's own origin. The URL judged is the URL posted to, whatever the form's
fields are called. A submit locks its form until its answer: the inputs
read-only, the buttons disabled, and each gets back what it had.

Fixture: tests/testproj/submitprobe/.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import OFFLINE_SHIM, WAIT_TIMEOUT, expect_text, open_live, wait_live
from testproj.e2e_server import serve
from testproj.submitprobe import live, views

pytestmark = pytest.mark.e2e

LOGIN_LIVE = "[data-name=SubmitLogin][data-is-live='true']"
LOCK_LIVE = "[data-name=SubmitLock][data-is-live='true']:not(.wireview-disconnected)"
# Longer than the page holds a form whose submission left it where it is (SUBMITTED_HOLD_MS)
STAYED_FOR = 12_000


@pytest.fixture(scope="function")
def server():
    views.POSTED.clear()
    views.STAYED.update(dict.fromkeys(views.STAYED, 0))
    live.GATE.clear()
    live.HEARD.clear()
    with serve() as base_url:
        yield base_url
    live.GATE.set()


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def console(page):
    """What the page said on its console, as (type, text)."""
    said: list[tuple[str, str]] = []
    page.on("console", lambda message: said.append((message.type, message.text)))
    return said


@pytest.fixture
def login(page, server, console):
    open_live(page, f"{server}/submitprobe/", selector=LOGIN_LIVE)
    return page


@pytest.fixture
def lock(page, server):
    open_live(page, f"{server}/submitprobe/lock/", selector=LOCK_LIVE)
    return page


def by(page, testid: str):
    return page.get_by_test_id(testid)


def errors(console) -> list[str]:
    return [text for kind, text in console if kind == "error"]


def said(page, console, words: str) -> None:
    """Wait until the page reported an error with ``words`` in it."""
    for _ in range(int(WAIT_TIMEOUT * 10)):
        if any(words in text for text in errors(console)):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"the page never reported {words!r}; it said {console}")


# ---------------------------------------------------------------------------
# wire.submit_form(): the server has the page submit
# ---------------------------------------------------------------------------


def test_a_checked_login_is_posted_with_its_token_and_signs_the_browser_in(login):
    by(login, "username").fill("ada")
    by(login, "password").fill("nope")
    by(login, "plain").click()
    expect_text(by(login, "error"), "wrong password")
    assert views.POSTED == []

    by(login, "password").fill("secret")
    by(login, "plain").click()
    # The view set the session cookie: the page it redirected to knows the user
    expect_text(login.get_by_test_id("welcome"), "welcome ada")
    (posted,) = views.POSTED
    fields = posted["fields"]
    assert fields["username"] == ["ada"]
    assert fields["password"] == ["secret"]
    # The button the user submitted it with, and no other
    assert fields["mode"] == ["plain"]
    assert len(fields["csrfmiddlewaretoken"]) == 1
    assert posted["query"] == ""


def test_the_submitter_goes_with_its_formaction(login):
    by(login, "username").fill("bo")
    by(login, "password").fill("secret")
    by(login, "remember").click()
    expect_text(login.get_by_test_id("welcome"), "welcome bo")
    (posted,) = views.POSTED
    assert posted["fields"]["mode"] == ["remember"]
    assert posted["query"] == "via=formaction"


def test_enter_submits_with_the_default_button(login):
    by(login, "username").fill("cy")
    by(login, "password").fill("secret")
    by(login, "password").press("Enter")
    expect_text(login.get_by_test_id("welcome"), "welcome cy")
    # Implicit submission's submitter is the first submit button, as the browser sends it
    assert views.POSTED[0]["fields"]["mode"] == ["plain"]


def test_a_double_click_checks_once_and_posts_once(login):
    by(login, "username").fill("di")
    by(login, "password").fill("secret")
    by(login, "plain").dblclick()
    expect_text(login.get_by_test_id("welcome"), "welcome di")
    assert len(views.POSTED) == 1


def test_fields_named_after_the_forms_own_properties_change_nothing(login, console):
    # name="action", "method", "target", "elements", "submit": the form still
    # posts to its own action, in its own window, with all its fields
    by(login, "shadow-go").click()
    expect_text(login.get_by_test_id("welcome"), "welcome shade")
    (posted,) = views.POSTED
    assert posted["query"] == "via=shadow"
    assert posted["fields"]["part"] == ["first", "button", "last"]
    fields = posted["fields"]
    assert fields["action"] == ["http://elsewhere.invalid/"]
    assert fields["method"] == ["get"]
    assert fields["target"] == ["_blank"]
    assert fields["elements"] == ["e"]
    assert fields["submit"] == ["s"]
    assert errors(console) == []


def test_a_submitter_sending_a_same_origin_form_elsewhere_is_refused(login, console):
    # The form's action is this origin's; the URL the submission would read is the submitter's
    by(login, "crossed-go").click()
    said(login, console, "elsewhere.invalid/steal/")
    expect(by(login, "crossed-field")).to_be_editable()
    assert views.POSTED == []
    assert login.url.endswith("/submitprobe/")


def test_a_submitter_bringing_another_origins_form_home_is_followed(login, console):
    by(login, "rescued-go").click()
    expect_text(login.get_by_test_id("welcome"), "welcome rescued")
    (posted,) = views.POSTED
    assert posted["query"] == "via=rescued"
    assert posted["fields"]["mode"] == ["home"]
    assert errors(console) == []


def test_a_form_of_another_origin_is_not_submitted(login, console):
    by(login, "elsewhere").click()
    said(login, console, "only to this page's origin")
    expect(by(login, "away-username")).to_be_editable()
    assert login.url.endswith("/submitprobe/")


def test_a_selector_that_finds_no_form_is_reported(login, console):
    by(login, "missing").click()
    said(login, console, "found no form")


def locked(page, testid: str, *, timeout: float = WAIT_TIMEOUT * 1000) -> None:
    """Wait until the field ``testid`` is read-only.

    Read off the DOM, not with ``expect``: Playwright waits for a navigation in
    flight before it looks, and one a 204 or a download answers never lands.
    """
    page.wait_for_function(f"() => document.querySelector('[data-testid={testid}]').readOnly", timeout=timeout)


def unlocked(page, testid: str, *, timeout: float = WAIT_TIMEOUT * 1000) -> None:
    page.wait_for_function(f"() => !document.querySelector('[data-testid={testid}]').readOnly", timeout=timeout)


def test_an_answer_that_keeps_the_page_lets_the_form_go_without_posting_again(login):
    by(login, "nothing-go").click()
    # Locked while the browser might be leaving: a 204 says nothing the page hears
    locked(login, "nothing-field")
    unlocked(login, "nothing-field", timeout=STAYED_FOR)
    assert login.evaluate("() => document.querySelector('[data-testid=nothing-go]').disabled") is False
    assert views.STAYED["nothing"] == 1
    assert login.url.endswith("/submitprobe/")
    # The user's again: another submit is a second request, as with any form.
    # Clicked by script: Playwright's click waits for the 204's navigation too
    login.evaluate("() => document.querySelector('[data-testid=nothing-go]').click()")
    locked(login, "nothing-field")
    unlocked(login, "nothing-field", timeout=STAYED_FOR)
    assert views.STAYED["nothing"] == 2


def test_a_download_lets_the_form_go(login):
    with login.expect_download() as download:
        by(login, "download-go").click()
    assert download.value.suggested_filename == "report.txt"
    unlocked(login, "download-field", timeout=STAYED_FOR)
    assert views.STAYED["download"] == 1
    assert login.url.endswith("/submitprobe/")


def test_a_submission_to_another_window_leaves_the_form_at_once(login):
    with login.context.expect_page() as opened:
        by(login, "popup-go").click()
    expect_text(opened.value.get_by_test_id("popup"), "p")
    # Not the hold of a page that may be leaving
    expect(by(login, "popup-field")).to_be_editable(timeout=2_000)
    assert views.STAYED["popup"] == 1


# ---------------------------------------------------------------------------
# A submit locks its form until its answer
# ---------------------------------------------------------------------------


def test_a_submit_locks_its_form_and_gives_each_control_back_what_it_had(lock):
    by(lock, "note").fill("hello")
    by(lock, "save").click()
    expect(by(lock, "save")).to_have_text("Saving...")

    expect(by(lock, "note")).not_to_be_editable()
    expect(by(lock, "body")).not_to_be_editable()
    expect(by(lock, "fixed")).not_to_be_editable()
    # Tied to the form from outside it
    expect(by(lock, "outside")).not_to_be_editable()
    expect(by(lock, "save")).to_be_disabled()
    expect(by(lock, "plain-button")).to_be_disabled()
    expect(by(lock, "off-button")).to_be_disabled()
    # No read-only state the browser honours: left alone
    expect(by(lock, "agree")).to_be_enabled()
    expect(by(lock, "size")).to_be_enabled()
    # A second submit meanwhile sends nothing
    lock.evaluate("() => document.getElementById('lock-form').requestSubmit()")
    lock.wait_for_timeout(200)

    live.GATE.set()
    expect_text(by(lock, "saved"), "1")
    expect_text(by(lock, "saved-note"), "hello")
    expect(by(lock, "note")).to_be_editable()
    expect(by(lock, "body")).to_be_editable()
    expect(by(lock, "outside")).to_be_editable()
    expect(by(lock, "save")).to_be_enabled()
    expect(by(lock, "save")).to_have_text("Save")
    expect(by(lock, "plain-button")).to_be_enabled()
    # What was read-only or disabled before stays so
    expect(by(lock, "fixed")).not_to_be_editable()
    expect(by(lock, "off-button")).to_be_disabled()
    # One event, and its submitter went with it though the lock had disabled it
    assert live.HEARD == ["slow:hello:go=1"]


def test_the_answers_html_has_the_last_word_on_a_control(lock):
    by(lock, "finish").click()
    expect(by(lock, "finish-field")).not_to_be_editable()
    live.GATE.set()
    expect(by(lock, "finish-field")).to_be_editable()
    # The server draws it disabled now: the lock does not give back the enabled one
    expect(by(lock, "finish")).to_be_disabled()
    lock.wait_for_timeout(200)
    expect(by(lock, "finish")).to_be_disabled()


def test_a_handler_that_raises_unlocks_its_form(lock):
    by(lock, "broken").click()
    expect(by(lock, "broken-field")).not_to_be_editable()
    live.GATE.set()
    expect(by(lock, "broken-field")).to_be_editable()
    expect(by(lock, "broken")).to_be_enabled()
    wait_live(lock, LOCK_LIVE)


def test_a_lost_connection_unlocks_the_form(page, server):
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/submitprobe/lock/", selector=LOCK_LIVE)
    by(page, "note").fill("lost")
    by(page, "save").click()
    expect(by(page, "note")).not_to_be_editable()
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#lock")).to_have_class(re.compile("wireview-disconnected"))
    expect(by(page, "note")).to_be_editable()
    expect(by(page, "save")).to_be_enabled()
    expect(by(page, "save")).to_have_text("Save")
    live.GATE.set()
