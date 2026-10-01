"""LiveComponents in slots stay on the page when the slot's owner renders on its own.

The server-side contract is in tests/test_live_component_slots.py. Here the page
applies it: the frame (``{% component_block %}``) renders its slot on its own
join and on its events, the box (``{% live_component_block %}``) on its events,
and both slots hold a LiveComponent the host owns. Before 1.0 their own render
carried the LiveComponent as a bare comment and it left the page.

Fixture: tests/testproj/slotprobe/.
"""

import pytest
from testproj.e2e_browser import INBOX_SHIM, expect_count, expect_text, open_live
from testproj.e2e_server import serve

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="function")
def server():
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _db(transactional_db):
    pass


def by(page, testid):
    return page.get_by_test_id(testid)


def after_render_of(page, component_id: str) -> None:
    """Wait until the page was handed a render of ``component_id``, and the frame that patches it."""
    page.wait_for_function(
        f"window.__inbox.seen.some((m) => m.command === 'render' && m.payload.id === '{component_id}')"
    )
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")


@pytest.fixture
def probe(page, server):
    page.add_init_script(INBOX_SHIM)
    open_live(page, f"{server}/slotprobe/", selector="#host[data-is-live='true']")
    after_render_of(page, "host")
    after_render_of(page, "frame")
    return page


def test_the_frames_join_keeps_the_live_component_of_its_slot(probe):
    expect_count(probe.locator("#leaf"), 1)
    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 0")

    by(probe, "leaf-poke").click()

    expect_text(by(probe, "leaf-pokes"), "1")


def test_the_frames_event_keeps_the_live_component_of_its_slot(probe):
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "1")

    by(probe, "frame-click").click()
    expect_text(by(probe, "frame-clicks"), "1")

    expect_count(probe.locator("#leaf"), 1)
    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 0")
    expect_text(by(probe, "leaf-pokes"), "1")
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "2")


def test_a_live_component_keeps_the_live_component_of_its_slot(probe):
    expect_count(probe.locator("#boxleaf"), 1)
    by(probe, "boxleaf-poke").click()
    expect_text(by(probe, "boxleaf-pokes"), "1")

    by(probe, "box-click").click()
    expect_text(by(probe, "box-clicks"), "1")

    expect_count(probe.locator("#boxleaf"), 1)
    expect_text(by(probe, "box-slot"), "BOX-SLOT 0")
    by(probe, "boxleaf-poke").click()
    expect_text(by(probe, "boxleaf-pokes"), "2")


def test_the_host_refills_both_slots_and_the_live_components_keep_their_state(probe):
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "1")
    by(probe, "boxleaf-poke").click()
    expect_text(by(probe, "boxleaf-pokes"), "1")

    by(probe, "host-bump").click()
    expect_text(by(probe, "host-n"), "1")

    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 1")
    expect_text(by(probe, "box-slot"), "BOX-SLOT 1")
    expect_text(by(probe, "leaf-pokes"), "1")
    expect_text(by(probe, "boxleaf-pokes"), "1")

    # The frame renders on its own again, from the slot the host just filled
    by(probe, "frame-click").click()
    expect_text(by(probe, "frame-clicks"), "1")
    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 1")
    expect_count(probe.locator("#leaf"), 1)
