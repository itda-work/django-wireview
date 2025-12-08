"""Debug utilities for wireview.

This module provides debugging tools that can be conditionally enabled
based on settings. In production, these tools have zero overhead.

Configuration:
    WIREVIEW = {
        "DEBUG_SYNC_TRANSITIONS": True,  # Enable sync/async tracking
        "SYNC_TRANSITION_WARNING_THRESHOLD": 2,
        "SYNC_TRANSITION_ERROR_THRESHOLD": 3,
    }
"""

from __future__ import annotations

from .. import settings


class _NoopTracker:
    """No-op tracker for production use."""

    WARNING_THRESHOLD = 2
    ERROR_THRESHOLD = 3

    @classmethod
    def configure(cls, warning_threshold: int = 2, error_threshold: int = 3) -> None:
        """No-op in production."""
        pass

    @classmethod
    def get_depth(cls) -> tuple[int, int]:
        """Always returns (0, 0) in production."""
        return (0, 0)

    @classmethod
    def get_total_depth(cls) -> int:
        """Always returns 0 in production."""
        return 0

    @classmethod
    def enter_sync(cls, location: str = "") -> int:
        """No-op in production."""
        return 0

    @classmethod
    def exit_sync(cls) -> None:
        """No-op in production."""
        pass

    @classmethod
    def enter_async(cls, location: str = "") -> int:
        """No-op in production."""
        return 0

    @classmethod
    def exit_async(cls) -> None:
        """No-op in production."""
        pass

    @classmethod
    def reset(cls) -> None:
        """No-op in production."""
        pass


if settings.DEBUG_SYNC_TRANSITIONS:
    # Import tracked versions when debugging is enabled
    from .sync_detector import (
        SyncAsyncTracker,
        tracked_async_to_sync,
        tracked_database_sync_to_async,
        tracked_sync_to_async,
    )

    # Configure thresholds from settings
    SyncAsyncTracker.configure(
        warning_threshold=settings.SYNC_TRANSITION_WARNING_THRESHOLD,
        error_threshold=settings.SYNC_TRANSITION_ERROR_THRESHOLD,
    )
else:
    # No-op implementations for production - zero overhead
    from asgiref.sync import async_to_sync as tracked_async_to_sync
    from asgiref.sync import sync_to_async as tracked_sync_to_async
    from channels.db import (
        database_sync_to_async as tracked_database_sync_to_async,
    )

    SyncAsyncTracker = _NoopTracker


__all__ = [
    "SyncAsyncTracker",
    "tracked_async_to_sync",
    "tracked_sync_to_async",
    "tracked_database_sync_to_async",
]
