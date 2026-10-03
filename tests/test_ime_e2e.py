"""Renders that land while an IME is composing Hangul in a field (#168).

A composition is the IME's: while it runs, the field shows a syllable the user
has not finished ("하" on the way to "한"). A render that writes the field's
value, or takes its focus, ends the composition under the IME -- the browser
drops it without a ``compositionend`` and the next keystroke starts a new one,
which is where a doubled syllable ("하한") comes from. A morph must leave such a
field alone, whoever sent the render:

- the field's own debounced ``input`` binding, which fires during a composition
  too, so the server renders the composing text back while the user is still
  composing;
- someone else's broadcast, which renders the component with whatever the
  server last heard from the field.

**How the IME is driven.** Chrome DevTools Protocol's ``Input.imeSetComposition``
and ``Input.insertText``: the browser's own composition path, the one an OS IME
feeds -- ``compositionstart``/``compositionupdate``, ``input`` events with
``isComposing``, the field's composition range, and a composition the browser
drops when the page writes the value. What it does not reproduce is the OS IME
itself: its candidate window, and how a given IME reacts to a dropped
composition (macOS's and Windows' Korean IMEs differ). ``composition_breaks``
below checks that this way of driving notices a broken composition at all.

Fixture: tests/testproj/imeprobe/.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, WAIT_TIMEOUT, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

#: The page records every composition boundary, with the field's value at the time.
RECORD = """() => {
  window.__compositions = [];
  for (const type of ["compositionstart", "compositionend"]) {
    document.addEventListener(type, (e) => window.__compositions.push([type, e.data ?? "", e.target.value]), true);
  }
}"""


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


class Ime:
    """An IME composing in the page's focused field, through the browser's composition path."""

    def __init__(self, page) -> None:
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        page.evaluate(RECORD)

    def compose(self, text: str) -> None:
        """Show ``text`` as the syllable being composed, caret after it."""
        self.cdp.send("Input.imeSetComposition", {"text": text, "selectionStart": len(text), "selectionEnd": len(text)})

    def commit(self, text: str) -> None:
        """Finish the composition with ``text``, as picking the syllable does."""
        self.cdp.send("Input.insertText", {"text": text})

    def boundaries(self) -> list[list[str]]:
        return self.page.evaluate("window.__compositions")

    def starts(self) -> int:
        return sum(1 for kind, *_ in self.boundaries() if kind == "compositionstart")

    def ended_with(self) -> list[str]:
        return [data for kind, data, _ in self.boundaries() if kind == "compositionend"]


def by(page, testid: str):
    return page.get_by_test_id(testid)


def type_hangul(page, ime: Ime, field: str, render_lands) -> None:
    """Type 한글 into ``field`` -- two compositions, ㅎ 하 한 and ㄱ 그 글 -- with a
    render landing in the middle of each, while the syllable is still open.

    ``render_lands(n, type_on)`` makes the ``n``th render land and calls
    ``type_on()`` -- the next jamo -- before or after it, as the case needs.
    """
    by(page, field).focus()
    ime.compose("ㅎ")
    ime.compose("하")
    render_lands(1, lambda: ime.compose("한"))
    ime.commit("한")
    ime.compose("ㄱ")
    ime.compose("그")
    render_lands(2, lambda: ime.compose("글"))
    ime.commit("글")


def assert_compositions_survived(page, ime: Ime, field: str) -> None:
    # One composition per syllable, each ended by the IME's commit and nothing else.
    # A render that dropped one shows up as a third start, and as a doubled syllable.
    assert ime.ended_with() == ["한", "글"], ime.boundaries()
    assert ime.starts() == 2, ime.boundaries()
    expect(by(page, field)).to_have_value("한글")


# --- the control: a write to the value breaks a composition -------------------------------


@pytest.mark.parametrize("field", ["memo", "notes"])
def test_the_probe_sees_a_composition_that_a_value_write_breaks(page, server, field):
    """Without this, a way of driving the IME that never noticed a break would pass
    every test below."""
    open_live(page, f"{server}/imeprobe/")
    ime = Ime(page)
    by(page, field).focus()
    ime.compose("ㅎ")
    ime.compose("하")
    page.evaluate("field => { document.querySelector(`[data-testid=${field}]`).value = 'x'; }", field)
    ime.compose("한")
    ime.commit("한")

    assert ime.starts() == 2, "the write dropped the composition and the next jamo started another"
    expect(by(page, field)).to_have_value("x한")


# --- a render from the field's own debounced input ----------------------------------------


@pytest.mark.parametrize("field", ["q", "notes"])
def test_the_fields_own_debounced_render_leaves_the_composition_alone(page, server, field):
    """``input`` fires on every jamo, ``isComposing`` or not, so the debounced event
    sends the open syllable and the server renders it back mid-composition.

    The render is held until the user has typed the next jamo: on a real network
    it arrives after the user moved on, carrying a value the field no longer has
    ("하" while it shows "한"). Delivered at once it carries what the field shows,
    and a morph that wrote it would write the same value and break nothing.
    """
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/imeprobe/")
    ime = Ime(page)
    seen = by(page, f"{field}-seen")
    page.evaluate(
        """field => {
          const seen = document.querySelector(`[data-testid=${field}-seen]`);
          window.__shown = [];
          new MutationObserver(() => window.__shown.push(seen.textContent)).observe(
            seen, {characterData: true, childList: true, subtree: true});
        }""",
        field,
    )

    def render_lands(n: int, type_on) -> None:
        sent = "하" if n == 1 else "한그"
        page.evaluate("window.__inbox.holding = true")
        page.wait_for_function(
            "sent => window.__inbox.held.some((m) => m.command === 'render' && JSON.stringify(m).includes(sent))",
            arg=sent,
            timeout=WAIT_TIMEOUT * 1000,
        )
        type_on()
        page.evaluate("window.__inbox.release()")
        # The open syllable the debounce sent, rendered back while another one is
        # open. Its own echo follows a debounce later, so the screen is read from
        # what it has shown rather than what it shows now.
        page.wait_for_function("sent => window.__shown.includes(sent)", arg=sent, timeout=WAIT_TIMEOUT * 1000)

    type_hangul(page, ime, field, render_lands)

    assert_compositions_survived(page, ime, field)
    expect_text(seen, "한글")


# --- a render someone else's broadcast caused ----------------------------------------------


@pytest.mark.parametrize("field", ["q", "notes", "memo"])
def test_a_broadcast_render_leaves_the_composition_alone(page, browser, server, field):
    open_live(page, f"{server}/imeprobe/")
    other_context = browser.new_context()
    try:
        other = other_context.new_page()
        open_live(other, f"{server}/imeprobe/")
        ime = Ime(page)

        def render_lands(n: int, type_on) -> None:
            by(other, "ping").click()
            expect_text(by(page, "pings"), str(n))
            type_on()

        type_hangul(page, ime, field, render_lands)
    finally:
        other_context.close()

    assert_compositions_survived(page, ime, field)
