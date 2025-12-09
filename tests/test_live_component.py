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


@pytest.mark.unit
class TestComponentRepositoryLiveComponent:
    """Test ComponentRepository LiveComponent methods."""

    def test_build_live_component(self):
        """Test building a LiveComponent via repository."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=False, user=AnonymousUser())

        live_comp = repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        assert live_comp.id == "counter-1"
        assert live_comp.count == 10
        assert live_comp._parent_id == "dashboard-1"

    def test_build_live_component_requires_id(self):
        """Test that build_live_component requires id in state."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=False, user=AnonymousUser())

        with pytest.raises(ValueError) as exc_info:
            repo.build_live_component(
                name="Counter",
                state={"count": 10},  # No id
                parent_id="dashboard-1",
            )

        assert "requires an 'id'" in str(exc_info.value)

    def test_build_live_component_registered(self):
        """Test that built LiveComponent is registered in repo."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=False, user=AnonymousUser())

        live_comp = repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        assert repo.get("counter-1") is live_comp

    @pytest.mark.asyncio
    async def test_build_live_component_reuses_existing(self):
        """Test that build_live_component reuses existing component on re-render."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=False, user=AnonymousUser())

        # First render
        live_comp1 = repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        # Flush to initialize
        await repo.flush_pending_live_components()

        # Simulate state change
        live_comp1.count = 20

        # Re-render with new props
        live_comp2 = repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 30},
            parent_id="dashboard-1",
        )

        # Should be same instance
        assert live_comp1 is live_comp2

        # Props not updated yet (queued for update)
        assert live_comp2.count == 20

        # Flush to call update()
        await repo.flush_pending_live_components()

        # Now props should be updated
        assert live_comp2.count == 30

    def test_get_live_components(self):
        """Test getting all LiveComponents under a parent."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=False, user=AnonymousUser())

        # Create multiple LiveComponents under same parent
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-2", "count": 20},
            parent_id="dashboard-1",
        )

        # Create one under different parent
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-3", "count": 30},
            parent_id="dashboard-2",
        )

        # Get children of dashboard-1
        children = repo.get_live_components("dashboard-1")

        assert len(children) == 2
        assert all(c._parent_id == "dashboard-1" for c in children)


@pytest.mark.unit
class TestMyselfTargeting:
    """Test @myself targeting via {% on %} tag."""

    def test_transpile_includes_target_in_kwargs(self):
        """Test that _target is included in transpiled kwargs."""
        from wireview.event_transpiler import transpile

        # Normal call without target
        _, code_normal = transpile("click", "increment", {"amount": 1})
        assert "_target" not in code_normal

        # Call with _target
        _, code_with_target = transpile("click", "increment", {"amount": 1, "_target": "counter-1"})
        assert "_target" in code_with_target
        assert "counter-1" in code_with_target

    def test_on_tag_myself_parameter(self):
        """Test that {% on %} tag accepts myself parameter."""
        from unittest.mock import MagicMock

        from django.template import Context, Template

        # Create a mock component in context
        mock_component = MagicMock()
        mock_component.id = "test-live-1"
        mock_component._name = "TestLive"
        mock_component.increment = MagicMock()  # Method exists

        # Render template with on tag using myself
        template = Template('{% load wireview %}{% on "click" "increment" myself=True %}')
        result = template.render(Context({"this": mock_component}))

        # Should include _target with component ID
        assert "_target" in result
        assert "test-live-1" in result

    def test_on_tag_without_myself(self):
        """Test that {% on %} without myself doesn't add _target."""
        from unittest.mock import MagicMock

        from django.template import Context, Template

        mock_component = MagicMock()
        mock_component.id = "test-comp-1"
        mock_component._name = "TestComp"
        mock_component.increment = MagicMock()

        template = Template('{% load wireview %}{% on "click" "increment" %}')
        result = template.render(Context({"this": mock_component}))

        # Should NOT include _target
        assert "_target" not in result


@pytest.mark.unit
class TestFlushPendingLiveComponents:
    """Test flush_pending_live_components for joined() calls."""

    @pytest.mark.asyncio
    async def test_new_live_component_added_to_pending(self):
        """Test that newly created LiveComponents are added to pending list."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=True, user=AnonymousUser())

        # Build a LiveComponent
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        # Should be in pending list
        assert len(repo._pending_live_components) == 1
        assert repo._pending_live_components[0].id == "counter-1"

    @pytest.mark.asyncio
    async def test_flush_pending_calls_joined(self):
        """Test that flush_pending_live_components calls joined()."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        # Create a LiveComponent class that tracks joined() calls
        joined_calls = []

        class TrackedCounter(LiveComponent):
            _template_name = "live_components/counter.html"
            count: int = 0

            async def joined(self):
                joined_calls.append(self.id)

        repo = ComponentRepository(is_live=True, user=AnonymousUser())

        # Build a LiveComponent
        repo.build_live_component(
            name="TrackedCounter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        # joined() not called yet
        assert len(joined_calls) == 0

        # Flush pending
        pending = await repo.flush_pending_live_components()

        # Should have called joined()
        assert len(pending) == 1
        assert len(joined_calls) == 1
        assert joined_calls[0] == "counter-1"

    @pytest.mark.asyncio
    async def test_flush_pending_clears_list(self):
        """Test that flush_pending_live_components clears the pending list."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=True, user=AnonymousUser())

        # Build a LiveComponent
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        # Flush pending
        await repo.flush_pending_live_components()

        # Pending list should be empty
        assert len(repo._pending_live_components) == 0

    @pytest.mark.asyncio
    async def test_existing_component_not_added_to_pending(self):
        """Test that existing LiveComponents are not added to pending on re-render."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        repo = ComponentRepository(is_live=True, user=AnonymousUser())

        # First build
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 10},
            parent_id="dashboard-1",
        )

        # Flush pending
        await repo.flush_pending_live_components()

        # Re-build (simulating re-render)
        repo.build_live_component(
            name="Counter",
            state={"id": "counter-1", "count": 20},
            parent_id="dashboard-1",
        )

        # Should not be in pending list for joined() (already exists)
        assert len(repo._pending_live_components) == 0
        # But should be in pending updates
        assert len(repo._pending_updates) == 1

    @pytest.mark.asyncio
    async def test_existing_component_update_called_on_rerender(self):
        """Test that update() is called when existing component re-renders with new props."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.repository import ComponentRepository

        # Track update() calls
        update_calls = []

        class TrackedCounter(LiveComponent):
            _template_name = "live_components/counter.html"
            count: int = 0
            label: str = "Count"

            async def update(self, **assigns):
                update_calls.append((self.id, assigns.copy()))
                await super().update(**assigns)

        repo = ComponentRepository(is_live=True, user=AnonymousUser())

        # First build
        live_comp = repo.build_live_component(
            name="TrackedCounter",
            state={"id": "counter-1", "count": 10, "label": "Initial"},
            parent_id="dashboard-1",
        )

        # Flush to call joined()
        await repo.flush_pending_live_components()
        assert len(update_calls) == 0

        # Re-build with changed props
        repo.build_live_component(
            name="TrackedCounter",
            state={"id": "counter-1", "count": 20, "label": "Updated"},
            parent_id="dashboard-1",
        )

        # Flush to call update()
        await repo.flush_pending_live_components()

        # update() should have been called with new props
        assert len(update_calls) == 1
        assert update_calls[0][0] == "counter-1"
        assert update_calls[0][1] == {"count": 20, "label": "Updated"}

        # Props should be updated
        assert live_comp.count == 20
        assert live_comp.label == "Updated"


@pytest.mark.unit
class TestSendUpdate:
    """Test send_update from parent to LiveComponent."""

    @pytest.mark.asyncio
    async def test_send_update_calls_wire(self):
        """Test that send_update sends through wire."""
        from unittest.mock import AsyncMock

        # Create a parent component
        view = await mount(Counter, count=0)

        # Mock wire.send
        view.component.wire.send = AsyncMock()

        # Call send_update
        await view.component.send_update("counter-1", count=10)

        # Verify wire.send was called with correct args
        view.component.wire.send.assert_called_once_with(
            "update_live_component",
            parent_id=view.component.id,
            live_component_id="counter-1",
            assigns={"count": 10},
        )

    @pytest.mark.asyncio
    async def test_send_update_with_multiple_assigns(self):
        """Test send_update with multiple values."""
        from unittest.mock import AsyncMock

        view = await mount(Counter, count=0)
        view.component.wire.send = AsyncMock()

        await view.component.send_update("counter-1", count=10, label="New Label")

        call_args = view.component.wire.send.call_args
        assert call_args[1]["assigns"] == {"count": 10, "label": "New Label"}
