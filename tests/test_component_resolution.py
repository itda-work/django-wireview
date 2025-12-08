"""Tests for component resolution by FQN and app prefix (Issue #53).

This feature allows components to be resolved by:
1. Simple name (e.g., 'Counter') - backward compatible
2. App prefix (e.g., 'myapp:Counter')
3. FQN (e.g., 'myapp.live.Counter')
"""

import warnings

import pytest

from wireview import Component
from wireview.core.component import ComponentNotFound


# Test components defined in this module
class PrivateCounter(Component, public=False):
    """Base test counter - not registered in registry."""

    _template_name = "todo/counter.html"
    count: int = 0


class ResolveTestComponent(Component):
    """Component for testing resolution."""

    _template_name = "todo/counter.html"
    value: int = 0


class AnotherTestComponent(Component, name="CustomName"):
    """Component with custom name for testing."""

    _template_name = "todo/counter.html"
    data: str = ""


@pytest.mark.unit
class TestComponentRegistration:
    """Test component registration in multiple registries."""

    def test_registers_in_all_registry(self):
        """Component is registered by simple name."""
        assert "ResolveTestComponent" in Component._all
        assert Component._all["ResolveTestComponent"] is ResolveTestComponent

    def test_registers_in_fqn_registry(self):
        """Component is registered by FQN."""
        fqn = f"{ResolveTestComponent.__module__}.ResolveTestComponent"
        assert fqn in Component._by_fqn
        assert Component._by_fqn[fqn] is ResolveTestComponent

    def test_registers_in_app_registry(self):
        """Component is registered by app prefix."""
        app_name = ResolveTestComponent.__module__.split(".")[0]
        app_key = f"{app_name}:ResolveTestComponent"
        assert app_key in Component._by_app
        assert Component._by_app[app_key] is ResolveTestComponent

    def test_custom_name_registration(self):
        """Component with custom name is registered correctly."""
        assert "CustomName" in Component._all
        assert Component._all["CustomName"] is AnotherTestComponent

        # FQN uses custom name
        fqn = f"{AnotherTestComponent.__module__}.CustomName"
        assert fqn in Component._by_fqn

    def test_fqn_attribute(self):
        """Component has correct _fqn attribute."""
        expected_fqn = f"{ResolveTestComponent.__module__}.ResolveTestComponent"
        assert ResolveTestComponent._fqn == expected_fqn

    def test_public_false_not_registered(self):
        """Component with public=False is not registered."""
        assert "PrivateCounter" not in Component._all


@pytest.mark.unit
class TestComponentResolve:
    """Test Component._resolve() method."""

    def test_resolve_by_simple_name(self):
        """Resolve component by simple class name."""
        result = Component._resolve("ResolveTestComponent")
        assert result is ResolveTestComponent

    def test_resolve_by_custom_name(self):
        """Resolve component by custom name."""
        result = Component._resolve("CustomName")
        assert result is AnotherTestComponent

    def test_resolve_by_fqn(self):
        """Resolve component by fully qualified name."""
        fqn = f"{ResolveTestComponent.__module__}.ResolveTestComponent"
        result = Component._resolve(fqn)
        assert result is ResolveTestComponent

    def test_resolve_by_app_prefix(self):
        """Resolve component by app:Name format."""
        app_name = ResolveTestComponent.__module__.split(".")[0]
        app_key = f"{app_name}:ResolveTestComponent"
        result = Component._resolve(app_key)
        assert result is ResolveTestComponent

    def test_resolve_not_found(self):
        """Raise ComponentNotFound for unknown component."""
        with pytest.raises(ComponentNotFound) as exc_info:
            Component._resolve("NonExistentComponent")
        assert "NonExistentComponent" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    def test_resolve_not_found_with_colon(self):
        """Raise ComponentNotFound for unknown app:Name."""
        with pytest.raises(ComponentNotFound):
            Component._resolve("unknownapp:UnknownComponent")

    def test_fqn_takes_precedence(self):
        """FQN lookup is checked before simple name."""
        # This test verifies the resolution order
        fqn = f"{ResolveTestComponent.__module__}.ResolveTestComponent"

        # Both should resolve to the same component
        by_fqn = Component._resolve(fqn)
        by_name = Component._resolve("ResolveTestComponent")
        assert by_fqn is by_name


@pytest.mark.unit
class TestNameCollisionWarning:
    """Test warning when components have the same name."""

    def test_no_warning_for_unique_name(self):
        """No warning for component with unique name."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            class UniqueNameComponent(Component):
                _template_name = "todo/counter.html"

            # Should not have any warnings about conflicts
            conflict_warnings = [warning for warning in w if "conflicts" in str(warning.message)]
            assert len(conflict_warnings) == 0

    def test_warning_for_duplicate_name(self):
        """Warning when component name conflicts with existing."""

        # First, create a component
        class DuplicateTestComponent(Component):
            _template_name = "todo/counter.html"
            original: bool = True

        # Now create another with the same name - should warn
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            class DuplicateTestComponent(Component):  # noqa: F811
                _template_name = "todo/counter.html"
                original: bool = False

            # Check for conflict warning
            conflict_warnings = [
                warning
                for warning in w
                if "DuplicateTestComponent" in str(warning.message) and "conflicts" in str(warning.message)
            ]
            assert len(conflict_warnings) == 1
            assert "FQN or app prefix" in str(conflict_warnings[0].message)
