"""Tests for temporary_assigns feature (GAP-006).

This feature resets specified fields to their default values after each render,
similar to Phoenix LiveView's temporary_assigns option.
"""

import pytest

from wireview import Component
from wireview.testing import mount


class ListComponent(Component):
    """Component with a temporary list field."""

    _template_name = "todo/counter.html"
    _temporary_assigns = {"items"}

    items: list[str] = []
    count: int = 0  # Not temporary, should persist

    async def load_items(self):
        self.items = ["item1", "item2", "item3"]

    async def increment(self):
        self.count += 1


class DictComponent(Component):
    """Component with a temporary dict field."""

    _template_name = "todo/counter.html"
    _temporary_assigns = {"data"}

    data: dict = {}
    name: str = "test"

    async def load_data(self):
        self.data = {"key": "value", "count": 42}


class MultipleTemporaryComponent(Component):
    """Component with multiple temporary fields."""

    _template_name = "todo/counter.html"
    _temporary_assigns = {"items", "metadata"}

    items: list[int] = []
    metadata: dict = {}
    persistent_value: str = "keep me"

    async def load_all(self):
        self.items = [1, 2, 3, 4, 5]
        self.metadata = {"loaded": True}
        self.persistent_value = "modified"


class NoTemporaryComponent(Component):
    """Component without temporary assigns."""

    _template_name = "todo/counter.html"

    items: list[str] = []

    async def load_items(self):
        self.items = ["a", "b", "c"]


class TestClearTemporaryAssigns:
    """Test the _clear_temporary_assigns() method directly."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clears_list_field(self):
        """Should clear list field to empty list after render."""
        view = await mount(ListComponent)
        await view.call("load_items")

        assert view.component.items == ["item1", "item2", "item3"]

        # Manually call clear (normally called by consumer.send_render)
        view.component._clear_temporary_assigns()

        assert view.component.items == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clears_dict_field(self):
        """Should clear dict field to empty dict after render."""
        view = await mount(DictComponent)
        await view.call("load_data")

        assert view.component.data == {"key": "value", "count": 42}

        view.component._clear_temporary_assigns()

        assert view.component.data == {}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_preserves_non_temporary_fields(self):
        """Should not affect fields not in _temporary_assigns."""
        view = await mount(ListComponent, count=10)
        await view.call("load_items")
        await view.call("increment")

        assert view.component.count == 11
        assert view.component.items == ["item1", "item2", "item3"]

        view.component._clear_temporary_assigns()

        # items should be cleared
        assert view.component.items == []
        # count should remain
        assert view.component.count == 11

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clears_multiple_temporary_fields(self):
        """Should clear all fields in _temporary_assigns."""
        view = await mount(MultipleTemporaryComponent)
        await view.call("load_all")

        assert view.component.items == [1, 2, 3, 4, 5]
        assert view.component.metadata == {"loaded": True}
        assert view.component.persistent_value == "modified"

        view.component._clear_temporary_assigns()

        assert view.component.items == []
        assert view.component.metadata == {}
        # persistent_value should remain
        assert view.component.persistent_value == "modified"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_error_when_empty_temporary_assigns(self):
        """Should not error when _temporary_assigns is empty."""
        view = await mount(NoTemporaryComponent)
        await view.call("load_items")

        assert view.component.items == ["a", "b", "c"]

        # Should not raise any error
        view.component._clear_temporary_assigns()

        # Items should remain since not in _temporary_assigns
        assert view.component.items == ["a", "b", "c"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_can_reassign_after_clear(self):
        """Should be able to reassign values after clearing."""
        view = await mount(ListComponent)

        # First load
        await view.call("load_items")
        assert view.component.items == ["item1", "item2", "item3"]

        # Clear
        view.component._clear_temporary_assigns()
        assert view.component.items == []

        # Second load
        await view.call("load_items")
        assert view.component.items == ["item1", "item2", "item3"]


class TestTemporaryAssignsInheritance:
    """Test _temporary_assigns inheritance behavior."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_inherits_temporary_assigns(self):
        """Subclasses should inherit _temporary_assigns from parent."""

        class ChildComponent(ListComponent):
            extra: str = "extra"

        view = await mount(ChildComponent)
        await view.call("load_items")

        assert view.component.items == ["item1", "item2", "item3"]

        view.component._clear_temporary_assigns()

        assert view.component.items == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_can_override_temporary_assigns(self):
        """Subclasses can override _temporary_assigns."""

        class ChildComponent(ListComponent):
            _temporary_assigns = {"items", "extra_list"}
            extra_list: list[int] = []

            async def load_extra(self):
                self.extra_list = [1, 2, 3]

        view = await mount(ChildComponent)
        await view.call("load_items")
        await view.call("load_extra")

        assert view.component.items == ["item1", "item2", "item3"]
        assert view.component.extra_list == [1, 2, 3]

        view.component._clear_temporary_assigns()

        assert view.component.items == []
        assert view.component.extra_list == []


class TestTemporaryAssignsWithDefaultFactory:
    """Test _temporary_assigns with default_factory fields."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clears_to_factory_default(self):
        """Should use default_factory when clearing."""

        class FactoryComponent(Component):
            _template_name = "todo/counter.html"
            _temporary_assigns = {"items"}

            # Using list as default_factory (implicit via = [])
            items: list[str] = []

            async def load(self):
                self.items = ["a", "b"]

        view = await mount(FactoryComponent)
        await view.call("load")

        view.component._clear_temporary_assigns()

        # Should be a new empty list (not None)
        assert view.component.items == []
        assert view.component.items is not None
