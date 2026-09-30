"""A handler that raises, seen from the browser (#94).

It used to close the socket: the page flashed ``wireview-disconnected``,
reconnected and joined every component again. Now only the component that
raised joins again, from the state its element carries, so what the handler
changed before raising is gone, the button it disabled comes back, and the
socket is the one the page opened. A component that cannot join keeps what the
page rendered and is marked ``wireview-error``.

Fixture: tests/testproj/errorprobe/.
"""

import json
import threading

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, expect_text, open_live
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
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws))
    page.add_init_script(
        "window.__wireviewErrors = [];"
        "document.addEventListener('wireview:error', (e) => window.__wireviewErrors.push(e.detail));"
    )
    open_live(page, f"{server}/errorprobe/", selector="#box[data-is-live='true']")
    page.sockets = sockets
    return page


def test_what_a_raising_handler_changed_is_rolled_back_on_the_same_socket(probe):
    by(probe, "box-bump").click()
    expect_text(by(probe, "box-count"), "1")

    by(probe, "box-raise").click()
    probe.wait_for_function("window.__wireviewErrors.some((e) => e.id === 'box' && e.during === 'event')")
    by(probe, "box-bump").click()

    # 2, not 102: the +100 happened in an instance that was thrown away
    expect_text(by(probe, "box-count"), "2")
    expect(by(probe, "box-raise")).to_be_enabled()
    expect_text(by(probe, "box-raise"), "raise")
    expect(probe.locator("#box")).not_to_have_class("wireview-disconnected")
    assert len(probe.sockets) == 1


def test_the_other_components_and_what_the_user_typed_are_untouched(probe):
    by(probe, "other-bump").click()
    expect_text(by(probe, "other-count"), "1")
    by(probe, "box-draft").fill("still here")

    by(probe, "box-raise").click()
    probe.wait_for_function("window.__wireviewErrors.some((e) => e.id === 'box')")
    by(probe, "other-bump").click()

    expect_text(by(probe, "other-count"), "2")
    expect(by(probe, "box-draft")).to_have_value("still here")


def test_a_component_that_cannot_join_keeps_its_markup_and_is_marked(probe):
    broken = probe.locator("#broken")

    expect(broken).to_have_class("wireview-error")
    expect_text(by(probe, "broken-text"), "rendered by the page")
    failures = probe.evaluate("window.__wireviewErrors.filter((e) => e.id === 'broken' && e.during === 'join').length")
    assert failures == 1
    assert len(probe.sockets) == 1


@pytest.fixture
def next_late_join_fails(monkeypatch):
    """Once set, the next join of ``#late`` raises in ``joined()``, which answers it with an ``error``."""
    from testproj.errorprobe.live import ErrorBox

    armed = threading.Event()
    joined = ErrorBox.joined

    async def once(self):
        if self.id == "late" and armed.is_set():
            armed.clear()
            raise RuntimeError("errorprobe: this join fails on purpose")
        await joined(self)

    monkeypatch.setattr(ErrorBox, "joined", once)
    return armed


def _held(page, script: str) -> None:
    page.wait_for_function(f"window.__inbox.held.some((m) => {script})")


def test_an_error_for_a_join_the_page_replaced_leaves_the_new_element_alive(next_late_join_fails, page, server):
    # #139: the first join's error reached the page after it had sent the next
    # join under the id. It marked the new element and dropped its component,
    # and the second join's render found nothing to patch: dead until the page
    # connected again.
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/errorprobe/late/", selector="#late[data-is-live='true']")
    # The page learns from this answer that the server takes a join's ref
    page.wait_for_function("window.__inbox.seen.some((m) => m.command === 'render' && 'vsn' in m.payload)")

    next_late_join_fails.set()
    page.evaluate("window.__inbox.holding = true")
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    _held(page, "m.command === 'error' && m.payload.id === 'late'")
    by(page, "third").click()
    expect_text(by(page, "visit"), "third")
    _held(page, "m.command === 'render' && m.payload.id === 'late'")
    page.evaluate("window.__inbox.release()")

    by(page, "late-bump").click()
    expect_text(by(page, "late-count"), "1")
    expect(page.locator("#late")).not_to_have_class("wireview-error")


@pytest.fixture
def next_late_join_halts(monkeypatch):
    """Once set, the next join of ``#late`` over the socket halts, as an ``on_mount`` hook
    would, which answers it with a ``remove``. The page's own render of it does not."""
    from testproj.errorprobe.live import ErrorBox

    armed = threading.Event()
    mount = ErrorBox._mount

    async def once(self, params=None, session=None):
        if self.id == "late" and self.wire.channel_name and armed.is_set():
            armed.clear()
            self.wire.has_mounted = True
            self.wire.mount_halted = True
            return False
        return await mount(self, params, session)

    monkeypatch.setattr(ErrorBox, "_mount", once)
    return armed


def test_a_remove_for_a_join_the_page_replaced_leaves_the_new_element(next_late_join_halts, page, server):
    # #146: the halted join's remove reached the page after it had sent the next
    # join under the id. It took the new element away, and the page's leave for
    # it retired the instance the next join had made.
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/errorprobe/late/", selector="#late[data-is-live='true']")
    page.wait_for_function("window.__inbox.seen.some((m) => m.command === 'render' && 'vsn' in m.payload)")

    next_late_join_halts.set()
    page.evaluate("window.__inbox.holding = true")
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    _held(page, "m.command === 'remove' && m.payload.id === 'late'")
    by(page, "third").click()
    expect_text(by(page, "visit"), "third")
    _held(page, "m.command === 'render' && m.payload.id === 'late'")
    page.evaluate("window.__inbox.release()")

    by(page, "late-bump").click()
    expect_text(by(page, "late-count"), "1")


def test_a_join_the_page_waits_for_that_halts_takes_its_element_away(next_late_join_halts, page, server):
    # #146: while the page waits for a join it drops a remove without that
    # join's ref, so the waiting join's own remove must carry the ref, and the
    # page must read it. Else the element stays, live to the page and to no
    # instance on the server.
    open_live(page, f"{server}/errorprobe/late/", selector="#late[data-is-live='true']")
    next_late_join_halts.set()
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    expect(page.locator("#late")).to_have_count(0)


def test_a_live_components_render_for_a_parent_the_page_replaced_is_not_painted(page, server):
    # #146: a LiveComponent's own render, from the instance its parent's next
    # join replaced, painted the old instance's state over the new element: the
    # render is named by the child's id, which no join is.
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")
    page.wait_for_function("window.__inbox.seen.some((m) => m.command === 'render' && 'vsn' in m.payload)")

    page.evaluate("window.__inbox.holding = true")
    by(page, "nest-child-bump").click()
    _held(page, "m.command === 'render' && m.payload.id === 'nest-child'")
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    _held(page, "m.command === 'render' && m.payload.id === 'nest'")
    page.evaluate(
        """() => new Promise((done) => {
          window.__inbox.release(window.__inbox.held.findIndex((m) => m.payload.id === 'nest-child') + 1);
          requestAnimationFrame(() => requestAnimationFrame(done));
        })"""
    )
    expect_text(by(page, "nest-child-count"), "0")
    page.evaluate("window.__inbox.release()")

    by(page, "nest-child-bump").click()
    expect_text(by(page, "nest-child-count"), "1")


def test_a_live_components_remove_for_a_parent_the_page_replaced_leaves_the_new_element(page, server):
    # #146: a LiveComponent's remove, asked for by the instance its parent's
    # next join replaced, took the new element's child away: like its render,
    # it is named by the child's id, which no join is.
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")
    page.wait_for_function("window.__inbox.seen.some((m) => m.command === 'render' && 'vsn' in m.payload)")

    page.evaluate("window.__inbox.holding = true")
    by(page, "nest-child-vanish").click()
    _held(page, "m.command === 'remove' && m.payload.id === 'nest-child'")
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    _held(page, "m.command === 'render' && m.payload.id === 'nest'")
    page.evaluate(
        """() => new Promise((done) => {
          window.__inbox.release(window.__inbox.held.findIndex((m) => m.command === 'remove') + 1);
          requestAnimationFrame(() => requestAnimationFrame(done));
        })"""
    )
    expect(page.locator("#nest-child")).to_have_count(1)
    page.evaluate("window.__inbox.release()")

    by(page, "nest-child-bump").click()
    expect_text(by(page, "nest-child-count"), "1")


def test_a_live_component_that_destroys_itself_leaves_the_page(page, server):
    # #146: a LiveComponent's remove waits with its root's join; once that is
    # answered, the instance's own remove is taken
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    by(page, "nest-child-vanish").click()
    expect(page.locator("#nest-child")).to_have_count(0)


@pytest.fixture
def next_nest_child_destroys_in_joined(monkeypatch):
    """Arms the child's next ``joined()`` to destroy it; ``.joined`` is set by each one.

    The child's ``joined()`` runs after the render that shows it, through the
    session's mailbox -- on NATS, well after the page reads as live. A test arms
    it only once the first page's child has joined, or that one is destroyed.
    """
    from testproj.errorprobe.live import ErrorNestChild

    armed = threading.Event()
    armed.joined = threading.Event()

    async def joined(self):
        if self.wire.channel_name and armed.is_set():
            armed.clear()
            await self.destroy()
        armed.joined.set()

    monkeypatch.setattr(ErrorNestChild, "joined", joined)
    return armed


def test_a_live_component_that_destroys_itself_in_its_parents_join_leaves_the_page(
    next_nest_child_destroys_in_joined, page, server
):
    # #146: the remove comes while the page waits for the parent's named join,
    # after the render that answers it, so it is the new instance's and is taken
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")
    assert next_nest_child_destroys_in_joined.joined.wait(15), "the first page's child never joined"
    next_nest_child_destroys_in_joined.set()
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    expect(page.locator("#nest-child")).to_have_count(0)


def test_an_id_that_was_a_live_component_joins_as_the_root_it_now_is(page, server):
    # #146: the page joined the root under the id before it let the old parent
    # go, so the server still held the LiveComponent under it and ignored the
    # join. The root never went live.
    sent = []
    page.on("websocket", lambda ws: ws.on("framesent", lambda frame: sent.append(json.loads(frame))))
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")
    by(page, "nest-child-bump").click()
    expect_text(by(page, "nest-child-count"), "1")

    by(page, "swap").click()
    expect_text(by(page, "visit"), "swap")
    expect_text(by(page, "nest-child-count"), "0")
    by(page, "nest-child-bump").click()
    expect_text(by(page, "nest-child-count"), "1")

    # The root leaves as a root, not as the LiveComponent the page once knew
    # under the id; else the server kept it and the id could not be a
    # LiveComponent again
    by(page, "bare").click()
    expect_text(by(page, "visit"), "bare")
    # A round trip after it, so the frames the page sent before are all seen
    by(page, "late-bump").click()
    expect_text(by(page, "late-count"), "1")
    assert {"command": "leave", "payload": {"id": "nest-child"}} in sent
    by(page, "second").click()
    expect_text(by(page, "visit"), "second")
    expect_text(by(page, "nest-child-count"), "0")
    by(page, "nest-child-bump").click()
    expect_text(by(page, "nest-child-count"), "1")
