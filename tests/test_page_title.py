"""Tests for Page Title feature (GAP-010)."""

import pytest

from wireview import Component
from wireview.testing import mount


class TitleComponent(Component):
    """Test component for page title."""

    _template_name = "simple.html"

    page: int = 1
    product_name: str = "Widget"

    async def joined(self):
        """Set initial title on mount."""
        await self.push_title(f"Page {self.page} - Products")

    async def next_page(self):
        """Go to next page and update title."""
        self.page += 1
        await self.push_title(f"Page {self.page} - Products")

    async def set_product(self, name: str):
        """Set product name and update title."""
        self.product_name = name
        await self.push_title(f"{name} - My Store")


@pytest.mark.unit
class TestPushTitle:
    """Test push_title method."""

    @pytest.mark.asyncio
    async def test_push_title_sends_title_message(self):
        """Test that push_title sends a title message."""
        view = await mount(TitleComponent)

        # Clear messages from joined()
        view.wire.sent_messages.clear()

        await view.component.push_title("New Title")

        # Verify message was sent
        assert len(view.wire.sent_messages) == 1
        msg = view.wire.sent_messages[0]
        assert msg["type"] == "title"
        assert msg["title"] == "New Title"

    @pytest.mark.asyncio
    async def test_push_title_on_joined(self):
        """Test that push_title works in joined() lifecycle."""
        view = await mount(TitleComponent)

        # Find title message from joined()
        title_messages = [m for m in view.wire.sent_messages if m["type"] == "title"]
        assert len(title_messages) == 1
        assert title_messages[0]["title"] == "Page 1 - Products"

    @pytest.mark.asyncio
    async def test_push_title_on_event(self):
        """Test that push_title works in event handlers."""
        view = await mount(TitleComponent)

        # Clear messages
        view.wire.sent_messages.clear()

        # Call event handler
        await view.call("next_page")

        # Verify title was updated
        title_messages = [m for m in view.wire.sent_messages if m["type"] == "title"]
        assert len(title_messages) == 1
        assert title_messages[0]["title"] == "Page 2 - Products"
        assert view.component.page == 2

    @pytest.mark.asyncio
    async def test_push_title_with_dynamic_content(self):
        """Test push_title with dynamic content."""
        view = await mount(TitleComponent)
        view.wire.sent_messages.clear()

        await view.call("set_product", name="Super Gadget")

        title_messages = [m for m in view.wire.sent_messages if m["type"] == "title"]
        assert len(title_messages) == 1
        assert title_messages[0]["title"] == "Super Gadget - My Store"

    @pytest.mark.asyncio
    async def test_push_title_empty_string(self):
        """Test push_title with empty string."""
        view = await mount(TitleComponent)
        view.wire.sent_messages.clear()

        await view.component.push_title("")

        title_messages = [m for m in view.wire.sent_messages if m["type"] == "title"]
        assert len(title_messages) == 1
        assert title_messages[0]["title"] == ""

    @pytest.mark.asyncio
    async def test_push_title_special_characters(self):
        """Test push_title with special characters."""
        view = await mount(TitleComponent)
        view.wire.sent_messages.clear()

        special_title = 'Product <script> & "quotes" - 한글'
        await view.component.push_title(special_title)

        title_messages = [m for m in view.wire.sent_messages if m["type"] == "title"]
        assert len(title_messages) == 1
        assert title_messages[0]["title"] == special_title
