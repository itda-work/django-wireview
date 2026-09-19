"""What the user types survives renders that are not about it (#91).

A morph copied the server's value into every input it touched, and none of
these inputs renders its value, so any render (another field's event, a click
elsewhere) reset them to empty, the focused one included. The rule now
(wireview/static/wireview/values.mjs): a field the user edited keeps its value,
unless the render answers an action from that field or its form, or the server
sends a new value for a field that is not focused.

The first three tests failed before; the last three pin what must not change:
the answer to Enter or a submit still empties the field, and the server can
still set a field the user is not in.
"""

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid: str):
    return page.get_by_test_id(testid)


@pytest.fixture
def probe(page, server):
    open_live(page, f"{server}/valueprobe/")
    return page


def test_edited_fields_survive_a_render_from_elsewhere(probe):
    page = probe
    by(page, "other").fill("unfocused text")
    by(page, "draft").fill("focused text")
    # Leave the debounce out of it: the render comes from a click on another element.
    by(page, "draft").focus()
    page.evaluate("() => document.querySelector('[data-testid=ping]').click()")
    expect_text(by(page, "pings"), "1")

    expect(by(page, "draft")).to_have_value("focused text")
    expect(by(page, "other")).to_have_value("unfocused text")


def test_typing_continues_through_the_answer_to_its_own_input_event(probe):
    page = probe
    draft = by(page, "draft")
    draft.press_sequentially("hel")
    expect_text(by(page, "typed"), "hel")  # the debounced event went and its render came back
    draft.press_sequentially("lo")

    expect(draft).to_have_value("hello")
    expect_text(by(page, "typed"), "hello")


def test_the_server_does_not_change_the_field_under_the_cursor(probe):
    page = probe
    field = by(page, "server-set")
    field.fill("mine")
    field.focus()
    # A render that changes this field's server value, not answering anything from it.
    page.evaluate("() => wireview.send(document.querySelector('[data-testid=ping]'), 'slow_set_from_server', {})")
    page.wait_for_timeout(600)  # the handler sleeps 0.3 s; nothing to wait on but the clock

    expect(field).to_have_value("mine")


def test_enter_still_empties_the_field_it_came_from(probe):
    page = probe
    by(page, "item").fill("buy milk")
    by(page, "item").press("Enter")

    expect_text(by(page, "added"), "buy milk")
    expect(by(page, "item")).to_have_value("")


def test_a_submit_still_empties_its_form(probe):
    page = probe
    by(page, "first").fill("a")
    by(page, "second").fill("b")
    by(page, "submit").click()

    expect_text(by(page, "submitted"), "a+b")
    expect(by(page, "first")).to_have_value("")
    expect(by(page, "second")).to_have_value("")


def test_the_server_can_set_a_field_the_user_is_not_in(probe):
    page = probe
    by(page, "server-set").fill("mine")
    by(page, "set-from-server").click()  # focus leaves the field

    expect(by(page, "server-set")).to_have_value("from server")
