"""E2E tests for LiveComponent functionality."""

import asyncio
import threading
import time
from random import randint
from time import sleep

import pytest
from channels.routing import get_default_application
from django.test import Client, TestCase, override_settings
from uvicorn.config import Config as UvicornConfig
from uvicorn.main import Server as Uvicorn


class TestLiveComponentRendering(TestCase):
    """Test static rendering of LiveComponent."""

    def test_dashboard_renders_structure(self):
        """Test that dashboard page renders basic structure."""
        c = Client()
        response = c.get("/livecomp/")
        assert response.status_code == 200
        # Dashboard should be rendered
        self.assertContains(response, "LiveComponent Dashboard")
        self.assertContains(response, 'id="main-dashboard"')
        self.assertContains(response, 'data-name="Dashboard"')
        # Note: counters are empty on static render (joined() called after WebSocket)
        # LiveComponents are created dynamically after WebSocket connection
        self.assertContains(response, 'data-testid="counters-grid"')


class UvicornThread(threading.Thread):
    """Thread that runs Uvicorn ASGI server for testing."""

    def __init__(self, application, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.application = application
        self.server: Uvicorn | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    @override_settings(DEBUG=True)
    def run(self):
        self.loop = asyncio.new_event_loop()
        config = UvicornConfig(self.application, host=self.host, port=self.port, log_level="warning")
        self.server = Uvicorn(config)
        self.server.install_signal_handlers = lambda *args, **kwargs: None
        self.loop.run_until_complete(self.server.serve())
        self.server = None

    @property
    def started(self) -> bool:
        return self.server is not None and self.server.started

    def terminate(self):
        if self.server:
            self.server.force_exit = True
            self.server.should_exit = True
            if self.loop:
                self.loop.create_task(self.server.shutdown())


@pytest.fixture(scope="function")
def livecomp_server():
    """Fixture that starts a live ASGI server for E2E tests."""
    host = "127.0.0.1"
    port = randint(9000, 40000)
    server = UvicornThread(get_default_application(), host, port)
    server.start()

    # Wait for server to start
    while not server.started:
        sleep(0.1)

    yield f"http://{host}:{port}"

    server.terminate()


def expect_text_eventually(locator, expected: str, timeout: float = 5.0):
    """Wait for locator to have expected text content."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        actual = locator.inner_text().strip()
        if actual == expected:
            return True
        time.sleep(0.1)
    actual = locator.inner_text().strip()
    assert actual == expected, f"Expected '{expected}', got '{actual}'"


def wait_for_websocket(page, timeout: float = 5.0):
    """Wait for WebSocket connection to be established."""
    page.wait_for_selector('[data-is-live="true"]', timeout=timeout * 1000)


@pytest.mark.e2e
class TestLiveComponentE2E:
    """E2E tests for LiveComponent using Playwright."""

    @pytest.fixture(autouse=True)
    def setup_db(self, transactional_db):
        """Ensure database is available for tests."""
        pass

    def test_dashboard_loads_with_counters(self, page, livecomp_server):
        """Test that dashboard loads and displays all counters."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Check all three counters are visible
        counters = page.locator(".counter-widget")
        assert counters.count() == 3

        # Check initial values
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "0")
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")
        expect_text_eventually(page.locator('[data-testid="count-counter-3"]'), "5")

        # Check total
        expect_text_eventually(page.locator('[data-testid="total"]'), "15")

    def test_increment_counter_with_myself_targeting(self, page, livecomp_server):
        """Test clicking increment button targets the correct LiveComponent."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Initial value
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "0")

        # Click increment on counter-1
        page.locator('[data-testid="increment-counter-1"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "1")

        # Counter-2 should still be 10
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")

        # Click increment again
        page.locator('[data-testid="increment-counter-1"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "2")

    def test_decrement_counter(self, page, livecomp_server):
        """Test decrement button works."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Counter-2 starts at 10
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")

        # Click decrement
        page.locator('[data-testid="decrement-counter-2"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "9")

    def test_reset_individual_counter(self, page, livecomp_server):
        """Test reset button on individual counter."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Counter-2 starts at 10
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")

        # Click reset
        page.locator('[data-testid="reset-counter-2"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "0")

    def test_send_to_parent_updates_dashboard(self, page, livecomp_server):
        """Test that LiveComponent events update parent dashboard."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Initial total should be 15 (0 + 10 + 5)
        expect_text_eventually(page.locator('[data-testid="total"]'), "15")

        # Increment counter-1
        page.locator('[data-testid="increment-counter-1"]').click()

        # Wait for parent to be notified and update
        expect_text_eventually(page.locator('[data-testid="last-changed"]'), "counter-1: 1")

        # Total should now be 16
        expect_text_eventually(page.locator('[data-testid="total"]'), "16")

    def test_parent_reset_all_updates_children(self, page, livecomp_server):
        """Test that parent can reset all LiveComponents."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Verify initial values
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")
        expect_text_eventually(page.locator('[data-testid="count-counter-3"]'), "5")

        # Click Reset All
        page.locator('[data-testid="reset-all"]').click()

        # All counters should be 0
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "0")
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "0")
        expect_text_eventually(page.locator('[data-testid="count-counter-3"]'), "0")

        # Total should be 0
        expect_text_eventually(page.locator('[data-testid="total"]'), "0")

        # Last changed should indicate all reset
        expect_text_eventually(page.locator('[data-testid="last-changed"]'), "All reset")

    def test_parent_sync_all_updates_children(self, page, livecomp_server):
        """Test that parent can sync all LiveComponents to same value."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Click Sync All to 5
        page.locator('[data-testid="sync-all"]').click()

        # All counters should be 5
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "5")
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "5")
        expect_text_eventually(page.locator('[data-testid="count-counter-3"]'), "5")

        # Total should be 15 (5 * 3)
        expect_text_eventually(page.locator('[data-testid="total"]'), "15")

    def test_independent_counter_state(self, page, livecomp_server):
        """Test that each counter maintains independent state."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Increment counter-1 twice
        page.locator('[data-testid="increment-counter-1"]').click()
        page.locator('[data-testid="increment-counter-1"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "2")

        # Decrement counter-2 once
        page.locator('[data-testid="decrement-counter-2"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "9")

        # Counter-3 should remain unchanged
        expect_text_eventually(page.locator('[data-testid="count-counter-3"]'), "5")

        # Final total: 2 + 9 + 5 = 16
        expect_text_eventually(page.locator('[data-testid="total"]'), "16")

    def test_multiple_rapid_clicks(self, page, livecomp_server):
        """Test that rapid clicks are handled correctly."""
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)

        # Rapid clicks on increment
        for _ in range(5):
            page.locator('[data-testid="increment-counter-1"]').click()

        # Wait a moment for all events to process
        page.wait_for_timeout(500)

        # Should show 5
        expect_text_eventually(page.locator('[data-testid="count-counter-1"]'), "5")
