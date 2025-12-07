"""
AsyncResult class for tracking asynchronous operation states.

Provides a way to track loading, error, and result states for async operations
in wireview components.

Example:
    class Dashboard(Component):
        stats: AsyncResult[Stats] = None

        async def joined(self):
            self.stats = await self.assign_async(self.load_stats())

    # In template:
    {% if stats.loading %}Loading...{% elif stats.error %}Error{% else %}{{ stats.result }}{% endif %}
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field
from enum import Enum

T = t.TypeVar("T")

__all__ = ("AsyncResult", "AsyncState")


class AsyncState(str, Enum):
    """State of an async operation."""

    PENDING = "pending"
    LOADING = "loading"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class AsyncResult(t.Generic[T]):
    """
    Represents the result of an async operation with loading/error states.

    Attributes:
        state: Current state of the operation
        result: The result value when successful
        error: The error when failed

    Properties:
        loading: True if the operation is in progress
        ok: True if the operation completed successfully
        failed: True if the operation failed with an error
    """

    state: AsyncState = AsyncState.PENDING
    result: T | None = None
    error: Exception | None = None
    _error_message: str | None = field(default=None, repr=False)

    @property
    def loading(self) -> bool:
        """True if the operation is currently loading."""
        return self.state == AsyncState.LOADING

    @property
    def pending(self) -> bool:
        """True if the operation has not started."""
        return self.state == AsyncState.PENDING

    @property
    def ok(self) -> bool:
        """True if the operation completed successfully."""
        return self.state == AsyncState.SUCCESS

    @property
    def failed(self) -> bool:
        """True if the operation failed with an error."""
        return self.state == AsyncState.ERROR

    @property
    def done(self) -> bool:
        """True if the operation is complete (success or error)."""
        return self.state in (AsyncState.SUCCESS, AsyncState.ERROR)

    @property
    def error_message(self) -> str | None:
        """Get the error message if failed."""
        if self._error_message:
            return self._error_message
        if self.error:
            return str(self.error)
        return None

    def __bool__(self) -> bool:
        """True if the operation completed successfully with a result."""
        return self.ok and self.result is not None

    @classmethod
    def loading_state(cls) -> "AsyncResult[T]":
        """Create an AsyncResult in loading state."""
        return cls(state=AsyncState.LOADING)

    @classmethod
    def success(cls, result: T) -> "AsyncResult[T]":
        """Create an AsyncResult with a successful result."""
        return cls(state=AsyncState.SUCCESS, result=result)

    @classmethod
    def failure(cls, error: Exception | str) -> "AsyncResult[T]":
        """Create an AsyncResult with an error."""
        if isinstance(error, str):
            return cls(state=AsyncState.ERROR, _error_message=error)
        return cls(state=AsyncState.ERROR, error=error)

    def map(self, func: t.Callable[[T], "R"]) -> "AsyncResult[R]":
        """
        Transform the result if successful.

        Args:
            func: Function to apply to the result

        Returns:
            A new AsyncResult with the transformed result, or the same error state
        """
        if self.ok and self.result is not None:
            try:
                return AsyncResult.success(func(self.result))
            except Exception as e:
                return AsyncResult.failure(e)
        return AsyncResult(state=self.state, error=self.error, _error_message=self._error_message)

    def get_or(self, default: T) -> T:
        """Get the result or a default value."""
        if self.ok and self.result is not None:
            return self.result
        return default

    def get_or_raise(self) -> T:
        """Get the result or raise the error."""
        if self.ok and self.result is not None:
            return self.result
        if self.error:
            raise self.error
        if self._error_message:
            raise ValueError(self._error_message)
        raise ValueError("AsyncResult has no result")


# Type alias for generic type variable
R = t.TypeVar("R")
