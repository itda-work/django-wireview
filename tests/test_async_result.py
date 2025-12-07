"""Tests for AsyncResult and assign_async functionality."""

import asyncio

import pytest

from wireview import Component
from wireview.async_result import AsyncResult
from wireview.testing import mount


class TestAsyncResult:
    """Test the AsyncResult class."""

    @pytest.mark.unit
    def test_initial_state_is_pending(self):
        """AsyncResult should start in pending state."""
        result = AsyncResult()
        assert result.pending
        assert not result.loading
        assert not result.ok
        assert not result.failed

    @pytest.mark.unit
    def test_loading_state(self):
        """loading_state() should create a loading AsyncResult."""
        result = AsyncResult.loading_state()
        assert result.loading
        assert not result.pending
        assert not result.ok
        assert not result.failed

    @pytest.mark.unit
    def test_success_state(self):
        """success() should create a successful AsyncResult."""
        result = AsyncResult.success(42)
        assert result.ok
        assert result.result == 42
        assert not result.loading
        assert not result.failed

    @pytest.mark.unit
    def test_failure_state_with_exception(self):
        """failure() should create a failed AsyncResult with exception."""
        error = ValueError("test error")
        result = AsyncResult.failure(error)
        assert result.failed
        assert result.error is error
        assert result.error_message == "test error"
        assert not result.ok

    @pytest.mark.unit
    def test_failure_state_with_string(self):
        """failure() should accept string error messages."""
        result = AsyncResult.failure("something went wrong")
        assert result.failed
        assert result.error_message == "something went wrong"

    @pytest.mark.unit
    def test_done_property(self):
        """done should be True for success or error states."""
        assert not AsyncResult().done
        assert not AsyncResult.loading_state().done
        assert AsyncResult.success(1).done
        assert AsyncResult.failure("err").done

    @pytest.mark.unit
    def test_bool_conversion(self):
        """AsyncResult should be truthy only when successful with result."""
        assert not bool(AsyncResult())
        assert not bool(AsyncResult.loading_state())
        assert bool(AsyncResult.success(42))
        assert not bool(AsyncResult.success(None))
        assert not bool(AsyncResult.failure("err"))

    @pytest.mark.unit
    def test_map_success(self):
        """map() should transform successful results."""
        result = AsyncResult.success(10)
        mapped = result.map(lambda x: x * 2)
        assert mapped.ok
        assert mapped.result == 20

    @pytest.mark.unit
    def test_map_failure(self):
        """map() should preserve error state."""
        result = AsyncResult.failure("error")
        mapped = result.map(lambda x: x * 2)
        assert mapped.failed
        assert mapped.error_message == "error"

    @pytest.mark.unit
    def test_map_exception_in_func(self):
        """map() should catch exceptions in the transform function."""
        result = AsyncResult.success(10)
        mapped = result.map(lambda x: 1 / 0)
        assert mapped.failed
        assert isinstance(mapped.error, ZeroDivisionError)

    @pytest.mark.unit
    def test_get_or(self):
        """get_or() should return result or default."""
        assert AsyncResult.success(42).get_or(0) == 42
        assert AsyncResult.failure("err").get_or(0) == 0
        assert AsyncResult.loading_state().get_or(0) == 0

    @pytest.mark.unit
    def test_get_or_raise_success(self):
        """get_or_raise() should return result when successful."""
        assert AsyncResult.success(42).get_or_raise() == 42

    @pytest.mark.unit
    def test_get_or_raise_failure(self):
        """get_or_raise() should raise error when failed."""
        error = ValueError("test")
        result = AsyncResult.failure(error)
        with pytest.raises(ValueError, match="test"):
            result.get_or_raise()


class AsyncComponent(Component):
    """Test component for async operations."""

    _template_name = "todo/counter.html"

    data: AsyncResult[str] | None = None
    load_delay: float = 0.01

    async def load_data(self) -> str:
        await asyncio.sleep(self.load_delay)
        return "loaded data"

    async def load_with_error(self) -> str:
        await asyncio.sleep(self.load_delay)
        raise ValueError("load failed")

    async def start_loading(self):
        self.data = await self.assign_async(self.load_data())

    async def start_loading_with_error(self):
        self.data = await self.assign_async(self.load_with_error())


class TestAssignAsync:
    """Test the assign_async method."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_loading_state_immediately(self):
        """assign_async should return loading state immediately."""
        view = await mount(AsyncComponent)
        await view.call("start_loading")

        # Should be in loading state immediately
        assert view.component.data is not None
        assert view.component.data.loading

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_updates_to_success_after_completion(self):
        """assign_async should update to success when operation completes."""
        view = await mount(AsyncComponent, load_delay=0.01)
        await view.call("start_loading")

        # Wait for the async operation to complete
        await asyncio.sleep(0.05)

        assert view.component.data is not None
        assert view.component.data.ok
        assert view.component.data.result == "loaded data"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_updates_to_error_on_failure(self):
        """assign_async should update to error when operation fails."""
        view = await mount(AsyncComponent, load_delay=0.01)
        await view.call("start_loading_with_error")

        # Wait for the async operation to complete
        await asyncio.sleep(0.05)

        assert view.component.data is not None
        assert view.component.data.failed
        assert view.component.data.error_message == "load failed"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_triggers_rerender_on_completion(self):
        """assign_async should trigger re-render when operation completes."""
        view = await mount(AsyncComponent, load_delay=0.01)
        view.clear_messages()

        await view.call("start_loading")

        # Wait for the async operation to complete
        await asyncio.sleep(0.05)

        # Should have sent a send_render message
        render_messages = [m for m in view.sent_messages if m.get("type") == "send_render"]
        assert len(render_messages) > 0
