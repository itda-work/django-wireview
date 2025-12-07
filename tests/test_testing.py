"""Tests for the testing utilities module."""

import pytest

from wireview import Component
from wireview.testing import ComponentTestCase, MountedComponent, mount


class SimpleCounter(Component):
    """A simple counter component for testing."""

    _template_name = "todo/counter.html"  # Use existing template

    count: int = 0

    async def increment(self, amount: int = 1):
        self.count += amount

    async def decrement(self):
        self.count -= 1

    async def reset(self):
        self.count = 0


class RedirectComponent(Component):
    """Component that tests redirect functionality."""

    _template_name = "todo/counter.html"

    async def do_redirect(self, url: str = "/home"):
        await self.wire.redirect_to(url)

    async def do_replace(self, url: str = "/dashboard"):
        await self.wire.replace_to(url)


class TestMount:
    """Test the mount() function."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_creates_component(self):
        """mount() should create a component instance."""
        view = await mount(SimpleCounter)
        assert view.component is not None
        assert isinstance(view.component, SimpleCounter)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_with_initial_state(self):
        """mount() should accept initial state."""
        view = await mount(SimpleCounter, count=10)
        assert view.component.count == 10

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_returns_mounted_component(self):
        """mount() should return a MountedComponent wrapper."""
        view = await mount(SimpleCounter)
        assert isinstance(view, MountedComponent)


class TestMountedComponent:
    """Test the MountedComponent class."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_handler(self):
        """call() should invoke the handler and update state."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment")
        assert view.component.count == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_handler_with_args(self):
        """call() should pass arguments to the handler."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment", amount=5)
        assert view.component.count == 5

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_multiple_handlers(self):
        """Multiple handler calls should accumulate changes."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment", amount=3)
        await view.call("increment", amount=2)
        await view.call("decrement")
        assert view.component.count == 4

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_nonexistent_handler(self):
        """call() should raise AttributeError for missing handlers."""
        view = await mount(SimpleCounter)
        with pytest.raises(AttributeError, match="has no handler"):
            await view.call("nonexistent")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_sent_messages(self):
        """sent_messages should track messages sent to client."""
        view = await mount(RedirectComponent)
        await view.call("do_redirect", url="/test")
        assert len(view.sent_messages) > 0
        redirect_msg = view.sent_messages[-1]
        assert redirect_msg["type"] == "url_change"
        assert redirect_msg["command"] == "redirect"
        assert redirect_msg["url"] == "/test"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_redirected_to(self):
        """redirected_to should track redirect URL."""
        view = await mount(RedirectComponent)
        assert view.redirected_to is None
        await view.call("do_redirect", url="/home")
        assert view.redirected_to == "/home"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_is_frozen_after_redirect(self):
        """Component should be frozen after redirect."""
        view = await mount(RedirectComponent)
        assert not view.is_frozen
        await view.call("do_redirect")
        assert view.is_frozen

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clear_messages(self):
        """clear_messages() should empty the message list."""
        view = await mount(RedirectComponent)
        await view.call("do_replace")
        assert len(view.sent_messages) > 0
        view.clear_messages()
        assert len(view.sent_messages) == 0


class TestComponentTestCase(ComponentTestCase):
    """Test the ComponentTestCase mixin."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_via_mixin(self):
        """ComponentTestCase.mount() should work like standalone mount()."""
        view = await self.mount(SimpleCounter, count=5)
        assert view.component.count == 5
        await view.call("increment")
        assert view.component.count == 6

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_components(self):
        """Should be able to mount multiple components in one test."""
        view1 = await self.mount(SimpleCounter, count=10)
        view2 = await self.mount(SimpleCounter, count=20)

        await view1.call("increment")
        await view2.call("decrement")

        assert view1.component.count == 11
        assert view2.component.count == 19
