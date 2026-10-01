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
import re
import threading

import pytest
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, OFFLINE_SHIM, expect_text, open_live
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
def heard():
    """What the errorprobe handlers heard on the server, from this test on."""
    from testproj.errorprobe.live import HEARD

    HEARD.clear()
    yield HEARD
    HEARD.clear()


LOADING = re.compile("wireview-click-loading")


def test_a_component_whose_join_failed_gets_nothing_when_its_parent_draws_it_again(probe, heard):
    # The holder's render draws ``held`` again, marked live and without the error
    # class, over a new instance its template pass built and nothing joined. The
    # page took that one up, and a click or a hook's push reached an instance
    # whose joined() never ran. The server now refuses them, answering the click
    # so its loading state ends; its HTML marks the element, which stays marked.
    sent = []
    probe.sockets[0].on("framesent", lambda frame: sent.append(json.loads(frame)))
    held = probe.locator("#held")
    expect(held).to_have_class("wireview-error")

    by(probe, "holder-bump").click()
    expect_text(by(probe, "holder-count"), "1")
    expect(held).to_have_attribute("wire-join-failed", "")
    by(probe, "held-poke").click()
    probe.evaluate("window.__askers.held.pushEvent('poke', {})")
    # Answered after anything the poke or the push sent
    by(probe, "holder-bump").click()
    expect_text(by(probe, "holder-count"), "2")

    expect(held).to_have_class(re.compile("wireview-error"))
    expect(by(probe, "held-poke")).not_to_have_class(LOADING)
    expect_text(by(probe, "held-pokes"), "0")
    assert heard == []
    # The parent's patches draw it over the element the page already took up,
    # which it does not join again: no loop
    assert [m for m in sent if m["command"] == "join"] == []
    assert _failures(probe, "held") == 1, "not joined again"


def test_a_component_whose_join_failed_joins_again_on_the_next_connection(page, server):
    page.add_init_script(
        OFFLINE_SHIM + "window.__wireviewErrors = [];"
        "document.addEventListener('wireview:error', (e) => window.__wireviewErrors.push(e.detail));"
    )
    open_live(page, f"{server}/errorprobe/", selector="#box[data-is-live='true']")
    page.wait_for_function("window.__wireviewErrors.filter((e) => e.id === 'held').length === 1")
    by(page, "holder-bump").click()
    expect_text(by(page, "holder-count"), "1")

    page.evaluate("window.__link.sockets.forEach((s) => s.close())")
    # It fails again, but it was tried
    page.wait_for_function("window.__wireviewErrors.filter((e) => e.id === 'held').length === 2")
    expect(page.locator("#held")).to_have_class("wireview-error")


def _failures(page, id: str) -> int:
    return page.evaluate(f"window.__wireviewErrors.filter((e) => e.id === '{id}' && e.during === 'join').length")


def test_the_live_components_a_failed_component_owns_get_nothing_either(probe, heard):
    # The nest's join failed, and the instance it made of ``held-child`` went
    # with it: a click went to an id the server no longer held, and once the
    # holder's pass built new instances of both, the child's button and hook
    # reached a LiveComponent of an instance whose joined() never ran. The
    # server refuses both, before the holder's pass and after it.
    probe.wait_for_function("window.__wireviewErrors.some((e) => e.id === 'held-nest')")
    expect(probe.locator("#held-nest")).to_have_class("wireview-error")

    for count in ("1", "2"):
        by(probe, "held-child-poke").click()
        probe.evaluate("window.__askers['held-child'].pushEvent('poke', {})")
        # Answered after anything the poke or the push sent; the second time,
        # the holder's pass has built instances of both
        by(probe, "holder-bump").click()
        expect_text(by(probe, "holder-count"), count)

    expect(probe.locator("#held-nest")).to_have_class(re.compile("wireview-error"))
    # What the page had for it stays, and its click was answered
    expect(by(probe, "held-child-poke")).to_have_count(1)
    expect(by(probe, "held-child-poke")).not_to_have_class(LOADING)
    assert heard == []
    assert _failures(probe, "held-nest") == 1, "not joined again"


def test_what_a_failed_join_owns_gets_nothing_before_the_next_patch_either(page, server, heard):
    # The click can come right after the error, and right after the holder's
    # render that brings the child's new instance, before that render's patch:
    # the server refuses it whichever instance the id names then.
    page.add_init_script(INBOX_SHIM + "window.__inbox.holding = true;")
    click = "document.querySelector('[data-testid=held-child-poke]').click()"
    open_live(page, f"{server}/errorprobe/", selector="#box[data-is-live='true']")
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'error' && m.payload.id === 'held-nest')")
    page.evaluate(f"window.__inbox.release(); {click}")
    expect(page.locator("#held-nest")).to_have_class("wireview-error")

    page.evaluate("window.__inbox.holding = true")
    by(page, "holder-bump").click()
    page.wait_for_function("window.__inbox.held.some((m) => m.command === 'render' && m.payload.id === 'holder')")
    page.evaluate(f"window.__inbox.release(); {click}")
    expect_text(by(page, "holder-count"), "1")
    # Answered after anything the clicks sent
    by(page, "holder-bump").click()
    expect_text(by(page, "holder-count"), "2")

    assert heard == []
    # Both clicks were answered, the first for an id that named nothing yet
    expect(by(page, "held-child-poke")).not_to_have_class(LOADING)


def test_a_boosted_navigation_tries_a_failed_join_again(probe, heard):
    # The new page's HTML is the server's over HTTP: the page joins its
    # components again, and the server takes such a join as a new try. When
    # the page remembered the failure itself, it never joined them: the
    # component stayed dead on a page where it may well join.
    probe.wait_for_function("window.__wireviewErrors.filter((e) => e.id === 'held-nest').length === 1")
    assert _failures(probe, "held") == 1

    probe.evaluate("window.wireview.visit('/errorprobe/')")
    probe.wait_for_function(
        "['held', 'held-nest'].every((id) => window.__wireviewErrors.filter((e) => e.id === id).length === 2)"
    )

    # And on the new page, what the failure keeps out gets nothing
    by(probe, "holder-bump").click()
    expect_text(by(probe, "holder-count"), "1")
    by(probe, "held-poke").click()
    by(probe, "held-child-poke").click()
    probe.evaluate("window.__askers.held.pushEvent('poke', {})")
    probe.evaluate("window.__askers['held-child'].pushEvent('poke', {})")
    by(probe, "holder-bump").click()
    expect_text(by(probe, "holder-count"), "2")

    expect(probe.locator("#held")).to_have_class(re.compile("wireview-error"))
    expect(probe.locator("#held-nest")).to_have_class(re.compile("wireview-error"))
    assert heard == []
    assert len(probe.sockets) == 1


def test_a_boosted_navigation_joins_what_failed_once_it_can(probe, monkeypatch):
    # The cause is gone by the time the page moves: the new page's components
    # join and work, LiveComponents and all.
    from testproj.errorprobe.live import ErrorJoin, ErrorJoinNest

    probe.wait_for_function("window.__wireviewErrors.filter((e) => e.id === 'held-nest').length === 1")

    async def joined(self):
        pass

    monkeypatch.setattr(ErrorJoin, "joined", joined)
    monkeypatch.setattr(ErrorJoinNest, "joined", joined)
    probe.evaluate("window.wireview.visit('/errorprobe/')")
    expect(probe.locator("#held")).not_to_have_class(re.compile("wireview-error"))
    expect(probe.locator("#held-nest")).not_to_have_class(re.compile("wireview-error"))

    by(probe, "held-poke").click()
    expect_text(by(probe, "held-pokes"), "1")
    by(probe, "held-child-poke").click()
    expect_text(by(probe, "held-child-pokes"), "1")
    assert _failures(probe, "held") == 1
    assert len(probe.sockets) == 1


@pytest.fixture
def slot_page(page, server):
    """The ``slot/`` page, once the joins of both its nests have failed."""
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws))
    page.add_init_script(
        "window.__wireviewErrors = [];"
        "document.addEventListener('wireview:error', (e) => window.__wireviewErrors.push(e.detail));"
    )
    open_live(page, f"{server}/errorprobe/slot/", selector="#host[data-is-live='true']")
    page.wait_for_function("['slot-nest', 'own-nest'].every((id) => window.__wireviewErrors.some((e) => e.id === id))")
    page.sockets = sockets
    return page


def test_a_live_component_in_the_slot_of_a_failed_join_is_its_callers(slot_page, heard):
    # A slot renders in its caller's template pass: ``slot-leaf`` is the
    # host's, which is alive, and the nest's failed join took nothing of it.
    # The page, guessing the owner from the nest around it, kept it out -- its
    # clicks went nowhere -- until a render of the host took it up again. The
    # nest's own LiveComponent did go with the nest, and the server refuses it.
    page = slot_page
    expect(page.locator("#slot-leaf")).to_have_attribute("data-parent", "host")

    for count in ("1", "2"):
        by(page, "slot-leaf-poke").click()
        by(page, "own-leaf-poke").click()
        # Answered after anything the pokes sent; the second time, after the
        # host's render carried the leaf's
        by(page, "host-bump").click()
        expect_text(by(page, "host-count"), count)
        expect_text(by(page, "slot-leaf-pokes"), count)

    expect_text(by(page, "own-leaf-pokes"), "0")
    assert heard == [("slot-leaf", "poke"), ("slot-leaf", "poke")]
    expect(page.locator("#slot-nest")).to_have_class("wireview-error")
    expect(page.locator("#own-nest")).to_have_class("wireview-error")


def test_the_hooks_of_a_failed_join_are_destroyed_when_it_leaves(slot_page):
    # The page let go of a component whose join failed, and of its
    # LiveComponents, and so never told their hooks the elements had left:
    # a listener or a timer a hook set up outlived its element. It keeps them
    # registered now; the server is the one that refuses them.
    page = slot_page
    html = page.locator("html")
    hooked = ["slot-nest", "own-nest", "slot-leaf", "own-leaf"]
    for who in hooked:
        expect(html).to_have_attribute(f"data-mounted-{who}", "1")

    by(page, "host-toggle").click()
    expect(page.locator("#slot-nest")).to_have_count(0)
    expect(page.locator("#own-nest")).to_have_count(0)
    for who in hooked:
        expect(html).to_have_attribute(f"data-destroyed-{who}", "1")


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


def test_a_field_a_remove_takes_away_does_not_send_its_blur(page, server):
    # A focused element the page removes is blurred while it is still in the
    # document. The remove is the server's, so that blur is nobody's: sent, it
    # named a component the server had just let go.
    sent = []
    page.on("websocket", lambda ws: ws.on("framesent", lambda frame: sent.append(json.loads(frame))))
    open_live(page, f"{server}/errorprobe/late/", selector="#nest[data-is-live='true']")

    bumps = iter(range(1, 10))

    def blurs():
        # A round trip first, so the frames the page sent before it are all seen
        by(page, "late-bump").click()
        expect_text(by(page, "late-count"), str(next(bumps)))
        return [m for m in sent if m["command"] == "user_event" and m["payload"]["command"] == "blurred"]

    by(page, "nest-child-bump").click()  # the child is live: its bindings are sent
    expect_text(by(page, "nest-child-count"), "1")
    field = by(page, "nest-child-field")
    field.focus()
    by(page, "late-draft").focus()  # the user leaves the field: that blur is theirs
    assert len(blurs()) == 1

    field.focus()
    field.press("Enter")
    expect(page.locator("#nest-child")).to_have_count(0)

    assert len(blurs()) == 1


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


def test_the_hooks_of_a_failed_join_hear_destroyed_with_no_patch_in_between(page, server):
    # The page used to let go of a component whose join failed, and only the
    # next patch's pass over the page took it up again: one that left with the
    # very next patch was never told. Here the host's toggle comes right after
    # the errors, before anything else is patched.
    page.add_init_script(INBOX_SHIM + "window.__inbox.holding = true;")
    open_live(page, f"{server}/errorprobe/slot/", selector="#host[data-is-live='true']")
    page.wait_for_function("window.__inbox.held.filter((m) => m.command === 'error').length === 2")
    # The host's answer, patched, and then the errors and the toggle at once
    page.evaluate("window.__inbox.release(window.__inbox.held.findIndex((m) => m.command === 'error'))")
    page.evaluate("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    page.evaluate("window.__inbox.release(); document.querySelector('[data-testid=host-toggle]').click()")

    expect(page.locator("#own-nest")).to_have_count(0)
    html = page.locator("html")
    for who in ["slot-nest", "own-nest", "slot-leaf", "own-leaf"]:
        expect(html).to_have_attribute(f"data-destroyed-{who}", "1")
