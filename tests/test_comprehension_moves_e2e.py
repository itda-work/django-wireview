"""Rearranged list items leave the browser where positional updates would (GAP-030, #69).

The new diff form changes what goes over the wire, not what the DOM ends up as:
the client rebuilds the same HTML either way and idiomorph morphs it. So the
check is a comparison. The same edits run twice on a list whose rows hold state
only the browser knows (a marker on each row element, typed input values,
focus): once as the current client,
which names protocol version 2 and receives ``{"k": ...}``, and once with the
version stripped from the socket URL, which is what a bundle from before this
change looks like to the server. Both runs must end in the same DOM, and each
run must have received the form it was meant to, or the comparison is between
two copies of one path.

What the comparison found about the state itself, the same on both paths: the
row elements survive the moves (idiomorph matches them by id), and values typed
into inputs the server does not render are reset to the server's HTML on the
next render. That second part predates GAP-030 and is not changed by it.
"""

import json

import pytest
from testproj.e2e_browser import expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e

# Stands in for an old bundle: the page's socket opens without ?vsn=.
STRIP_VERSION = """
(() => {
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(url, protocols) {
      super(String(url).replace(/[?&]vsn=\\d+/, ""), protocols);
    }
  };
})();
"""

EDITS = ["rotate", "insert-front", "rotate", "remove-first", "rotate"]


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def _has_moves(value) -> bool:
    if isinstance(value, dict):
        return "k" in value or any(_has_moves(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_moves(v) for v in value)
    return False


def _snapshot(page) -> dict:
    return page.evaluate(
        """() => ({
          rows: [...document.querySelectorAll('li')].map(li => li.id),
          markers: [...document.querySelectorAll('li')].map(li => li.__probe ?? null),
          labels: [...document.querySelectorAll('li label')].map(l => l.textContent.trim()),
          values: Object.fromEntries([...document.querySelectorAll('li input')].map(i => [i.id, i.value])),
          focused: document.activeElement?.id ?? null,
        })"""
    )


def _expected_rows() -> list[list[str]]:
    """The row order after each edit, as ListProbe's handlers compute it."""
    rows = [f"r{n}" for n in range(8)]
    orders = []
    for edit in EDITS:
        if edit == "rotate":
            rows.insert(0, rows.pop())
        elif edit == "insert-front":
            rows.insert(0, f"r{len(rows)}")
        elif edit == "remove-first":
            rows.pop(0)
        orders.append(list(rows))
    return orders


def _run(page, server: str) -> tuple[list[dict], list[dict]]:
    """Type into some rows, focus one, apply the edits; the DOM after each edit and the renders received."""
    renders: list[dict] = []

    def on_socket(ws):
        def on_frame(payload):
            message = json.loads(payload)
            if message.get("command") == "render":
                renders.append(message["payload"]["diff"])

        ws.on("framereceived", on_frame)

    page.on("websocket", on_socket)
    open_live(page, f"{server}/listprobe/")
    for row in ("r0", "r3", "r7"):
        page.locator(f"#in-{row}").fill(f"typed {row}")
    page.locator("#in-r3").focus()
    # A marker only the element object carries: it survives a move, not a rebuild.
    page.evaluate("() => document.querySelectorAll('li').forEach(li => { li.__probe = li.id; })")

    snapshots = []
    for edit, rows in zip(EDITS, _expected_rows(), strict=True):
        page.locator(f'[data-testid="{edit}"]').click()
        expect_text(page.locator("li label"), rows)  # type: ignore[arg-type]
        # The click moved focus to the button; put it back so every step starts alike.
        snapshots.append(_snapshot(page))
        page.locator("#in-r3").focus() if page.locator("#in-r3").count() else None
    return snapshots, renders[1:]


def test_moving_rows_ends_in_the_same_dom_with_either_form(browser, server):
    current = browser.new_page()
    current_snapshots, current_renders = _run(current, server)
    current.close()

    old = browser.new_page()
    old.add_init_script(STRIP_VERSION)
    old_snapshots, old_renders = _run(old, server)
    old.close()

    assert any(_has_moves(r) for r in current_renders), "the current client never received a rearrangement"
    assert not any(_has_moves(r) for r in old_renders), "a client without ?vsn= received a rearrangement"
    assert current_snapshots == old_snapshots
    assert [snap["labels"] for snap in current_snapshots] == _expected_rows()
    # The rows are the same element objects after the moves: idiomorph matched them
    # by id. Only the row inserted by the edit is new.
    after_rotate = current_snapshots[0]
    assert after_rotate["markers"] == after_rotate["rows"], after_rotate
