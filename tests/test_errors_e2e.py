"""A handler that raises, seen from the browser (#94).

It used to close the socket: the page flashed ``wireview-disconnected``,
reconnected and joined every component again. Now only the component that
raised joins again, from the state its element carries, so what the handler
changed before raising is gone, the button it disabled comes back, and the
socket is the one the page opened. A component that cannot join keeps what the
page rendered and is marked ``wireview-error``.

Fixture: tests/testproj/errorprobe/.
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
