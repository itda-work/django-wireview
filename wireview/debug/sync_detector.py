"""Sync/async transition depth detector for performance monitoring.

This module provides tools to detect and warn about nested async_to_sync
and sync_to_async transitions, which can cause performance degradation.

Enable detection by setting:
    WIREVIEW = {
        "DEBUG_SYNC_TRANSITIONS": True,
        "SYNC_TRANSITION_WARNING_THRESHOLD": 2,  # optional
        "SYNC_TRANSITION_ERROR_THRESHOLD": 3,    # optional
    }

Note on tracking limitations:
    Because sync_to_async runs in different threads and async_to_sync creates
    new event loops, we use a combination of thread-local and global state
    to track nesting depth. This provides accurate detection for the most
    common problematic pattern:

        ASYNC (Consumer)
          → sync_to_async (db wrapper)
            → SYNC (render)
              → async_to_sync (coroutine resolution)  # WARNING triggered here
"""

from __future__ import annotations

import logging
import threading
import traceback
import typing as t
from functools import wraps

if t.TYPE_CHECKING:
    from typing import Callable, ParamSpec, TypeVar

    P = ParamSpec("P")
    T = TypeVar("T")

log = logging.getLogger("wireview.sync_detector")

# Global lock for thread-safe access to shared state
_lock = threading.Lock()

# Global counter for total transition depth across all threads
# This is incremented on enter and decremented on exit
_global_depth = 0

# Thread-local for per-thread depth info (for debugging)
_thread_local = threading.local()


class SyncAsyncTracker:
    """Tracks async/sync transition nesting depth.

    This class helps detect performance issues caused by nested
    async_to_sync and sync_to_async calls. When transitions exceed
    the configured thresholds, warnings or errors are logged.

    Example problematic pattern:
        ASYNC -> sync_to_async -> SYNC -> async_to_sync -> ASYNC
        (3 levels of nesting)

    The tracking works as follows:
    - enter_sync/enter_async increment a global counter
    - exit_sync/exit_async decrement it
    - Warnings are emitted when the counter exceeds thresholds
    """

    WARNING_THRESHOLD = 2
    ERROR_THRESHOLD = 3

    @classmethod
    def configure(cls, warning_threshold: int = 2, error_threshold: int = 3) -> None:
        """Configure the thresholds for warnings and errors."""
        cls.WARNING_THRESHOLD = warning_threshold
        cls.ERROR_THRESHOLD = error_threshold

    @classmethod
    def get_depth(cls) -> tuple[int, int]:
        """Get current (sync_depth, async_depth) for this thread.

        Note: This returns thread-local values which may not reflect
        the global nesting state across thread boundaries.
        """
        sync_d = getattr(_thread_local, "sync_depth", 0)
        async_d = getattr(_thread_local, "async_depth", 0)
        return sync_d, async_d

    @classmethod
    def get_total_depth(cls) -> int:
        """Get global total transition depth across all threads."""
        global _global_depth
        with _lock:
            return _global_depth

    @classmethod
    def enter_sync(cls, location: str = "") -> int:
        """Called when entering sync context from async (async_to_sync)."""
        global _global_depth

        # Update thread-local
        current = getattr(_thread_local, "sync_depth", 0)
        _thread_local.sync_depth = current + 1

        # Update global and check threshold
        with _lock:
            _global_depth += 1
            total = _global_depth

        if total > cls.WARNING_THRESHOLD:
            cls._emit_warning("sync", total, location)

        return total

    @classmethod
    def exit_sync(cls) -> None:
        """Called when exiting sync context."""
        global _global_depth

        current = getattr(_thread_local, "sync_depth", 0)
        _thread_local.sync_depth = max(0, current - 1)

        with _lock:
            _global_depth = max(0, _global_depth - 1)

    @classmethod
    def enter_async(cls, location: str = "") -> int:
        """Called when entering async context from sync (sync_to_async)."""
        global _global_depth

        # Update thread-local
        current = getattr(_thread_local, "async_depth", 0)
        _thread_local.async_depth = current + 1

        # Update global and check threshold
        with _lock:
            _global_depth += 1
            total = _global_depth

        if total > cls.WARNING_THRESHOLD:
            cls._emit_warning("async", total, location)

        return total

    @classmethod
    def exit_async(cls) -> None:
        """Called when exiting async context."""
        global _global_depth

        current = getattr(_thread_local, "async_depth", 0)
        _thread_local.async_depth = max(0, current - 1)

        with _lock:
            _global_depth = max(0, _global_depth - 1)

    @classmethod
    def reset(cls) -> None:
        """Reset all depth counters. Useful for testing."""
        global _global_depth
        _thread_local.sync_depth = 0
        _thread_local.async_depth = 0
        with _lock:
            _global_depth = 0

    @classmethod
    def _emit_warning(cls, context_type: str, depth: int, location: str) -> None:
        """Emit warning or error based on depth."""
        msg = (
            f"Nested {context_type} context detected (total depth={depth}) "
            f"at {location}. This may cause performance degradation. "
            f"Consider restructuring to avoid nested async/sync transitions."
        )

        if depth > cls.ERROR_THRESHOLD:
            log.error(msg)
            log.error("Stack trace:\n%s", "".join(traceback.format_stack()[:-1]))
        else:
            log.warning(msg)


def tracked_async_to_sync(
    fn: "Callable[P, t.Coroutine[t.Any, t.Any, T]] | None" = None,
    *,
    force_new_loop: bool = False,
    location: str = "",
) -> t.Any:
    """Wrapper for async_to_sync that tracks nesting depth.

    Can be used as a decorator or called directly:
        @tracked_async_to_sync
        async def my_func(): ...

        result = tracked_async_to_sync(my_coro, location="inline")()
    """
    from asgiref.sync import async_to_sync

    def decorator(
        func: "Callable[P, t.Coroutine[t.Any, t.Any, T]]",
    ) -> "Callable[P, T]":
        loc = location or func.__qualname__

        @wraps(func)
        def wrapper(*args: "P.args", **kwargs: "P.kwargs") -> "T":
            SyncAsyncTracker.enter_sync(loc)
            try:
                wrapped = async_to_sync(func, force_new_loop=force_new_loop)
                return wrapped(*args, **kwargs)
            finally:
                SyncAsyncTracker.exit_sync()

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


def tracked_sync_to_async(
    fn: "Callable[P, T] | None" = None,
    *,
    thread_sensitive: bool = True,
    location: str = "",
) -> t.Any:
    """Wrapper for sync_to_async that tracks nesting depth.

    Can be used as a decorator or called directly:
        @tracked_sync_to_async
        def my_func(): ...
    """
    from asgiref.sync import sync_to_async

    def decorator(func: "Callable[P, T]") -> "Callable[P, t.Coroutine[t.Any, t.Any, T]]":
        loc = location or func.__qualname__

        @wraps(func)
        async def wrapper(*args: "P.args", **kwargs: "P.kwargs") -> "T":
            SyncAsyncTracker.enter_async(loc)
            try:
                wrapped = sync_to_async(func, thread_sensitive=thread_sensitive)
                return await wrapped(*args, **kwargs)
            finally:
                SyncAsyncTracker.exit_async()

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


def tracked_database_sync_to_async(
    fn: "Callable[P, T] | None" = None,
    *,
    thread_sensitive: bool = True,
    location: str = "",
) -> t.Any:
    """Wrapper for database_sync_to_async that tracks nesting depth."""
    from channels.db import database_sync_to_async

    def decorator(func: "Callable[P, T]") -> "Callable[P, t.Coroutine[t.Any, t.Any, T]]":
        loc = location or func.__qualname__

        @wraps(func)
        async def wrapper(*args: "P.args", **kwargs: "P.kwargs") -> "T":
            SyncAsyncTracker.enter_async(loc)
            try:
                wrapped = database_sync_to_async(func, thread_sensitive=thread_sensitive)
                return await wrapped(*args, **kwargs)
            finally:
                SyncAsyncTracker.exit_async()

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


__all__ = [
    "SyncAsyncTracker",
    "tracked_async_to_sync",
    "tracked_sync_to_async",
    "tracked_database_sync_to_async",
]
