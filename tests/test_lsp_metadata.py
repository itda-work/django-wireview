"""Tests for the wireview_lsp management command."""

import json
from io import StringIO

import pytest
from django.core.management import call_command


@pytest.mark.unit
class TestWireviewLspCommand:
    """Test the wireview_lsp management command."""

    def test_outputs_valid_json(self):
        """Command should output valid JSON."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        output = out.getvalue()

        # Should be valid JSON
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_metadata_structure(self):
        """Output should have the expected structure."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Check top-level structure
        assert "version" in data
        assert "generated_at" in data
        assert "components" in data
        assert "modifiers" in data

        # Check version format
        assert data["version"] == "1.0"

    def test_component_metadata(self):
        """Component metadata should include all expected fields."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Should have at least one component (XTodoList from testproj)
        assert len(data["components"]) > 0

        # Check first component structure
        component = list(data["components"].values())[0]
        expected_fields = [
            "name",
            "fqn",
            "app_key",
            "module",
            "file_path",
            "line_number",
            "docstring",
            "template_name",
            "fields",
            "methods",
            "slots",
            "subscriptions",
            "subscriptions_is_dynamic",
            "temporary_assigns",
        ]
        for field in expected_fields:
            assert field in component, f"Missing field: {field}"

    def test_field_metadata(self):
        """Field metadata should include type and default information."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Find a component with fields
        for component in data["components"].values():
            if component["fields"]:
                field = list(component["fields"].values())[0]
                assert "type" in field
                assert "annotation" in field
                assert "default" in field
                assert "required" in field
                break

    def test_method_metadata(self):
        """Method metadata should include async flag and parameters."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Find a component with methods
        for component in data["components"].values():
            if component["methods"]:
                method = list(component["methods"].values())[0]
                assert "is_async" in method
                assert "parameters" in method
                assert "docstring" in method
                assert "line_number" in method
                break

    def test_modifiers_included(self):
        """Event modifiers should be included in output."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        modifiers = data["modifiers"]
        assert len(modifiers) > 0

        # Check some expected modifiers
        expected_modifiers = ["prevent", "stop", "debounce", "throttle", "enter", "ctrl"]
        for mod in expected_modifiers:
            assert mod in modifiers, f"Missing modifier: {mod}"

        # Check modifier structure
        modifier = modifiers["debounce"]
        assert "description" in modifier
        assert "has_argument" in modifier
        assert modifier["has_argument"] is True

    def test_dynamic_subscriptions_flag(self):
        """Components with @property _subscriptions should be flagged."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # XTodoItem has dynamic subscriptions
        if "XTodoItem" in data["components"]:
            component = data["components"]["XTodoItem"]
            assert component["subscriptions_is_dynamic"] is True

    def test_pretty_output(self):
        """--pretty flag should format JSON output."""
        out = StringIO()
        call_command("wireview_lsp", "--pretty", stdout=out)
        output = out.getvalue()

        # Pretty output should have newlines and indentation
        assert "\n" in output
        assert "  " in output  # Indentation

    def test_component_fqn_format(self):
        """FQN should be in module.ClassName format."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        for name, component in data["components"].items():
            # FQN should contain module and class name
            assert "." in component["fqn"]
            assert component["fqn"].endswith(component["name"])

    def test_app_key_format(self):
        """app_key should be in app:ClassName format."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        for name, component in data["components"].items():
            # app_key should have colon separator
            assert ":" in component["app_key"]
            parts = component["app_key"].split(":")
            assert len(parts) == 2
            assert parts[1] == component["name"]
