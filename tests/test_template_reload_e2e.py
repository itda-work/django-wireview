"""A template saved under the dev server reaches the open page without a reload (#180).

The browser's side of tests/test_template_reload.py: the autoreloader's
``file_changed`` (sent here by the test, as the reloader would from its thread)
makes the page join its components again. A component that was fine shows the
new file at once with its state; one whose join failed on a broken file comes
back once the file is fixed -- on the socket the page opened, with the count
it had before it broke. A click still being handled when the file changes is
not undone by the joins: they wait for its answer.

Fixture: tests/testproj/reloadprobe/.
"""

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils.autoreload import file_changed
from playwright.sync_api import expect
from testproj.e2e_browser import INBOX_SHIM, expect_text, open_live
from testproj.e2e_server import serve, server_errors
from testproj.reloadprobe.live import ENTERED, RELEASE
from testproj.reloadprobe.shadow import shadowed

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def shadow(tmp_path):
    with shadowed(tmp_path) as shadow:
        yield shadow


@pytest.fixture(scope="function")
def server(shadow):
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


@pytest.fixture(autouse=True)
def _gate():
    ENTERED.clear()
    RELEASE.clear()
    yield
    # A handler left waiting would hold the server's shutdown
    RELEASE.set()


@pytest.fixture
def probe(page, server):
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws))
    page.add_init_script(
        "window.__wireviewErrors = [];"
        "document.addEventListener('wireview:error', (e) => window.__wireviewErrors.push(e.detail));"
    )
    open_live(page, f"{server}/reloadprobe/", selector="#other[data-is-live='true']")
    page.sockets = sockets
    return page


def box(page, testid: str):
    return page.locator("#box").get_by_test_id(testid)


def saved(path) -> None:
    file_changed.send(sender=None, file_path=path)


def test_a_saved_template_shows_at_once_with_the_state_kept(probe, shadow):
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "1")

    saved(shadow.write("v2"))

    # No event: the page joined again and the join rendered the new file
    expect_text(box(probe, "box-version"), "v2")
    expect_text(probe.locator("#other").get_by_test_id("box-version"), "v2")
    expect_text(box(probe, "box-count"), "1")
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "2")
    assert len(probe.sockets) == 1
    # Not a server error, and none was reported as one
    assert probe.evaluate("window.__wireviewErrors") == []


def test_a_component_broken_by_a_template_comes_back_once_it_is_fixed(probe, shadow):
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "1")

    saved(shadow.break_it())
    expect(probe.locator("#box")).to_have_class("wireview-error")
    # Refused on this connection: the click runs nothing
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "1")

    saved(shadow.write("v3"))

    expect_text(box(probe, "box-version"), "v3")
    expect(probe.locator("#box")).not_to_have_class("wireview-error")
    expect_text(box(probe, "box-count"), "1")
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "2")
    assert len(probe.sockets) == 1


def test_the_issues_path_an_event_that_fails_to_render_then_the_fix(probe, shadow):
    """#180 as reported: the break reaches the page through an event, not through a rejoin.

    With the rejoin off, saving the broken file only empties the loaders. The
    next click's render raises, the page joins again from the state before
    the click, that join fails, and the component is refused on the socket.
    Then the fix is saved with it on.
    """
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "1")

    with override_settings(WIREVIEW={**settings.WIREVIEW, "REJOIN_ON_TEMPLATE_CHANGE": False}):
        saved(shadow.break_it())
    box(probe, "box-bump").click()
    probe.wait_for_function("window.__wireviewErrors.some((e) => e.id === 'box' && e.during === 'join')")
    expect(probe.locator("#box")).to_have_class("wireview-error")

    saved(shadow.write("v3"))

    expect_text(box(probe, "box-version"), "v3")
    expect(probe.locator("#box")).not_to_have_class("wireview-error")
    # The click whose render failed was rolled back, as any handler that raised
    expect_text(box(probe, "box-count"), "1")
    box(probe, "box-bump").click()
    expect_text(box(probe, "box-count"), "2")
    assert len(probe.sockets) == 1


def test_a_click_still_being_handled_is_not_undone_by_the_rejoin(probe, shadow):
    box(probe, "box-slow").click()
    assert ENTERED.wait(5), "slow_bump never ran"

    saved(shadow.write("v2"))
    # Sent while the server is still busy with the first; answered in order
    box(probe, "box-bump").click()
    RELEASE.set()

    expect_text(box(probe, "box-version"), "v2")
    expect_text(box(probe, "box-count"), "2")
    expect(probe.locator("#box")).not_to_have_class("wireview-error")
    assert len(probe.sockets) == 1
    assert probe.evaluate("window.__wireviewErrors") == []


#: Records the frames the page sends, and once the page has asked ``sync``, has
#: a DOM callback send an event the next time the box's element is morphed --
#: the morph ``rejoin()`` runs to land a render before it reads the state.
ARM_CALLBACK = """() => {
    window.__frames = [];
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) {
        const message = JSON.parse(data);
        window.__frames.push([message.command, message.payload.id ?? message.payload.name ?? null]);
        if (message.command === "sync") window.__armed = true;
        return send.call(this, data);
    };
    wireview.dom.onBeforeElUpdated((from) => {
        if (window.__armed && from.id === "box") {
            window.__armed = false;
            wireview.send(from, "bump", {});
        }
    });
}"""


def test_what_a_dom_callback_sends_during_the_rejoin_goes_after_its_join(probe, shadow):
    """The second review's case: sent between ``synced`` and the join, the event's render was dropped
    and the join put the count back to 0."""
    probe.evaluate(ARM_CALLBACK)

    saved(shadow.write("v2"))

    expect_text(box(probe, "box-version"), "v2")
    expect_text(box(probe, "box-count"), "1")
    frames = probe.evaluate("window.__frames")
    sent = [command for command, _ in frames]
    assert "user_event" in sent, frames
    # The rejoin's joins first, then the event the callback sent
    assert sent.index("sync") < sent.index("join") < sent.index("user_event"), frames
    assert probe.evaluate("window.__armed") is False
    assert probe.evaluate("window.__wireviewErrors") == []


#: Holds what the server sends from the moment the page asks ``sync``: its
#: ``synced`` waits until the test releases the inbox (INBOX_SHIM).
HOLD_SYNCED = """
(() => {
  window.__frames = [];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    const message = JSON.parse(data);
    window.__frames.push(message.command);
    if (message.command === "sync") window.__inbox.holding = true;
    return send.call(this, data);
  };
})();
"""


@pytest.fixture
def held(page, server):
    page.add_init_script(INBOX_SHIM + HOLD_SYNCED)
    page.add_init_script(
        "window.__wireviewErrors = [];"
        "document.addEventListener('wireview:error', (e) => window.__wireviewErrors.push(e.detail));"
    )
    open_live(page, f"{server}/reloadprobe/", selector="#other[data-is-live='true']")
    return page


def test_a_navigation_while_the_rejoin_waits_joins_its_page_once(held, shadow):
    """A boosted visit's leaves, ``navigated`` and joins are held with the rest, and the rejoin leaves the
    new page's components to their own joins: one join each, after the leaves (#146, #170)."""
    saved(shadow.write("v2"))
    held.wait_for_function("window.__inbox.holding")

    held.get_by_test_id("away-link").click()
    held.wait_for_function("document.getElementById('away') && !document.getElementById('box')")
    held.evaluate("window.__inbox.release()")

    away = held.locator("#away")
    expect_text(away.get_by_test_id("box-version"), "v2")
    away.get_by_test_id("box-bump").click()
    expect_text(away.get_by_test_id("box-count"), "1")
    frames = held.evaluate("window.__frames")
    after = frames[frames.index("sync") + 1 :]
    # No join ahead of the old page's leaves: the rejoin did not join the new page's component itself
    assert after[: after.index("navigated")] == ["leave", "leave"], frames
    assert after[after.index("navigated") :].count("join") == 1, frames
    assert held.evaluate("window.__wireviewErrors") == []
    assert not server_errors()
