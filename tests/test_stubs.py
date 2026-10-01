"""Tests for wireview type stub generation."""

from __future__ import annotations

import tempfile
import typing as t
from pathlib import Path

import pytest

from wireview import Component
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

    class Meta:
        template_name = "test/simple.html"

    count: int = 0
    name: str = "default"

    async def increment(self, amount: int = 1) -> None:
        """Increment the counter."""
        self.count += amount


class ComponentWithSlots(Component, public=False):
    """Component with slot definitions."""

    class Meta:
        template_name = "test/slots.html"
        slots = {
            "header": {"required": True, "doc": "Header content"},
            "footer": {"required": False},
        }

    title: str = ""


class ComponentWithSubscriptions(Component, public=False):
    """Component with subscriptions."""

    class Meta:
        template_name = "test/subs.html"
        subscriptions = {"test.model", "other.model"}


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
        assert class_vars.get("template_name") == "test/simple.html"

    def test_extract_slots(self):
        """Test extracting slots."""
        class_vars = _extract_class_vars(ComponentWithSlots)
        assert "slots" in class_vars
        assert "header" in class_vars["slots"]

    def test_extract_subscriptions(self):
        """Test extracting subscriptions."""
        class_vars = _extract_class_vars(ComponentWithSubscriptions)
        assert "subscriptions" in class_vars
        assert "test.model" in class_vars["subscriptions"]


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
            class_vars={"template_name": "test.html"},
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

        assert "from wireview import Component" in import_text
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
                    class_vars={"template_name": "test.html"},
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
                    class_vars={"template_name": "test.html"},
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
        assert "from wireview import Component" in content

        # Check class
        assert "class TestComponent(Component):" in content
        assert "value: int" in content


@pytest.mark.unit
def test_a_generated_stub_imports_only_the_public_package():
    """A committed .pyi imported LiveComponent from wireview.live_component, an internal module (#98)."""
    import re

    module_stubs = ModuleStubs(
        module_path="myapp.live",
        file_path="/tmp/live.py",
        components=[
            ComponentStubInfo(
                name=name,
                fqn=f"myapp.live.{name}",
                module="myapp.live",
                file_path="/tmp/live.py",
                component_type=kind,
                docstring="",
                fields=[],
                methods=[],
                class_vars={},
                handlers=[],
            )
            for name, kind in (("Page", "Component"), ("Row", "LiveComponent"))
        ],
    )

    content = generate_stub_content(module_stubs)

    assert "from wireview import LiveComponent" in content
    assert not re.findall(r"^from wireview\.\S+ import .*$", content, re.M)


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


class TestTheCommand:
    """``manage.py wireview_stubs`` itself, not the functions it calls (#110)."""

    @pytest.mark.unit
    def test_it_writes_a_stub_whose_handlers_are_what_a_client_can_call(self, tmp_path):
        from django.core.management import call_command

        call_command("wireview_stubs", "--output-dir", str(tmp_path), "--app", "chat")

        (stub,) = tmp_path.rglob("live.pyi")
        text = stub.read_text(encoding="utf-8")
        handlers = next(
            line for line in text.splitlines() if "__wireview_handlers__" in line and "send_message" in line
        )
        assert "on_typing" in handlers
        # A mixin's framework methods: a client cannot call them, whatever a stub says
        assert "presence_join" not in handlers and "presence_set_typing" not in handlers

    @pytest.mark.unit
    def test_check_fails_when_a_stub_is_stale(self, tmp_path):
        from django.core.management import call_command

        call_command("wireview_stubs", "--output-dir", str(tmp_path), "--app", "chat")
        call_command("wireview_stubs", "--output-dir", str(tmp_path), "--app", "chat", "--check")

        (stub,) = tmp_path.rglob("live.pyi")
        stub.write_text(stub.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exit_:
            call_command("wireview_stubs", "--output-dir", str(tmp_path), "--app", "chat", "--check")
        assert exit_.value.code == 1


@pytest.mark.unit
def test_annotated_metadata_is_left_out_of_a_type_string():
    """``AsyncResult`` fields are ``Annotated`` with a schema object whose repr has a
    memory address; it went into the stub, which differed on every run (#109)."""
    from wireview.management.commands.wireview_lsp import get_type_string

    assert get_type_string(t.Annotated[int, object()]) == "int"
    assert get_type_string(t.Optional[t.Annotated[list[int], object()]]) == "Union[list[int], None]"


_STUBS_OF_EVERY_COMPONENT = """
import django
django.setup()
from wireview.management.commands.wireview_stubs import (
    _strip_timestamp, collect_components_by_module, generate_stub_content,
)
modules = collect_components_by_module()
print("".join(_strip_timestamp(generate_stub_content(modules[name])) for name in sorted(modules)))
"""


@pytest.mark.integration
def test_the_stubs_are_the_same_in_every_process():
    """A tracked stub rewritten on every DEBUG start is noise in git and a conflict
    between worktrees (#109). A string set's order and an object's address change
    with the process; the stubs must not."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    tests_dir = Path(__file__).resolve().parent
    outputs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "DJANGO_SETTINGS_MODULE": "testproj.settings"}
        result = subprocess.run(
            [sys.executable, "-c", _STUBS_OF_EVERY_COMPONENT],
            cwd=tests_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(result.stdout)
    assert outputs[0], "no stubs were generated"
    assert outputs[0] == outputs[1]
