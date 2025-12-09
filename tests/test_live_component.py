"""Tests for LiveComponent (GAP-005)."""

import pytest

from wireview.live_component import LiveComponent, is_live_component
from wireview.testing import MockWireviewMeta, mount

# Test fixtures - LiveComponents


class Counter(LiveComponent):
    """Simple counter LiveComponent."""

    _template_name = "live_components/counter.html"

    count: int = 0
    label: str = "Count"

    async def increment(self, amount: int = 1):
        self.count += amount

    async def decrement(self):
        self.count -= 1


class ChildCounter(Counter):
    """Counter that notifies parent on change."""

    async def increment(self, amount: int = 1):
        await super().increment(amount)
        await self.send_to_parent("counter_changed", count=self.count)


def create_counter(**kwargs) -> Counter:
    """Helper to create a Counter with mock wire."""
    from django.contrib.auth.models import AnonymousUser

    wire = MockWireviewMeta()
    return Counter(user=AnonymousUser(), wire=wire, **kwargs)


@pytest.mark.unit
class TestLiveComponentRegistration:
    """Test LiveComponent class registration."""

    def test_live_component_registered_in_live_all(self):
        """Test LiveComponent is registered in _live_all."""
        assert "Counter" in LiveComponent._live_all
        assert LiveComponent._live_all["Counter"] is Counter

    def test_live_component_registered_in_component_all(self):
        """Test LiveComponent is also registered in Component._all for compatibility."""
        from wireview import Component

        assert "Counter" in Component._all

    def test_live_component_has_name(self):
        """Test LiveComponent has _name attribute."""
        assert Counter._name == "Counter"

    def test_live_component_has_fqn(self):
        """Test LiveComponent has _fqn attribute."""
        assert "Counter" in Counter._fqn

    def test_resolve_live_by_name(self):
        """Test resolving LiveComponent by simple name."""
        resolved = LiveComponent._resolve_live("Counter")
        assert resolved is Counter

    def test_resolve_live_by_fqn(self):
        """Test resolving LiveComponent by FQN."""
        resolved = LiveComponent._resolve_live(Counter._fqn)
        assert resolved is Counter

    def test_resolve_live_not_found(self):
        """Test error when LiveComponent not found."""
        with pytest.raises(LookupError) as exc_info:
            LiveComponent._resolve_live("NonExistent")

        assert "not found" in str(exc_info.value)


@pytest.mark.unit
class TestLiveComponentMarker:
    """Test LiveComponent identification."""

    def test_is_live_component_true(self):
        """Test is_live_component returns True for LiveComponent."""
        counter = create_counter(id="counter-1")
        assert is_live_component(counter) is True

    def test_is_live_component_false_for_component(self):
        """Test is_live_component returns False for regular Component."""
        from django.contrib.auth.models import AnonymousUser

        from wireview import Component

        class RegularComponent(Component):
            _template_name = "test.html"

        wire = MockWireviewMeta()
        component = RegularComponent(id="regular-1", user=AnonymousUser(), wire=wire)
        assert is_live_component(component) is False

    def test_live_component_class_marker(self):
        """Test _is_live_component class attribute."""
        assert Counter._is_live_component is True


@pytest.mark.unit
class TestLiveComponentFields:
    """Test LiveComponent field handling."""

    def test_parent_id_none_by_default(self):
        """Test _parent_id is None by default."""
        counter = create_counter(id="counter-1")
        assert counter._parent_id is None

    def test_parent_id_can_be_set(self):
        """Test _parent_id can be set."""
        counter = create_counter(id="counter-1")
        counter._parent_id = "dashboard-1"
        assert counter._parent_id == "dashboard-1"

    def test_myself_property(self):
        """Test myself property returns component ID."""
        counter = create_counter(id="counter-1")
        assert counter.myself == "counter-1"

    def test_parent_id_excluded_from_serialization(self):
        """Test _parent_id is excluded from model dump."""
        counter = create_counter(id="counter-1")
        counter._parent_id = "dashboard-1"

        dump = counter.model_dump()
        assert "_parent_id" not in dump


@pytest.mark.unit
class TestLiveComponentUpdate:
    """Test LiveComponent update callback."""

    @pytest.mark.asyncio
    async def test_update_sets_attributes(self):
        """Test update() sets matching attributes."""
        view = await mount(Counter, count=0)

        await view.component.update(count=10, label="New Label")

        assert view.component.count == 10
        assert view.component.label == "New Label"

    @pytest.mark.asyncio
    async def test_update_ignores_unknown_attributes(self):
        """Test update() ignores attributes not in model_fields."""
        view = await mount(Counter, count=0)

        # Should not raise even with unknown attribute
        await view.component.update(count=10, unknown_field="ignored")

        assert view.component.count == 10


@pytest.mark.unit
class TestLiveComponentEventHandlers:
    """Test LiveComponent event handlers."""

    @pytest.mark.asyncio
    async def test_increment(self):
        """Test increment event handler."""
        view = await mount(Counter, count=5)

        await view.call("increment")

        assert view.component.count == 6

    @pytest.mark.asyncio
    async def test_increment_with_amount(self):
        """Test increment with custom amount."""
        view = await mount(Counter, count=5)

        await view.call("increment", amount=3)

        assert view.component.count == 8

    @pytest.mark.asyncio
    async def test_decrement(self):
        """Test decrement event handler."""
        view = await mount(Counter, count=5)

        await view.call("decrement")

        assert view.component.count == 4


@pytest.mark.unit
class TestLiveComponentInheritance:
    """Test LiveComponent inheritance."""

    def test_child_inherits_template(self):
        """Test child LiveComponent inherits template."""
        # ChildCounter doesn't set _template_name, so inherits from Counter
        assert ChildCounter._template_name == "live_components/counter.html"

    def test_child_registered_separately(self):
        """Test child LiveComponent has its own registration."""
        assert "ChildCounter" in LiveComponent._live_all
        assert LiveComponent._live_all["ChildCounter"] is ChildCounter

    @pytest.mark.asyncio
    async def test_child_can_call_send_to_parent(self):
        """Test child can call send_to_parent without error when no parent."""
        view = await mount(ChildCounter, count=0)
        view.component._parent_id = None  # No parent

        # Should not raise, just no-op
        await view.call("increment")

        assert view.component.count == 1


@pytest.mark.integration
class TestLiveComponentWithMock:
    """Integration tests with mocked wire."""

    @pytest.mark.asyncio
    async def test_send_to_parent_calls_wire(self):
        """Test send_to_parent calls wire.send_to_parent."""
        from unittest.mock import AsyncMock

        view = await mount(ChildCounter, count=0)
        view.component._parent_id = "dashboard-1"

        # Mock send_to_parent on the wire
        view.component.wire.send_to_parent = AsyncMock()

        await view.component.send_to_parent("counter_changed", count=5)

        view.component.wire.send_to_parent.assert_called_once_with("dashboard-1", "counter_changed", {"count": 5})
