"""Tests for the sync/async transition detector."""

import logging
import threading

import pytest


class TestSyncAsyncTracker:
    """Tests for SyncAsyncTracker depth tracking."""

    def setup_method(self):
        """Reset tracker state before each test."""
        # Import here to get fresh state after Django setup
        from wireview.debug.sync_detector import SyncAsyncTracker, _thread_local

        SyncAsyncTracker.reset()
        # Also reset thread local directly to ensure clean state
        _thread_local.sync_depth = 0

    @pytest.mark.unit
    def test_initial_depth_is_zero(self):
        """Test that initial depth is (0, 0)."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        assert SyncAsyncTracker.get_depth() == (0, 0)
        assert SyncAsyncTracker.get_total_depth() == 0

    @pytest.mark.unit
    def test_sync_depth_tracking(self):
        """Test that sync depth is correctly tracked."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        assert SyncAsyncTracker.get_depth() == (0, 0)

        SyncAsyncTracker.enter_sync("test_location")
        assert SyncAsyncTracker.get_depth() == (1, 0)

        SyncAsyncTracker.enter_sync("nested_location")
        assert SyncAsyncTracker.get_depth() == (2, 0)

        SyncAsyncTracker.exit_sync()
        assert SyncAsyncTracker.get_depth() == (1, 0)

        SyncAsyncTracker.exit_sync()
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.unit
    def test_async_depth_tracking(self):
        """Test that async depth is correctly tracked."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        assert SyncAsyncTracker.get_depth() == (0, 0)

        SyncAsyncTracker.enter_async("test_location")
        assert SyncAsyncTracker.get_depth() == (0, 1)

        SyncAsyncTracker.enter_async("nested_location")
        assert SyncAsyncTracker.get_depth() == (0, 2)

        SyncAsyncTracker.exit_async()
        assert SyncAsyncTracker.get_depth() == (0, 1)

        SyncAsyncTracker.exit_async()
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.unit
    def test_mixed_depth_tracking(self):
        """Test tracking when both sync and async depths are used."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.enter_async("async_1")
        assert SyncAsyncTracker.get_depth() == (0, 1)
        assert SyncAsyncTracker.get_total_depth() == 1

        SyncAsyncTracker.enter_sync("sync_1")
        assert SyncAsyncTracker.get_depth() == (1, 1)
        assert SyncAsyncTracker.get_total_depth() == 2

        SyncAsyncTracker.enter_async("async_2")
        assert SyncAsyncTracker.get_depth() == (1, 2)
        assert SyncAsyncTracker.get_total_depth() == 3

        SyncAsyncTracker.exit_async()
        SyncAsyncTracker.exit_sync()
        SyncAsyncTracker.exit_async()
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.unit
    def test_depth_cannot_go_negative(self):
        """Test that exiting without entering doesn't go negative."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.exit_sync()
        SyncAsyncTracker.exit_async()
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.unit
    def test_reset_clears_all_depths(self):
        """Test that reset() clears all depth counters."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.enter_sync("test")
        SyncAsyncTracker.enter_async("test")
        assert SyncAsyncTracker.get_total_depth() == 2

        SyncAsyncTracker.reset()
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.unit
    def test_configure_thresholds(self):
        """Test that thresholds can be configured."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.configure(warning_threshold=5, error_threshold=10)
        assert SyncAsyncTracker.WARNING_THRESHOLD == 5
        assert SyncAsyncTracker.ERROR_THRESHOLD == 10

        # Reset to defaults
        SyncAsyncTracker.configure(warning_threshold=2, error_threshold=3)

    @pytest.mark.unit
    def test_warning_emitted_on_threshold_exceeded(self, caplog):
        """Test that warnings are emitted when threshold exceeded."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.configure(warning_threshold=2, error_threshold=3)

        with caplog.at_level(logging.WARNING, logger="wireview.sync_detector"):
            # Depth 1 - no warning
            SyncAsyncTracker.enter_sync("level_1")
            assert "Nested" not in caplog.text

            # Depth 2 - no warning (at threshold, not above)
            SyncAsyncTracker.enter_sync("level_2")
            assert "Nested" not in caplog.text

            # Depth 3 - warning
            SyncAsyncTracker.enter_sync("level_3")
            assert "Nested sync context detected" in caplog.text
            assert "level_3" in caplog.text

    @pytest.mark.unit
    def test_error_emitted_on_critical_threshold(self, caplog):
        """Test that errors are emitted when critical threshold exceeded."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.configure(warning_threshold=2, error_threshold=3)

        with caplog.at_level(logging.ERROR, logger="wireview.sync_detector"):
            for i in range(5):
                SyncAsyncTracker.enter_sync(f"level_{i}")

            # Should have error at depth > 3
            assert "Nested sync context detected" in caplog.text
            assert "Stack trace" in caplog.text


class TestTrackedWrappers:
    """Tests for tracked wrapper functions."""

    def setup_method(self):
        """Reset tracker state before each test."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.reset()
        SyncAsyncTracker.configure(warning_threshold=2, error_threshold=3)

    @pytest.mark.unit
    def test_tracked_async_to_sync_basic(self):
        """Test that tracked_async_to_sync wraps correctly."""
        from wireview.debug.sync_detector import tracked_async_to_sync

        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            return "result"

        wrapped = tracked_async_to_sync(async_func, location="test_location")
        result = wrapped()

        assert result == "result"
        assert call_count == 1

    @pytest.mark.unit
    def test_tracked_async_to_sync_tracks_depth(self):
        """Test that tracked_async_to_sync properly tracks depth."""
        from wireview.debug.sync_detector import SyncAsyncTracker, tracked_async_to_sync

        depth_during_call = None

        async def async_func():
            nonlocal depth_during_call
            # Note: During the sync wrapper, we're in sync context
            depth_during_call = SyncAsyncTracker.get_depth()
            return "result"

        wrapped = tracked_async_to_sync(async_func, location="test_location")

        # Before call
        assert SyncAsyncTracker.get_depth() == (0, 0)

        # Call (depth is tracked in sync context)
        wrapped()

        # After call - depth should be back to 0
        assert SyncAsyncTracker.get_depth() == (0, 0)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_tracked_sync_to_async_basic(self):
        """Test that tracked_sync_to_async wraps correctly."""
        from wireview.debug.sync_detector import tracked_sync_to_async

        call_count = 0

        def sync_func():
            nonlocal call_count
            call_count += 1
            return "result"

        wrapped = tracked_sync_to_async(sync_func, location="test_location")
        result = await wrapped()

        assert result == "result"
        assert call_count == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_tracked_sync_to_async_as_decorator(self):
        """Test that tracked_sync_to_async works as decorator."""
        from wireview.debug.sync_detector import tracked_sync_to_async

        @tracked_sync_to_async
        def sync_func():
            return "decorated"

        result = await sync_func()
        assert result == "decorated"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_tracked_sync_to_async_with_args(self):
        """Test that tracked_sync_to_async as decorator with args."""
        from wireview.debug.sync_detector import tracked_sync_to_async

        @tracked_sync_to_async(thread_sensitive=False)
        def sync_func(x, y):
            return x + y

        result = await sync_func(1, 2)
        assert result == 3

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_tracked_database_sync_to_async(self):
        """Test that tracked_database_sync_to_async wraps correctly."""
        from wireview.debug.sync_detector import tracked_database_sync_to_async

        def db_func():
            return "db_result"

        wrapped = tracked_database_sync_to_async(db_func, location="test_db")
        result = await wrapped()

        assert result == "db_result"


class TestThreadSafety:
    """Tests for thread safety of the tracker."""

    @pytest.mark.unit
    def test_sync_depth_is_thread_local(self):
        """Test that sync depth is isolated per thread."""
        from wireview.debug.sync_detector import SyncAsyncTracker

        SyncAsyncTracker.reset()

        results = {}

        def thread_func(thread_id, depth):
            for _ in range(depth):
                SyncAsyncTracker.enter_sync(f"thread_{thread_id}")
            results[thread_id] = SyncAsyncTracker.get_depth()[0]

        threads = [
            threading.Thread(target=thread_func, args=(1, 3)),
            threading.Thread(target=thread_func, args=(2, 5)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread should have its own depth
        assert results[1] == 3
        assert results[2] == 5
