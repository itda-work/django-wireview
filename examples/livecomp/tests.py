"""E2E tests for LiveComponent functionality."""

import time

import pytest
from django.test import Client, TestCase
from testproj.e2e_server import serve


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
        # The HTTP response is a dead render: children are inlined without joined()
        self.assertContains(response, 'data-testid="counters-grid"')
        self.assertContains(response, 'data-testid="count-counter-2"')


@pytest.fixture(scope="function")
def livecomp_server():
    """Fixture that starts a live ASGI server for E2E tests."""
    with serve() as base_url:
        yield base_url


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

    def test_a_reset_counter_survives_an_unrelated_parent_rerender(self, page, livecomp_server):
        """A child's own state is not overwritten when the parent re-renders for another reason.

        Counter.reset() changes the child without telling the parent. The parent still
        passes count=10 for counter-2 on its next render, and that must not undo the reset.
        """
        page.goto(f"{livecomp_server}/livecomp/")
        wait_for_websocket(page)
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "10")

        page.locator('[data-testid="reset-counter-2"]').click()
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "0")

        # An event on another counter makes the parent re-render (send_to_parent)
        page.locator('[data-testid="increment-counter-1"]').click()
        expect_text_eventually(page.locator('[data-testid="last-changed"]'), "counter-1: 1")

        page.wait_for_timeout(300)
        expect_text_eventually(page.locator('[data-testid="count-counter-2"]'), "0")

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
