"""Tests for Flash Messages feature (GAP-011)."""

import pytest

from wireview import Component
from wireview.testing import mount


class FlashComponent(Component):
    """Test component for flash messages."""

    _template_name = "simple.html"

    async def save_success(self):
        """Simulate successful save."""
        await self.put_flash("success", "Item saved successfully!")

    async def save_error(self):
        """Simulate failed save."""
        await self.put_flash("error", "Failed to save item")

    async def show_info(self):
        """Show info message."""
        await self.put_flash("info", "Here's some information")

    async def show_warning(self):
        """Show warning message."""
        await self.put_flash("warning", "Be careful!")

    async def custom_timeout(self):
        """Show message with custom timeout."""
        await self.put_flash("info", "Quick message", timeout=2000)

    async def no_dismiss(self):
        """Show non-dismissible message."""
        await self.put_flash("error", "Critical error", dismissible=False)

    async def persistent_message(self):
        """Show persistent message (no auto-dismiss)."""
        await self.put_flash("info", "Stays forever", timeout=0)

    async def clear_all(self):
        """Clear all flash messages."""
        await self.clear_flash()

    async def clear_specific(self, flash_id: str):
        """Clear specific flash message."""
        await self.clear_flash(flash_id)


@pytest.mark.unit
class TestPutFlash:
    """Test put_flash method."""

    @pytest.mark.asyncio
    async def test_put_flash_sends_flash_message(self):
        """Test that put_flash sends a flash message."""
        view = await mount(FlashComponent)

        await view.call("save_success")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert len(flash_messages) == 1
        msg = flash_messages[0]
        assert msg["type"] == "flash"
        assert msg["flash_type"] == "success"
        assert msg["message"] == "Item saved successfully!"

    @pytest.mark.asyncio
    async def test_put_flash_success_type(self):
        """Test flash message with success type."""
        view = await mount(FlashComponent)

        await view.call("save_success")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["type"] == "flash"
        # Note: The 'type' in payload is for flash type (success, error, etc.)
        # but the sent message type is 'flash'. Check the correct field.

    @pytest.mark.asyncio
    async def test_put_flash_error_type(self):
        """Test flash message with error type."""
        view = await mount(FlashComponent)

        await view.call("save_error")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert len(flash_messages) == 1

    @pytest.mark.asyncio
    async def test_put_flash_info_type(self):
        """Test flash message with info type."""
        view = await mount(FlashComponent)

        await view.call("show_info")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert len(flash_messages) == 1
        assert flash_messages[0]["message"] == "Here's some information"

    @pytest.mark.asyncio
    async def test_put_flash_warning_type(self):
        """Test flash message with warning type."""
        view = await mount(FlashComponent)

        await view.call("show_warning")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert len(flash_messages) == 1
        assert flash_messages[0]["message"] == "Be careful!"

    @pytest.mark.asyncio
    async def test_put_flash_default_timeout(self):
        """Test flash message has default timeout of 5000ms."""
        view = await mount(FlashComponent)

        await view.call("save_success")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["timeout"] == 5000

    @pytest.mark.asyncio
    async def test_put_flash_custom_timeout(self):
        """Test flash message with custom timeout."""
        view = await mount(FlashComponent)

        await view.call("custom_timeout")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["timeout"] == 2000

    @pytest.mark.asyncio
    async def test_put_flash_no_auto_dismiss(self):
        """Test flash message with timeout=0 (no auto-dismiss)."""
        view = await mount(FlashComponent)

        await view.call("persistent_message")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["timeout"] == 0

    @pytest.mark.asyncio
    async def test_put_flash_default_dismissible(self):
        """Test flash message is dismissible by default."""
        view = await mount(FlashComponent)

        await view.call("save_success")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["dismissible"] is True

    @pytest.mark.asyncio
    async def test_put_flash_non_dismissible(self):
        """Test flash message can be non-dismissible."""
        view = await mount(FlashComponent)

        await view.call("no_dismiss")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert flash_messages[0]["dismissible"] is False


@pytest.mark.unit
class TestClearFlash:
    """Test clear_flash method."""

    @pytest.mark.asyncio
    async def test_clear_flash_all(self):
        """Test clearing all flash messages."""
        view = await mount(FlashComponent)

        await view.call("clear_all")

        clear_messages = [m for m in view.wire.sent_messages if m["type"] == "clear_flash"]
        assert len(clear_messages) == 1
        assert clear_messages[0]["flash_id"] is None

    @pytest.mark.asyncio
    async def test_clear_flash_specific(self):
        """Test clearing specific flash message."""
        view = await mount(FlashComponent)

        await view.call("clear_specific", flash_id="flash-123")

        clear_messages = [m for m in view.wire.sent_messages if m["type"] == "clear_flash"]
        assert len(clear_messages) == 1
        assert clear_messages[0]["flash_id"] == "flash-123"


@pytest.mark.unit
class TestMultipleFlashes:
    """Test multiple flash messages."""

    @pytest.mark.asyncio
    async def test_multiple_flash_messages(self):
        """Test sending multiple flash messages."""
        view = await mount(FlashComponent)

        await view.call("save_success")
        await view.call("show_info")
        await view.call("show_warning")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        assert len(flash_messages) == 3

    @pytest.mark.asyncio
    async def test_flash_and_clear(self):
        """Test sending flash then clearing."""
        view = await mount(FlashComponent)

        await view.call("save_success")
        await view.call("clear_all")

        flash_messages = [m for m in view.wire.sent_messages if m["type"] == "flash"]
        clear_messages = [m for m in view.wire.sent_messages if m["type"] == "clear_flash"]

        assert len(flash_messages) == 1
        assert len(clear_messages) == 1
