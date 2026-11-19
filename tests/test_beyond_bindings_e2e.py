"""Bindings that listen beyond their element, in a browser (#234, GAP-038).

The table in ``testproj.domprobe.scenarios`` holds the browser and the test
helpers to the same answers for a click away, keys with nothing focused, a
``.stop`` and the component a binding belongs to. These are what the table
cannot say: an IME's keys, a binding the page removed and drew again, a
reconnect, the window's own ``blur``, and a menu the click opens.
"""

import re

import pytest
from playwright.sync_api import expect
from testproj.domprobe.live import HEARD
from testproj.domprobe.scenarios import by
from testproj.e2e_browser import OFFLINE_SHIM, expect_text, open_live, wait_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def closed(*where: str) -> list[str]:
    return [h["where"] for h in heard("closed") if h["where"] in where]


def heard(handler: str) -> list[dict]:
    return [kwargs for cls, name, kwargs in HEARD if cls == "DomBeyond" and name == handler]


def expect_last(page, text: str) -> None:
    """The probe's ``heard`` output ends with ``text``: the handlers in it ran, in that order."""
    expect_text(by(page, "heard-beyond"), re.compile(rf"(^|\s){re.escape(text)}$"))


def press_and_settle(page, key: str = "Escape") -> None:
    """A key with nothing focused, then the toggle's click: the server answers in order, so once the
    click is heard the key was too, if anything heard it."""
    page.keyboard.press(key)
    by(page, "toggle-keys").click()
    expect_last(page, "toggle_keys")


@pytest.fixture
def probe(page, server):
    open_live(page, f"{server}/domprobe/")
    HEARD.clear()
    return page


def test_a_key_an_ime_composes_with_is_no_window_shortcut(probe):
    """Composing Hangul in a field, Escape cancels the composition and the first key that starts one
    is keyCode 229: neither is the page's Escape, nor a key the page hears at all."""
    probe.evaluate(
        """() => {
          const note = document.querySelector('[data-testid=note]');
          note.focus();
          const key = (init, keyCode) => {
            const event = new KeyboardEvent('keydown', { bubbles: true, ...init });
            Object.defineProperty(event, 'keyCode', { value: keyCode });
            note.dispatchEvent(event);
          };
          key({ key: 'Escape', isComposing: true }, 229);
          key({ key: 'Process', isComposing: false }, 229);
        }"""
    )
    by(probe, "note").press("Escape")
    expect_last(probe, "field_escaped escaped typed")

    assert len(heard("escaped")) == 1
    assert len(heard("typed")) == 1
    assert len(heard("field_escaped")) == 1


def test_a_binding_the_render_removed_is_not_heard_and_drawn_again_is_heard_once(probe):
    by(probe, "toggle-keys").click()
    expect(by(probe, "keys")).to_have_count(0)
    press_and_settle(probe)
    expect(by(probe, "keys")).to_have_count(1)
    assert heard("escaped") == []

    probe.keyboard.press("Escape")
    expect_last(probe, "escaped typed")

    assert len(heard("escaped")) == 1


def test_after_a_reconnect_a_window_binding_is_heard_once(page, server):
    page.add_init_script(OFFLINE_SHIM)
    open_live(page, f"{server}/domprobe/")
    HEARD.clear()
    page.evaluate("() => { window.__link.offline = true; window.__link.sockets.forEach((s) => s.close()); }")
    expect(page.locator("#beyond")).to_have_class(re.compile("wireview-disconnected"))
    # Nothing is sent while the page has no connection
    page.keyboard.press("Escape")
    page.evaluate("() => { window.__link.offline = false; }")
    wait_live(page)
    expect(page.locator("#beyond")).not_to_have_class(re.compile("wireview-disconnected"))

    page.keyboard.press("Escape")
    expect_last(page, "escaped typed")
    # The window's click binding hears the toggle's click after it
    by(page, "toggle-keys").click()
    expect_last(page, "toggle_keys anywhere")

    assert len(heard("escaped")) == 1


def test_the_window_blur_is_the_windows_not_a_fields(probe):
    """``blur.window`` is the tab losing focus. A field's blur does not bubble: the window does not hear it."""
    by(probe, "note").focus()
    by(probe, "note").blur()
    probe.evaluate("() => window.dispatchEvent(new FocusEvent('blur'))")
    expect_last(probe, "tab_left")
    by(probe, "toggle-keys").click()
    expect_last(probe, "toggle_keys anywhere")

    assert len(heard("tab_left")) == 1


def test_the_click_that_opens_a_menu_does_not_close_it(probe):
    by(probe, "open").click()
    expect(by(probe, "popup")).to_be_visible()
    # The page answers the window's click.anywhere: the open click reached the server
    expect_last(probe, "closed anywhere")
    assert closed("menu", "popup") == ["menu"]

    by(probe, "outside").click()
    expect_last(probe, "outside anywhere")

    assert closed("menu", "popup") == ["menu", "menu", "popup"]


def test_a_reset_is_no_change_on_the_page(probe):
    """wireview dispatches a reset's input and change for the form: its own bindings hear them, the
    window's do not -- nothing changed on the page."""
    # Set from a script: typed and left, the field would send a change of its own, which the window hears
    probe.evaluate("() => { document.querySelector('[data-testid=kept]').value = 'typed'; }")
    by(probe, "beyond-reset").click()
    expect_last(probe, "form_changed")

    assert heard("anywhere_changed") == []
    assert heard("form_changed")[-1]["kept"] == "k0"
