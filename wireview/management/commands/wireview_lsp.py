"""Django management command to extract wireview component metadata for IDE LSP support.

This command introspects all registered wireview components and outputs their metadata
as JSON, which can be used by IDE plugins (VSCode, Neovim, etc.) to provide
autocompletion, go-to-definition, and hover documentation.

Usage:
    python manage.py wireview_lsp
    python manage.py wireview_lsp --output=.wireview/metadata.json
    python manage.py wireview_lsp --pretty
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import typing as t
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from wireview.component import Component
from wireview.event_transpiler import Modifiers


class Command(BaseCommand):
    """Extract wireview component metadata for IDE LSP support."""

    help = "Extract wireview component metadata for IDE LSP support"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            help="Output file path (default: stdout)",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty print JSON output",
        )

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        metadata = extract_metadata()

        if options["pretty"]:
            output = json.dumps(metadata, indent=2, ensure_ascii=False)
        else:
            output = json.dumps(metadata, ensure_ascii=False)

        output_path = options.get("output")
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Metadata written to {output_path}"))
        else:
            self.stdout.write(output)


def is_dynamic_property(cls: type, attr_name: str) -> bool:
    """Check if an attribute is defined as a property (dynamic)."""
    for klass in cls.__mro__:
        if attr_name in klass.__dict__:
            return isinstance(klass.__dict__[attr_name], property)
    return False


def get_class_attribute_safe(cls: type, attr_name: str, default: t.Any = None) -> t.Any:
    """Safely get a class attribute, handling properties gracefully.

    If the attribute is a property, returns the default value since
    we can't evaluate it without an instance.
    """
    if is_dynamic_property(cls, attr_name):
        return default

    return getattr(cls, attr_name, default)


def extract_metadata() -> dict[str, t.Any]:
    """Extract metadata from all registered components."""
    components: dict[str, dict[str, t.Any]] = {}

    for name, cls in Component._all.items():
        try:
            components[name] = extract_component_metadata(cls)
        except Exception as e:
            # Log error but continue processing other components
            sys.stderr.write(f"Warning: Failed to extract metadata for {name}: {e}\n")

    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "modifiers": extract_modifiers(),
    }


def extract_component_metadata(cls: type[Component]) -> dict[str, t.Any]:
    """Extract metadata from a single component class."""
    # Get source file and line number
    try:
        file_path = inspect.getfile(cls)
        source_lines, line_number = inspect.getsourcelines(cls)
    except (TypeError, OSError):
        file_path = ""
        line_number = 0

    # Extract app name from module path
    module = cls.__module__
    app_name = module.split(".")[0]

    # Handle _subscriptions (may be class var or property)
    subscriptions = get_class_attribute_safe(cls, "_subscriptions", set())

    # Handle _temporary_assigns
    temporary_assigns = get_class_attribute_safe(cls, "_temporary_assigns", set())

    return {
        "name": cls._name,
        "fqn": cls._fqn,
        "app_key": f"{app_name}:{cls._name}",
        "module": module,
        "file_path": file_path,
        "line_number": line_number,
        "docstring": inspect.getdoc(cls),
        "template_name": getattr(cls, "_template_name", ""),
        "fields": extract_fields(cls),
        "methods": extract_methods(cls),
        "slots": getattr(cls, "_slots", {}),
        "subscriptions": list(subscriptions) if not callable(subscriptions) else [],
        "subscriptions_is_dynamic": is_dynamic_property(cls, "_subscriptions"),
        "temporary_assigns": list(temporary_assigns) if not callable(temporary_assigns) else [],
    }


def extract_fields(cls: type[Component]) -> dict[str, dict[str, t.Any]]:
    """Extract Pydantic field information from a component class."""
    fields: dict[str, dict[str, t.Any]] = {}

    # Skip internal fields
    internal_fields = {"id", "user", "wire"}
    exclude_fields = getattr(cls, "_exclude_fields", set())
    skip_fields = internal_fields | exclude_fields

    for name, field_info in cls.model_fields.items():
        if name in skip_fields:
            continue

        # Get type annotation as string
        annotation = field_info.annotation
        type_str = get_type_string(annotation)

        # Get default value
        default = field_info.default
        default_value = serialize_default(default)

        fields[name] = {
            "type": type_str,
            "annotation": str(annotation) if annotation else None,
            "default": default_value,
            "required": field_info.is_required(),
            "description": field_info.description,
        }

    return fields


def extract_methods(cls: type[Component]) -> dict[str, dict[str, t.Any]]:
    """Extract public async methods (event handlers) from a component class."""
    methods: dict[str, dict[str, t.Any]] = {}

    # Get all class attributes
    for name in dir(cls):
        # Skip private and dunder methods
        if name.startswith("_"):
            continue

        # Skip non-lowercase names (not event handlers)
        if not name.islower():
            continue

        try:
            attr = getattr(cls, name)
        except AttributeError:
            continue

        # Only include callable attributes
        if not callable(attr):
            continue

        # Check if it's a coroutine function (async method)
        # Need to unwrap validate_call decorator if present
        original_func = getattr(attr, "__wrapped__", attr)
        is_async = asyncio.iscoroutinefunction(original_func)

        # Get signature
        try:
            sig = inspect.signature(original_func)
            parameters = extract_parameters(sig)
        except (ValueError, TypeError):
            parameters = {}

        # Get docstring
        docstring = inspect.getdoc(original_func)

        # Get source location
        try:
            source_lines, method_line = inspect.getsourcelines(original_func)
        except (TypeError, OSError):
            method_line = 0

        methods[name] = {
            "is_async": is_async,
            "parameters": parameters,
            "docstring": docstring,
            "line_number": method_line,
        }

    return methods


def extract_parameters(sig: inspect.Signature) -> dict[str, dict[str, t.Any]]:
    """Extract parameter information from a function signature."""
    params: dict[str, dict[str, t.Any]] = {}

    for name, param in sig.parameters.items():
        # Skip self parameter
        if name == "self":
            continue

        # Get type annotation
        if param.annotation != inspect.Parameter.empty:
            type_str = get_type_string(param.annotation)
        else:
            type_str = None

        # Get default value
        if param.default != inspect.Parameter.empty:
            default = serialize_default(param.default)
            has_default = True
        else:
            default = None
            has_default = False

        params[name] = {
            "type": type_str,
            "default": default,
            "has_default": has_default,
            "kind": param.kind.name,
        }

    return params


def extract_modifiers() -> dict[str, dict[str, t.Any]]:
    """Extract available event modifiers from the Modifiers class."""
    modifiers: dict[str, dict[str, t.Any]] = {}

    # Get all public methods from Modifiers class
    for name in dir(Modifiers):
        if name.startswith("_"):
            continue

        attr = getattr(Modifiers, name)
        if callable(attr):
            docstring = inspect.getdoc(attr)

            # Determine modifier type and provide description
            description = get_modifier_description(name)

            modifiers[name] = {
                "docstring": docstring,
                "description": description,
                "has_argument": name in ("debounce", "throttle", "key", "key_code"),
            }

    return modifiers


def get_modifier_description(name: str) -> str:
    """Get a human-readable description for a modifier."""
    descriptions = {
        "prevent": "Calls event.preventDefault()",
        "stop": "Calls event.stopPropagation()",
        "debounce": "Debounce the event handler (requires delay in ms)",
        "throttle": "Throttle the event handler (requires delay in ms)",
        "ctrl": "Only trigger if Ctrl key is pressed",
        "alt": "Only trigger if Alt key is pressed",
        "shift": "Only trigger if Shift key is pressed",
        "meta": "Only trigger if Meta (Cmd/Win) key is pressed",
        "key": "Only trigger for specific key (requires key name)",
        "key_code": "Only trigger for specific keyCode (requires code)",
        "enter": "Only trigger on Enter key",
        "tab": "Only trigger on Tab key",
        "delete": "Only trigger on Delete key",
        "backspace": "Only trigger on Backspace key",
        "esc": "Only trigger on Escape key",
        "space": "Only trigger on Space key",
        "up": "Only trigger on Arrow Up key",
        "down": "Only trigger on Arrow Down key",
        "left": "Only trigger on Arrow Left key",
        "right": "Only trigger on Arrow Right key",
    }
    return descriptions.get(name, "")


def get_type_string(annotation: t.Any) -> str:
    """Convert a type annotation to a readable string."""
    if annotation is None:
        return "None"

    # Handle None type
    if annotation is type(None):
        return "None"

    # Handle basic types
    if isinstance(annotation, type):
        return annotation.__name__

    # Handle typing module types
    origin = t.get_origin(annotation)
    if origin is not None:
        args = t.get_args(annotation)
        origin_name = getattr(origin, "__name__", str(origin))

        if args:
            args_str = ", ".join(get_type_string(arg) for arg in args)
            return f"{origin_name}[{args_str}]"
        return origin_name

    # Fallback to string representation
    return str(annotation)


def serialize_default(value: t.Any) -> t.Any:
    """Serialize a default value to JSON-compatible format."""
    from pydantic_core import PydanticUndefinedType

    # Handle PydanticUndefined
    if isinstance(value, PydanticUndefinedType):
        return None

    # Handle None
    if value is None:
        return None

    # Handle basic JSON-compatible types
    if isinstance(value, (bool, int, float, str)):
        return value

    # Handle empty collections
    if isinstance(value, (list, dict, tuple, set)):
        if not value:
            if isinstance(value, list):
                return []
            if isinstance(value, dict):
                return {}
            if isinstance(value, tuple):
                return []
            if isinstance(value, set):
                return []
        # Non-empty collections - return string representation
        return repr(value)

    # Handle callable defaults (factory functions)
    if callable(value):
        return f"<factory: {value.__name__}>"

    # Fallback to string representation
    return repr(value)
