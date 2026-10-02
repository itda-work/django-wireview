"""What is in a slot stays on the page when the slot's owner renders on its own.

The server-side contract is in tests/test_live_component_slots.py. Here the page
applies it: the frame (``{% component_block %}``) renders its slot on its own
join and on its events, the box (``{% live_component_block %}``) on its events,
and both slots hold a LiveComponent the host owns. Before 1.0 their own render
carried the LiveComponent as a bare comment and it left the page. The frame's
slot also holds a plain component, which its render put back as the host's pass
had drawn it; the frame can hide its slot and show it again, and joins again
after it raises or the page is visited again, which emptied its slot. Another
page with a frame of the same id and no fill draws its slot empty, and so does
another page whose host draws the frame with no fill: the host's render carried
the old page's slot before the frame's own join emptied it.

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


def renders_of(page, component_id: str) -> int:
    """How many renders of ``component_id`` the page was handed so far."""
    return page.evaluate(
        f"window.__inbox.seen.filter((m) => m.command === 'render' && m.payload.id === '{component_id}').length"
    )


def after_render_of(page, component_id: str, more_than: int = 0) -> None:
    """Wait until the page was handed a render of ``component_id`` past the first ``more_than``, and its patch."""
    page.wait_for_function(
        "window.__inbox.seen.filter((m) => m.command === 'render' && "
        f"m.payload.id === '{component_id}').length > {more_than}"
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


def test_a_plain_component_in_the_frames_slot_keeps_its_state_on_the_frames_render(probe):
    by(probe, "plain-click").click()
    expect_text(by(probe, "plain-clicks"), "1")
    state = probe.locator("#plain").get_attribute("data-state")

    by(probe, "frame-click").click()
    expect_text(by(probe, "frame-clicks"), "1")

    expect_text(by(probe, "plain-clicks"), "1")
    assert probe.locator("#plain").get_attribute("data-state") == state
    by(probe, "plain-click").click()
    expect_text(by(probe, "plain-clicks"), "2")


def test_the_frame_shows_its_slot_again_with_the_live_component_in_it(probe):
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "1")

    by(probe, "frame-toggle").click()
    expect_count(probe.locator("#leaf"), 0)
    by(probe, "frame-toggle").click()

    expect_count(probe.locator("#leaf"), 1)
    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 0")
    expect_text(by(probe, "frame-slot-tail"), "TAIL")
    expect_text(by(probe, "leaf-pokes"), "1")
    # The plain component left with the hidden slot; nothing without a server instance comes back
    expect_count(probe.locator("#plain"), 0)
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "2")


def test_the_frame_joined_again_after_it_raised_keeps_its_slot(probe):
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "1")
    seen = renders_of(probe, "frame")

    by(probe, "frame-boom").click()
    after_render_of(probe, "frame", seen)

    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 0")
    expect_text(by(probe, "frame-slot-tail"), "TAIL")
    expect_count(probe.locator("#leaf"), 1)
    expect_count(probe.locator("#plain"), 1)
    expect_text(by(probe, "leaf-pokes"), "1")
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "2")


def test_a_visit_to_the_same_page_keeps_the_frames_slot(probe):
    seen = renders_of(probe, "frame")

    by(probe, "again").click()
    probe.wait_for_url("**/slotprobe/?again=1")
    after_render_of(probe, "frame", seen)

    expect_text(by(probe, "frame-slot"), "FRAME-SLOT 0")
    expect_text(by(probe, "frame-slot-tail"), "TAIL")
    expect_count(probe.locator("#leaf"), 1)
    by(probe, "leaf-poke").click()
    expect_text(by(probe, "leaf-pokes"), "1")
    by(probe, "plain-click").click()
    expect_text(by(probe, "plain-clicks"), "1")


def test_a_visit_to_another_hosts_page_draws_no_slot_in_the_hosts_render(probe):
    """The other host's pass takes over the frame this page filled; with no fill it has no slot."""
    seen = renders_of(probe, "frame")

    by(probe, "otherhost").click()
    probe.wait_for_url("**/slotprobe/?otherhost=1")
    expect_text(by(probe, "other-host"), "OTHER HOST")
    after_render_of(probe, "other-host")
    after_render_of(probe, "frame", seen)

    host_renders = probe.evaluate(
        "window.__inbox.seen.filter((m) => m.command === 'render' && m.payload.id === 'other-host')"
        ".map((m) => JSON.stringify(m.payload))"
    )
    assert host_renders and not [r for r in host_renders if "FRAME-SLOT" in r or '"c":"leaf"' in r], host_renders
    expect_count(by(probe, "frame-slot"), 0)
    expect_count(probe.locator("#leaf"), 0)


def test_a_visit_to_another_page_with_the_frames_id_draws_no_slot(probe):
    """The frame there has no fill; the slot the host gave this page's frame stays behind."""
    seen = renders_of(probe, "frame")

    by(probe, "other").click()
    probe.wait_for_url("**/slotprobe/?other=1")
    expect_text(by(probe, "other-page"), "OTHER PAGE")
    after_render_of(probe, "frame", seen)

    expect_count(by(probe, "frame-slot"), 0)
    expect_count(by(probe, "frame-slot-tail"), 0)
    expect_count(probe.locator("#leaf"), 0)
    expect_count(probe.locator("#plain"), 0)
    by(probe, "frame-click").click()
    expect_text(by(probe, "frame-clicks"), "1")
    expect_count(by(probe, "frame-slot"), 0)
