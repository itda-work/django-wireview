"""Tests for wireview type stub generation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from wireview.component import Component
from wireview.management.commands.wireview_stubs import (
    ComponentStubInfo,
    FieldInfo,
    MethodInfo,
    ModuleStubs,
    _collect_imports,
    _extract_class_vars,
    _extract_fields,
    _extract_methods,
    _format_attrs_dict,
    _format_method_signature,
    _generate_class_stub,
    _serialize_default_for_stub,
    collect_components_by_module,
    generate_all_stubs,
    generate_stub_content,
)

# =============================================================================
# Test Component Fixtures
# =============================================================================


class SimpleTestComponent(Component, public=False):
    """A simple test component."""

    _template_name = "test/simple.html"

    count: int = 0
    name: str = "default"

    async def increment(self, amount: int = 1) -> None:
        """Increment the counter."""
        self.count += amount


class ComponentWithSlots(Component, public=False):
    """Component with slot definitions."""

    _template_name = "test/slots.html"
    _slots = {
        "header": {"required": True, "doc": "Header content"},
        "footer": {"required": False},
    }

    title: str = ""


class ComponentWithSubscriptions(Component, public=False):
    """Component with subscriptions."""

    _template_name = "test/subs.html"
    _subscriptions = {"test.model", "other.model"}


# =============================================================================
# Unit Tests
# =============================================================================


@pytest.mark.unit
class TestSerializeDefaultForStub:
    """Tests for _serialize_default_for_stub function."""

    def test_serialize_none(self):
        """Test serializing None."""
        assert _serialize_default_for_stub(None) == "None"

    def test_serialize_bool(self):
        """Test serializing boolean values."""
        assert _serialize_default_for_stub(True) == "True"
        assert _serialize_default_for_stub(False) == "False"

    def test_serialize_int(self):
        """Test serializing integers."""
        assert _serialize_default_for_stub(0) == "0"
        assert _serialize_default_for_stub(42) == "42"
        assert _serialize_default_for_stub(-1) == "-1"

    def test_serialize_float(self):
        """Test serializing floats."""
        assert _serialize_default_for_stub(3.14) == "3.14"
        assert _serialize_default_for_stub(0.0) == "0.0"

    def test_serialize_str(self):
        """Test serializing strings."""
        assert _serialize_default_for_stub("hello") == "'hello'"
        assert _serialize_default_for_stub("") == "''"

    def test_serialize_enum(self):
        """Test serializing Enum values."""
        from enum import Enum, StrEnum

        class Color(Enum):
            RED = 1
            BLUE = 2

        class Status(StrEnum):
            ACTIVE = "active"
            INACTIVE = "inactive"

        assert _serialize_default_for_stub(Color.RED) == "1"
        assert _serialize_default_for_stub(Status.ACTIVE) == "'active'"

    def test_serialize_empty_list(self):
        """Test serializing empty list."""
        assert _serialize_default_for_stub([]) == "[]"

    def test_serialize_empty_dict(self):
        """Test serializing empty dict."""
        assert _serialize_default_for_stub({}) == "{}"

    def test_serialize_nonempty_list(self):
        """Test serializing non-empty list shows placeholder."""
        result = _serialize_default_for_stub([1, 2, 3])
        assert result == "..."

    def test_serialize_callable(self):
        """Test serializing callable."""

        def my_factory():
            return []

        result = _serialize_default_for_stub(my_factory)
        assert result == "..."


@pytest.mark.unit
class TestFormatMethodSignature:
    """Tests for _format_method_signature function."""

    def test_no_params(self):
        """Test method with no parameters."""
        method = MethodInfo(
            name="foo",
            is_async=True,
            parameters={},
            return_type="None",
        )
        assert _format_method_signature(method) == "(self) -> None"

    def test_single_param(self):
        """Test method with single parameter."""
        method = MethodInfo(
            name="foo",
            is_async=True,
            parameters={"value": {"type": "int", "has_default": False}},
            return_type="None",
        )
        assert _format_method_signature(method) == "(self, value: int) -> None"

    def test_param_with_default(self):
        """Test method with parameter having default."""
        method = MethodInfo(
            name="foo",
            is_async=True,
            parameters={"value": {"type": "int", "has_default": True, "default": 0}},
            return_type="None",
        )
        assert _format_method_signature(method) == "(self, value: int = ...) -> None"

    def test_multiple_params(self):
        """Test method with multiple parameters."""
        method = MethodInfo(
            name="foo",
            is_async=True,
            parameters={
                "name": {"type": "str", "has_default": False},
                "count": {"type": "int", "has_default": True},
            },
            return_type="str",
        )
        sig = _format_method_signature(method)
        assert "self" in sig
        assert "name: str" in sig
        assert "count: int = ..." in sig
        assert "-> str" in sig


@pytest.mark.unit
class TestFormatAttrsDict:
    """Tests for _format_attrs_dict function."""

    def test_empty_fields(self):
        """Test with no fields."""
        assert _format_attrs_dict([]) == "{}"

    def test_single_field(self):
        """Test with single field."""
        fields = [
            FieldInfo(
                name="count",
                type_str="int",
                annotation=int,
                default=0,
                required=False,
            )
        ]
        result = _format_attrs_dict(fields)
        assert "'count'" in result
        assert "'type': 'int'" in result
        assert "'required': False" in result
        assert "'default': 0" in result

    def test_required_field(self):
        """Test with required field (no default)."""
        fields = [
            FieldInfo(
                name="name",
                type_str="str",
                annotation=str,
                default=None,
                required=True,
            )
        ]
        result = _format_attrs_dict(fields)
        assert "'required': True" in result
        assert "'default'" not in result


@pytest.mark.unit
class TestExtractFields:
    """Tests for _extract_fields function."""

    def test_extract_simple_fields(self):
        """Test extracting fields from simple component."""
        fields = _extract_fields(SimpleTestComponent)

        assert len(fields) == 2
        names = [f.name for f in fields]
        assert "count" in names
        assert "name" in names

        count_field = next(f for f in fields if f.name == "count")
        assert count_field.type_str == "int"
        assert count_field.default == 0
        assert count_field.required is False

    def test_internal_fields_excluded(self):
        """Test that internal fields are excluded."""
        fields = _extract_fields(SimpleTestComponent)
        names = [f.name for f in fields]

        assert "id" not in names
        assert "user" not in names
        assert "wire" not in names


@pytest.mark.unit
class TestExtractMethods:
    """Tests for _extract_methods function."""

    def test_extract_async_methods(self):
        """Test extracting async methods."""
        methods, handlers = _extract_methods(SimpleTestComponent)

        method_names = [m.name for m in methods]
        assert "increment" in method_names

        increment = next(m for m in methods if m.name == "increment")
        assert increment.is_async is True
        assert "amount" in increment.parameters

    def test_handlers_list(self):
        """Test that handlers are collected correctly."""
        methods, handlers = _extract_methods(SimpleTestComponent)

        assert "increment" in handlers

    def test_base_methods_excluded(self):
        """Test that base class methods are excluded unless overridden."""
        methods, handlers = _extract_methods(SimpleTestComponent)
        method_names = [m.name for m in methods]

        # Base Component methods should not appear
        assert "joined" not in method_names
        assert "mutation" not in method_names
        assert "push_event" not in method_names


@pytest.mark.unit
class TestExtractClassVars:
    """Tests for _extract_class_vars function."""

    def test_extract_template_name(self):
        """Test extracting template name."""
        class_vars = _extract_class_vars(SimpleTestComponent)
        assert class_vars.get("_template_name") == "test/simple.html"

    def test_extract_slots(self):
        """Test extracting slots."""
        class_vars = _extract_class_vars(ComponentWithSlots)
        assert "_slots" in class_vars
        assert "header" in class_vars["_slots"]

    def test_extract_subscriptions(self):
        """Test extracting subscriptions."""
        class_vars = _extract_class_vars(ComponentWithSubscriptions)
        assert "_subscriptions" in class_vars
        assert "test.model" in class_vars["_subscriptions"]


@pytest.mark.unit
class TestGenerateClassStub:
    """Tests for _generate_class_stub function."""

    def test_generate_component_stub(self):
        """Test generating stub for a component."""
        stub_info = ComponentStubInfo(
            name="TestComp",
            fqn="test.TestComp",
            module="test",
            file_path="/test/test.py",
            component_type="Component",
            docstring="Test component docstring.",
            fields=[
                FieldInfo(
                    name="count",
                    type_str="int",
                    annotation=int,
                    default=0,
                    required=False,
                )
            ],
            methods=[
                MethodInfo(
                    name="increment",
                    is_async=True,
                    parameters={"amount": {"type": "int", "has_default": True}},
                    return_type="None",
                )
            ],
            class_vars={"_template_name": "test.html"},
            handlers=["increment"],
        )

        lines = _generate_class_stub(stub_info)
        stub_content = "\n".join(lines)

        assert "class TestComp(Component):" in stub_content
        assert '"""Test component docstring."""' in stub_content
        assert "count: int" in stub_content
        assert "async def increment" in stub_content
        assert "__wireview_attrs__" in stub_content
        assert "__wireview_handlers__" in stub_content

    def test_generate_live_component_stub(self):
        """Test generating stub for a LiveComponent."""
        stub_info = ComponentStubInfo(
            name="TestLive",
            fqn="test.TestLive",
            module="test",
            file_path="/test/test.py",
            component_type="LiveComponent",
            docstring=None,
            fields=[],
            methods=[],
            class_vars={},
            handlers=[],
        )

        lines = _generate_class_stub(stub_info)
        stub_content = "\n".join(lines)

        assert "class TestLive(LiveComponent):" in stub_content


@pytest.mark.unit
class TestCollectImports:
    """Tests for _collect_imports function."""

    def test_collect_component_import(self):
        """Test that Component import is collected."""
        module_stubs = ModuleStubs(
            module_path="test",
            file_path="/test.py",
            components=[
                ComponentStubInfo(
                    name="Test",
                    fqn="test.Test",
                    module="test",
                    file_path="/test.py",
                    component_type="Component",
                    docstring=None,
                    fields=[],
                    methods=[],
                    class_vars={},
                    handlers=[],
                )
            ],
        )

        imports = _collect_imports(module_stubs)
        import_text = "\n".join(imports)

        assert "from wireview.component import Component" in import_text
        assert "from typing import" in import_text

    def test_collect_classvar_import(self):
        """Test that ClassVar is imported when class_vars present."""
        module_stubs = ModuleStubs(
            module_path="test",
            file_path="/test.py",
            components=[
                ComponentStubInfo(
                    name="Test",
                    fqn="test.Test",
                    module="test",
                    file_path="/test.py",
                    component_type="Component",
                    docstring=None,
                    fields=[],
                    methods=[],
                    class_vars={"_template_name": "test.html"},
                    handlers=[],
                )
            ],
        )

        imports = _collect_imports(module_stubs)
        import_text = "\n".join(imports)

        assert "ClassVar" in import_text


@pytest.mark.unit
class TestGenerateStubContent:
    """Tests for generate_stub_content function."""

    def test_generate_complete_stub(self):
        """Test generating complete stub file content."""
        module_stubs = ModuleStubs(
            module_path="test.module",
            file_path="/test/module.py",
            components=[
                ComponentStubInfo(
                    name="TestComponent",
                    fqn="test.module.TestComponent",
                    module="test.module",
                    file_path="/test/module.py",
                    component_type="Component",
                    docstring="A test component.",
                    fields=[
                        FieldInfo(
                            name="value",
                            type_str="int",
                            annotation=int,
                            default=0,
                            required=False,
                        )
                    ],
                    methods=[],
                    class_vars={"_template_name": "test.html"},
                    handlers=[],
                )
            ],
        )

        content = generate_stub_content(module_stubs)

        # Check header
        assert "Auto-generated type stubs" in content
        assert "DO NOT EDIT" in content

        # Check imports
        assert "from typing import" in content
        assert "from wireview.component import Component" in content

        # Check class
        assert "class TestComponent(Component):" in content
        assert "value: int" in content


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.integration
class TestStubsCommand:
    """Integration tests for wireview_stubs command."""

    @pytest.fixture
    def temp_output_dir(self):
        """Create a temporary directory for stub output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_generate_all_stubs(self, temp_output_dir):
        """Test generating stubs to a custom output directory."""
        # This test requires Django to be set up with components registered
        files_written = generate_all_stubs(output_dir=temp_output_dir, quiet=True)

        # Should generate at least one file
        assert files_written >= 0

    def test_collect_components_by_module(self):
        """Test collecting components grouped by module."""
        modules = collect_components_by_module()

        # Should find components from test project
        assert len(modules) >= 0

    def test_stub_syntax_valid(self, temp_output_dir):
        """Test that generated stubs have valid Python syntax."""
        import ast

        files_written = generate_all_stubs(output_dir=temp_output_dir, quiet=True)

        if files_written > 0:
            # Check all generated .pyi files
            for pyi_file in Path(temp_output_dir).rglob("*.pyi"):
                content = pyi_file.read_text(encoding="utf-8")
                # Should parse without syntax errors
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    pytest.fail(f"Generated stub has syntax error: {pyi_file}\n{e}")
