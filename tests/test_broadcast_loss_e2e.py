"""Broadcasts past the channel layer's capacity (#168).

Delivery is at-most-once on every layer. A connection that falls behind -- its
component still handling one notification while more arrive -- has a queue of
``capacity`` messages, and the layer drops what does not fit. What is checked
here, in a browser, on whichever layer the run uses:

- Nothing raises. The publisher's handler finishes, the receiver's connection
  stays up and keeps working, and the server logs no error.
- The receiver's screen is left short. A component that adds up what it hears
  (``received``) never gets the dropped ones back. A value the template reads
  from the source of truth (``published``) is right again on the next render
  that reaches the page.
- Which messages go depends on the layer (``HEARD``):

  * InMemory and channels-nats keep the oldest and drop the newer ones;
    channels-nats logs a WARNING on the receiving process, InMemory nothing.
  * channels_redis keeps the newest. Its process reads every connection's
    messages into a buffer per connection while any connection of the process is
    reading -- here the sender's -- and a full buffer drops its oldest message
    without a log. Only when no connection of the process reads do messages pile
    up in Redis, where ``group_send`` drops the new ones and logs INFO
    ("channels over capacity in group").

The receiver is held in its first notification (``GATE``) until the burst is
out, so the queue is full when the test looks, however fast the machine.

Fixture: tests/testproj/lossprobe/.
"""

from __future__ import annotations

import copy
import logging
import os

import pytest
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live
from testproj.e2e_server import serve, server_errors
from testproj.lossprobe.live import GATE, HEARD, PUBLISHED

pytestmark = pytest.mark.e2e

CAPACITY = 5
BURST = 30
LAYER = os.environ.get("WIREVIEW_TEST_LAYER", "memory")


@pytest.fixture
def small_capacity(settings):
    """Every queue holds CAPACITY messages. Channels drops its cached layer on the change."""
    layers = copy.deepcopy(settings.CHANNEL_LAYERS)
    layers["default"]["CONFIG"] = {**layers["default"].get("CONFIG", {}), "capacity": CAPACITY}
    settings.CHANNEL_LAYERS = layers


@pytest.fixture
def server(small_capacity):
    with serve() as base_url:
        yield base_url


@pytest.fixture(autouse=True)
def _probe(transactional_db):
    PUBLISHED["n"] = 0
    HEARD.clear()
    GATE.clear()
    yield
    # A test that failed with the gate shut must not leave the next one's server waiting
    GATE.set()


def by(page, testid: str):
    return page.get_by_test_id(testid)


def wait_for_number(page, testid: str, at_least: int) -> None:
    page.wait_for_function(
        "([testid, n]) => Number(document.querySelector(`[data-testid=${testid}]`).textContent) >= n",
        arg=[testid, at_least],
        timeout=WAIT_TIMEOUT * 1000,
    )


def test_a_burst_past_the_capacity_is_dropped_quietly_and_the_next_render_shows_the_truth(
    page, browser, server, caplog
):
    caplog.set_level(logging.INFO)
    open_live(page, f"{server}/lossprobe/")
    sender_context = browser.new_context()
    try:
        sender = sender_context.new_page()
        open_live(sender, f"{server}/lossprobe/send/")

        by(sender, "burst").click()
        # The publisher's handler ran to the end: nothing told it a message was dropped
        expect_text(by(sender, "sent"), str(BURST))

        GATE.set()
        # One of the queued messages handled: there is room again for the next one
        wait_for_number(page, "received", 2)
        by(sender, "one").click()
        expect_text(by(sender, "sent"), str(BURST + 1))
        # The last one published arrived, so everything queued before it has too
        expect_text(by(page, "last"), str(BURST + 1))
    finally:
        sender_context.close()

    heard = list(HEARD)
    assert heard[-1] == BURST + 1
    assert len(heard) < BURST + 1, f"the layer dropped what did not fit: heard {heard}"
    # Lost for good in what the component added up; right in what it reads at render
    expect_text(by(page, "received"), str(len(heard)))
    expect_text(by(page, "published"), str(BURST + 1))
    assert not server_errors()

    burst = heard[:-1]
    logged = [record for record in caplog.records if record.name.startswith(("channels_nats", "channels_redis"))]
    if LAYER == "redis":
        assert BURST in burst, f"channels_redis kept the newest: {burst}"
        assert 2 not in burst, f"and dropped the oldest: {burst}"
        assert not logged, [record.getMessage() for record in logged]
    else:
        assert burst == list(range(1, len(burst) + 1)), f"{LAYER} kept the oldest: {burst}"
    if LAYER == "nats":
        assert any(record.levelno == logging.WARNING and "is full" in record.getMessage() for record in logged)

    # The receiver's connection is still up, and any render it gets reads the truth again
    PUBLISHED["n"] += 1
    by(page, "refresh").click()
    expect_text(by(page, "published"), str(BURST + 2))
    expect_text(by(page, "received"), str(len(heard)))
