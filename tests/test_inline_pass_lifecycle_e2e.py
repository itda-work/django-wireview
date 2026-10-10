"""A LiveComponent a nested component hides by its drawer's prop, in a browser.

The server-side contract is tests/test_inline_pass_lifecycle.py. Here the page
applies the root's render, which carries the leaf's lifecycle: the leaf the box
hid has left, the one it shows again starts anew and answers its own events,
and a note the root passes through the box reaches it.

Fixture: tests/testproj/nestprobe/ (``?hidden=1``: the box starts with the leaf hidden;
the ``visit-shown`` link is a boosted visit to the page with it shown; ``?anon=1``: the
root also draws a component without an id, a new one in each of its renders; ``?ignore=1``:
one under ``wire-update="ignore"``, which the page keeps while the root's renders retire it).
"""

import re
import time

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture
def heard():
    from testproj.nestprobe.live import HEARD

    HEARD.clear()
    yield HEARD
    HEARD.clear()


def _bump(page, times: int) -> None:
    for count in range(1, times + 1):
        page.get_by_test_id("bump").click()
        expect_text(page.get_by_test_id("leaf-count"), str(count))


def test_the_leaf_the_box_hid_leaves_and_comes_back_anew(page, server, heard):
    open_live(page, f"{server}/nestprobe/")
    _bump(page, 3)

    page.get_by_test_id("toggle").click()
    expect_text(page.get_by_test_id("box-shown"), "False")
    expect_count(page.get_by_test_id("leaf-count"), 0)
    assert ("leaving", "nest-leaf", 3) in heard

    page.get_by_test_id("toggle").click()
    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 1)


def test_a_note_the_root_passes_through_the_box_reaches_the_leaf(page, server, heard):
    open_live(page, f"{server}/nestprobe/")
    _bump(page, 2)

    page.get_by_test_id("write").click()

    expect_text(page.get_by_test_id("leaf-note"), "!")
    expect_text(page.get_by_test_id("leaf-count"), "2")


def test_a_leaf_the_box_shows_first_is_drawn_and_answers(page, server, heard):
    open_live(page, f"{server}/nestprobe/?hidden=1")
    expect_count(page.get_by_test_id("leaf-count"), 0)

    page.get_by_test_id("toggle").click()

    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 2)
    assert [hook for hook, *_ in heard] == ["joined"]


def test_a_boosted_visit_joins_the_leaf_once(page, server, heard):
    # The visit brings the root and the box as new elements under the same ids
    # and the page joins both, the root first. The root's join render is not
    # the one to settle the box's leaf: the box's join right behind it is. The
    # leaf the root's join render built never joined, and gets no leaving().
    open_live(page, f"{server}/nestprobe/?hidden=1")

    page.get_by_test_id("visit-shown").click()

    expect_text(page.get_by_test_id("leaf-count"), "0")
    _bump(page, 2)
    assert [hook for hook, *_ in heard] == ["joined"], heard

    # From then on, the root's render settles the box's leaf again
    page.get_by_test_id("toggle").click()
    expect_count(page.get_by_test_id("leaf-count"), 0)
    assert heard[-1] == ("leaving", "nest-leaf", 2), heard


#: Drops the page's leaves on their way to the socket: one lost with a connection, or never sent
DROP_LEAVES = """
const send = WebSocket.prototype.send;
WebSocket.prototype.send = function (data) {
  if (typeof data === "string" && JSON.parse(data).command === "leave") return;
  return send.call(this, data);
};
"""


#: What the page's sockets received, and the wireview:error events it heard
RECEIVED = """
window.__received = [];
window.__errors = [];
document.addEventListener("wireview:error", (e) => window.__errors.push(e.detail));
const Socket = window.WebSocket;
window.WebSocket = class extends Socket {
  constructor(...args) {
    super(...args);
    this.addEventListener("message", (e) => window.__received.push(JSON.parse(e.data)));
  }
};
"""


def _anon_hooks(heard, hook: str) -> list[str]:
    return [id for each, id, _ in heard if each == hook and id.startswith("rx-")]


def _until(check, what) -> None:
    """Wait for ``check()``: a fact about the server's ``heard``, which the page cannot see. The live server runs on."""
    deadline = time.monotonic() + 5
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(what)
        time.sleep(0.02)


@pytest.mark.parametrize("leaves", ["sent", "lost"])
def test_a_component_drawn_without_an_id_leaves_with_the_render_that_draws_another(page, server, heard, leaves):
    # Each render of the root draws a new NestAnon under a new rx-<uuid>. The
    # one it replaced retires with that render, and is kept only for the events
    # the page sent before it saw the render (a tombstone, #204): its leaving()
    # runs on the page's leave, and where that leave never comes, with the
    # connection (or a render ten seconds on). Left to that leave alone, the
    # ones it never came for stayed live for the connection's life.
    if leaves == "lost":
        page.add_init_script(DROP_LEAVES)
    open_live(page, f"{server}/nestprobe/?anon=1")
    page.get_by_test_id("anon-poke").click()
    expect_text(page.get_by_test_id("anon-note"), "poked")
    first = page.get_by_test_id("anon").get_attribute("id")
    # The first response's element, which the page joined as it connected: no
    # pass on this connection drew it, and the page's leave is what lets it go
    # once the root's first render replaced it (docs/design/live-component-ownership.md §3-6)
    connected = set(_anon_hooks(heard, "joined")) - {first}

    for count in range(1, 4):
        page.get_by_test_id("write").click()
        expect_text(page.get_by_test_id("anon-note"), "!" * count)
        shown = page.get_by_test_id("anon").get_attribute("id")
        replaced = {id for id in _anon_hooks(heard, "joined") if id != shown}
        if leaves == "sent":
            _until(lambda: replaced <= set(_anon_hooks(heard, "leaving")), heard)
        else:
            # Kept for the events the page may still send: nothing has left yet
            assert set(_anon_hooks(heard, "leaving")) <= connected, heard
        left = _anon_hooks(heard, "leaving")
        assert len(left) == len(set(left)), heard

    # The one on screen is joined and answers
    page.get_by_test_id("anon-poke").click()
    expect_text(page.get_by_test_id("anon-note"), "poked")
    assert ("poked", shown, 0) in heard
    assert shown not in _anon_hooks(heard, "leaving")

    # The connection ends: every one the page joined leaves, once
    joined = set(_anon_hooks(heard, "joined"))
    page.close()
    _until(lambda: joined <= set(_anon_hooks(heard, "leaving")), heard)
    left = _anon_hooks(heard, "leaving")
    assert len(left) == len(set(left)), heard


def test_a_retired_component_the_page_kept_answers_its_event_and_joins_again(page, server, heard):
    # Under wire-update="ignore" the page keeps the element of the NestAnon the
    # root's first render with a note drew; the next render draws another and
    # retires that one, kept for the page's events (a tombstone, #204): the
    # page never leaves it. The click on the kept element runs its handler
    # there, nothing of it is rendered, and the answer (gone) ends its loading
    # state; the page joins the element again from its state, which buries the
    # retired one -- the next click answers on screen. Before #204, nothing came
    # back and the button stayed loading.
    page.add_init_script(RECEIVED)
    open_live(page, f"{server}/nestprobe/?ignore=1")
    page.get_by_test_id("write").click()
    expect_text(page.get_by_test_id("anon-note"), "!")
    kept = page.get_by_test_id("anon").get_attribute("id")
    page.get_by_test_id("write").click()
    expect_text(page.get_by_test_id("leaf-note"), "!!")
    expect_text(page.get_by_test_id("anon-note"), "!")
    assert kept not in _anon_hooks(heard, "leaving"), heard

    poke = page.get_by_test_id("anon-poke")
    poke.click()
    expect(poke).not_to_have_class(re.compile("wireview-click-loading"))
    # The join's answer: a render carrying the server's version
    page.wait_for_function(
        "id => window.__received.some((m) => m.command === 'render' && m.payload.id === id && m.payload.vsn)",
        arg=kept,
    )
    assert heard.count(("poked", kept, 0)) == 1, heard
    assert kept in _anon_hooks(heard, "leaving"), heard
    assert page.evaluate("window.__received.filter((m) => m.command === 'gone').map((m) => m.payload.id)") == [kept]
    assert page.evaluate("window.__errors") == []
    expect_text(page.get_by_test_id("anon-note"), "!")

    expect_count(page.get_by_test_id("anon"), 1)
    poke.click()
    expect_text(page.get_by_test_id("anon-note"), "poked")
    assert heard.count(("poked", kept, 0)) == 2, heard
    assert _anon_hooks(heard, "joined").count(kept) == 2, heard
