"""Django management command to generate type stubs (.pyi) for wireview components.

This command introspects all registered wireview components and generates
type stub files for IDE support and static type checking with mypy/pyright.

Usage:
    python manage.py wireview_stubs
    python manage.py wireview_stubs --dry-run
    python manage.py wireview_stubs --check
    python manage.py wireview_stubs --output-dir=./stubs
    python manage.py wireview_stubs --app myapp
"""

from __future__ import annotations

import asyncio
import collections.abc
import enum
import inspect
import sys
import types
import typing as t
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from wireview.component import Component
from wireview.function_component import FunctionComponent
from wireview.function_component import _registry as function_registry
from wireview.live_component import LiveComponent

# Reuse utilities from wireview_lsp
from .wireview_lsp import (
    extract_parameters,
    get_class_attribute_safe,
    get_type_string,
    is_dynamic_property,
    serialize_default,
)


@dataclass
class FieldInfo:
    """Information about a component field."""

    name: str
    type_str: str
    annotation: t.Any
    default: t.Any
    required: bool
    description: str | None = None


@dataclass
class MethodInfo:
    """Information about a component method."""

    name: str
    is_async: bool
    parameters: dict[str, dict[str, t.Any]]
    return_type: str | None = None
    docstring: str | None = None
    # The resolved return annotation; ``return_type`` is its display string.
    return_annotation: t.Any = inspect.Parameter.empty
    # "method", "classmethod" or "staticmethod": decides the decorator and first parameter.
    binding: str = "method"


@dataclass
class ComponentStubInfo:
    """Complete stub information for a component."""

    name: str
    fqn: str
    module: str
    file_path: str
    component_type: str  # "Component", "LiveComponent", "FunctionComponent"
    docstring: str | None
    fields: list[FieldInfo]
    methods: list[MethodInfo]
    class_vars: dict[str, t.Any]  # _template_name, _slots, etc.
    handlers: list[str]  # Event handler names


@dataclass
class ModuleStubs:
    """All component stubs for a single module."""

    module_path: str
    file_path: str
    components: list[ComponentStubInfo] = field(default_factory=list)


class Command(BaseCommand):
    """Generate type stubs (.pyi) for wireview components."""

    help = "Generate type stubs (.pyi) for wireview components"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output-dir",
            "-o",
            type=str,
            default=None,
            help="Output directory for stubs (default: next to source files)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be generated without writing files",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Check if stubs are up-to-date (CI mode, exit 1 if changes needed)",
        )
        parser.add_argument(
            "--app",
            type=str,
            action="append",
            dest="apps",
            help="Generate stubs only for specific apps (can be repeated)",
        )

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        verbosity = options.get("verbosity", 1)
        dry_run = options.get("dry_run", False)
        check_mode = options.get("check", False)
        output_dir = options.get("output_dir")
        app_filter = options.get("apps")

        # Collect all components grouped by module
        modules = collect_components_by_module(app_filter)

        if not modules:
            self.stdout.write(self.style.WARNING("No components found."))
            return

        if verbosity >= 2:
            total = sum(len(m.components) for m in modules.values())
            self.stdout.write(f"Found {total} components in {len(modules)} modules")

        # Generate stubs
        changes_needed = False
        files_written = 0

        for file_path, module_stubs in modules.items():
            stub_content = generate_stub_content(module_stubs)

            # Determine output path
            if output_dir:
                # Calculate relative path and place in output_dir
                source_path = Path(file_path)
                try:
                    # Try to get relative path from current working directory
                    rel_path = source_path.relative_to(Path.cwd())
                except ValueError:
                    # Use just the filename if not relative
                    rel_path = Path(source_path.name)
                stub_path = Path(output_dir) / rel_path.with_suffix(".pyi")
            else:
                stub_path = Path(file_path).with_suffix(".pyi")

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"\n{'=' * 60}"))
                self.stdout.write(self.style.SUCCESS(f"Would write: {stub_path}"))
                self.stdout.write(self.style.SUCCESS(f"{'=' * 60}"))
                self.stdout.write(stub_content)
                continue

            if check_mode:
                # Check if stub exists and is up-to-date
                if stub_path.exists():
                    existing_content = stub_path.read_text(encoding="utf-8")
                    # Strip the generated_at timestamp for comparison
                    if _strip_timestamp(existing_content) != _strip_timestamp(stub_content):
                        changes_needed = True
                        if verbosity >= 2:
                            self.stdout.write(self.style.WARNING(f"Outdated: {stub_path}"))
                else:
                    changes_needed = True
                    if verbosity >= 2:
                        self.stdout.write(self.style.WARNING(f"Missing: {stub_path}"))
                continue

            # Write stub file
            stub_path.parent.mkdir(parents=True, exist_ok=True)
            stub_path.write_text(stub_content, encoding="utf-8")
            files_written += 1

            if verbosity >= 2:
                self.stdout.write(self.style.SUCCESS(f"Generated: {stub_path}"))

        # Report results
        if check_mode:
            if changes_needed:
                msg = "Type stubs are outdated. Run 'python manage.py wireview_stubs' to update."
                self.stdout.write(self.style.ERROR(msg))
                sys.exit(1)
            else:
                self.stdout.write(self.style.SUCCESS("Type stubs are up-to-date."))
        elif not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Generated {files_written} stub file(s)."))


def _strip_timestamp(content: str) -> str:
    """Strip the generated_at timestamp from stub content for comparison."""
    lines = content.split("\n")
    filtered = [line for line in lines if not line.startswith("# Generated:")]
    return "\n".join(filtered)


def collect_components_by_module(app_filter: list[str] | None = None) -> dict[str, ModuleStubs]:
    """Collect all components grouped by source file."""
    modules: dict[str, ModuleStubs] = {}

    # Track seen FQNs to avoid duplicates
    seen_fqns: set[str] = set()

    # 1. Collect from Component._all registry
    for name, cls in Component._all.items():
        fqn = getattr(cls, "_fqn", f"{cls.__module__}.{name}")
        if fqn in seen_fqns:
            continue
        seen_fqns.add(fqn)

        # Apply app filter
        if app_filter:
            app_name = cls.__module__.split(".")[0]
            if app_name not in app_filter:
                continue

        # Skip library components (in site-packages or wireview itself)
        file_path = _get_source_file(cls)
        if not file_path or _is_library_path(file_path):
            continue

        # Determine component type
        if getattr(cls, "_is_live_component", False):
            component_type = "LiveComponent"
        else:
            component_type = "Component"

        stub_info = extract_component_stub_info(cls, component_type)
        if file_path not in modules:
            modules[file_path] = ModuleStubs(module_path=cls.__module__, file_path=file_path)
        modules[file_path].components.append(stub_info)

    # 2. Collect FunctionComponents
    for name, fc in function_registry.items():
        # Skip FQN duplicates (registered twice)
        if "." in name and name.count(".") > 1:
            continue

        # Apply app filter
        if app_filter:
            app_name = fc.__module__.split(".")[0]
            if app_name not in app_filter:
                continue

        file_path = _get_source_file_func(fc)
        if not file_path or _is_library_path(file_path):
            continue

        stub_info = extract_function_component_stub_info(fc)
        if file_path not in modules:
            modules[file_path] = ModuleStubs(module_path=fc.__module__, file_path=file_path)
        modules[file_path].components.append(stub_info)

    return modules


def _get_source_file(cls: type) -> str | None:
    """Get source file path for a class."""
    try:
        return inspect.getfile(cls)
    except (TypeError, OSError):
        return None


def _get_source_file_func(fc: FunctionComponent) -> str | None:
    """Get source file path for a function component."""
    try:
        return inspect.getfile(fc.func)
    except (TypeError, OSError):
        return None


def _is_library_path(path: str) -> bool:
    """Check if path is in a library (site-packages, venv, etc.)."""
    path_lower = path.lower()
    library_indicators = [
        "site-packages",
        ".venv",
        "venv",
        "/wireview/",  # Skip wireview library itself
    ]
    return any(indicator in path_lower for indicator in library_indicators)


def extract_component_stub_info(cls: type[Component], component_type: str) -> ComponentStubInfo:
    """Extract stub information from a component class."""
    name = getattr(cls, "_name", cls.__name__)
    fqn = getattr(cls, "_fqn", f"{cls.__module__}.{name}")

    # Extract fields
    fields = _extract_fields(cls)

    # Extract methods
    methods, handlers = _extract_methods(cls)

    # Extract class variables
    class_vars = _extract_class_vars(cls)

    return ComponentStubInfo(
        name=name,
        fqn=fqn,
        module=cls.__module__,
        file_path=_get_source_file(cls) or "",
        component_type=component_type,
        docstring=inspect.getdoc(cls),
        fields=fields,
        methods=methods,
        class_vars=class_vars,
        handlers=handlers,
    )


def extract_function_component_stub_info(fc: FunctionComponent) -> ComponentStubInfo:
    """Extract stub information from a function component."""
    # Extract parameter info as fields
    fields = []
    for param_name, info in fc._param_info.items():
        type_hint = info.get("type")
        type_str = get_type_string(type_hint) if type_hint else "Any"
        default = info.get("default") if "default" in info else None
        fields.append(
            FieldInfo(
                name=param_name,
                type_str=type_str,
                annotation=type_hint,
                default=serialize_default(default) if "default" in info else None,
                required=info.get("required", True),
            )
        )

    return ComponentStubInfo(
        name=fc.name,
        fqn=f"{fc.__module__}.{fc.name}",
        module=fc.__module__,
        file_path=_get_source_file_func(fc) or "",
        component_type="FunctionComponent",
        docstring=fc.__doc__,
        fields=fields,
        methods=[],
        class_vars={"template": fc.template, "slots": fc.slots} if fc.template or fc.slots else {},
        handlers=[],
    )


def _extract_fields(cls: type[Component]) -> list[FieldInfo]:
    """Extract Pydantic field information from a component class."""
    from pydantic_core import PydanticUndefinedType

    fields = []

    # Skip internal fields
    internal_fields = {"id", "user", "wire"}
    exclude_fields = getattr(cls, "_exclude_fields", set())
    skip_fields = internal_fields | exclude_fields

    for name, field_info in cls.model_fields.items():
        if name in skip_fields:
            continue

        annotation = field_info.annotation
        type_str = get_type_string(annotation)
        default = field_info.default

        # Keep raw default value for proper serialization later
        # Convert PydanticUndefined to None
        if isinstance(default, PydanticUndefinedType):
            default_value = None
        else:
            default_value = default

        fields.append(
            FieldInfo(
                name=name,
                type_str=type_str,
                annotation=annotation,
                default=default_value,
                required=field_info.is_required(),
                description=field_info.description,
            )
        )

    return fields


def _extract_methods(cls: type[Component]) -> tuple[list[MethodInfo], list[str]]:
    """Extract public methods (event handlers) from a component class."""
    methods = []
    handlers = []

    # Base Component and LiveComponent method names to exclude
    base_methods = set(dir(Component)) | set(dir(LiveComponent))

    # Pydantic internal methods to skip
    pydantic_methods = {"model_post_init", "model_dump", "model_dump_json", "model_validate", "model_copy"}

    for name in dir(cls):
        # Skip private and dunder methods
        if name.startswith("_"):
            continue

        # Skip non-lowercase names
        if not name.islower():
            continue

        # Skip Pydantic internal methods
        if name in pydantic_methods:
            continue

        # Skip base class methods unless overridden
        if name in base_methods:
            # Check if actually overridden in this class
            if name not in vars(cls):
                continue

        try:
            attr = getattr(cls, name)
        except AttributeError:
            continue

        # Only include callable attributes
        if not callable(attr):
            continue

        # Unwrap validate_call decorator
        original_func = getattr(attr, "__wrapped__", attr)
        is_async = asyncio.iscoroutinefunction(original_func)

        static = inspect.getattr_static(cls, name, None)
        if isinstance(static, classmethod):
            binding = "classmethod"
        elif isinstance(static, staticmethod):
            binding = "staticmethod"
        else:
            binding = "method"

        # Get signature
        sig = None
        try:
            sig = _resolved_signature(original_func)
            parameters = extract_parameters(sig)
            for param_name, param in sig.parameters.items():
                if param_name in parameters:
                    parameters[param_name]["annotation"] = param.annotation
        except (ValueError, TypeError):
            parameters = {}

        # Get return type
        return_annotation = sig.return_annotation if sig else inspect.Parameter.empty
        if return_annotation != inspect.Parameter.empty:
            return_type = get_type_string(return_annotation)
        else:
            return_type = "None"

        methods.append(
            MethodInfo(
                name=name,
                is_async=is_async,
                parameters=parameters,
                return_type=return_type,
                docstring=inspect.getdoc(original_func),
                return_annotation=return_annotation,
                binding=binding,
            )
        )

        # Track as handler if async (event handler convention)
        # Exclude lifecycle methods from handlers list
        lifecycle_methods = {
            "joined",
            "leaving",
            "mutation",
            "notification",
            "params_changed",
            "handle_hook_event",
            "update",
        }
        if is_async and name not in lifecycle_methods:
            handlers.append(name)

    return methods, handlers


def _extract_class_vars(cls: type[Component]) -> dict[str, t.Any]:
    """Extract class variable values."""
    class_vars = {}

    # Template name
    if hasattr(cls, "_template_name"):
        class_vars["_template_name"] = getattr(cls, "_template_name", "")

    # Slots
    slots = get_class_attribute_safe(cls, "_slots", {})
    if slots:
        class_vars["_slots"] = slots

    # Subscriptions (may be dynamic property)
    if is_dynamic_property(cls, "_subscriptions"):
        class_vars["_subscriptions_dynamic"] = True
    else:
        subscriptions = get_class_attribute_safe(cls, "_subscriptions", set())
        if subscriptions:
            class_vars["_subscriptions"] = list(subscriptions) if not callable(subscriptions) else []

    # Temporary assigns
    temp_assigns = get_class_attribute_safe(cls, "_temporary_assigns", set())
    if temp_assigns:
        class_vars["_temporary_assigns"] = list(temp_assigns) if not callable(temp_assigns) else []

    return class_vars


def generate_stub_content(module_stubs: ModuleStubs) -> str:
    """Generate .pyi file content for a module."""
    lines: list[str] = []

    # Header
    timestamp = datetime.now(timezone.utc).isoformat()
    lines.append('"""Auto-generated type stubs for wireview components.')
    lines.append("")
    lines.append("DO NOT EDIT - regenerate with: python manage.py wireview_stubs")
    lines.append('"""')
    lines.append("")
    lines.append(f"# Generated: {timestamp}")
    lines.append("")

    types_ = _StubTypes.for_module(module_stubs)
    body: list[str] = []
    for comp in module_stubs.components:
        if comp.component_type == "FunctionComponent":
            body.extend(_generate_function_component_stub(comp, types_))
        else:
            body.extend(_generate_class_stub(comp, types_))
        body.append("")

    # Imports last: they are exactly the names the body used.
    lines.extend(types_.import_lines())
    lines.append("")
    lines.extend(body)

    return "\n".join(lines)


def _collect_imports(module_stubs: ModuleStubs) -> list[str]:
    """The import lines a module's stub needs: every name its body uses, and nothing else."""
    types_ = _StubTypes.for_module(module_stubs)
    for comp in module_stubs.components:
        if comp.component_type == "FunctionComponent":
            _generate_function_component_stub(comp, types_)
        else:
            _generate_class_stub(comp, types_)
    return types_.import_lines()


def _resolved_signature(func: t.Callable[..., t.Any]) -> inspect.Signature:
    """A signature whose string annotations (``from __future__ import annotations``) are evaluated.

    ``eval_str`` fails as a whole when one annotation names something only a
    ``TYPE_CHECKING`` block imports; then each annotation is tried on its own and
    the ones that still fail stay strings, which the stub writes as ``Any``.
    """
    try:
        return inspect.signature(func, eval_str=True)
    except Exception:
        sig = inspect.signature(func)
    namespace = getattr(inspect.unwrap(func), "__globals__", {})

    def resolve(annotation: t.Any) -> t.Any:
        if not isinstance(annotation, str):
            return annotation
        try:
            return eval(annotation, namespace)  # noqa: S307 -- the module's own annotation text
        except Exception:
            return annotation

    return sig.replace(
        parameters=[p.replace(annotation=resolve(p.annotation)) for p in sig.parameters.values()],
        return_annotation=resolve(sig.return_annotation),
    )


class _StubTypes:
    """Renders annotations for one stub file and records the imports they need.

    A stub replaces its module for the type checker, so every name it writes has
    to be bound in the stub itself (#89): the source's ``import typing as t``
    does not exist there. So nothing is copied as text. Each annotation is
    rendered from the resolved object: builtins by name, ``typing`` constructs
    with their own imports, and classes from other modules with a
    ``from module import Name``. What cannot be written faithfully, a TypeVar, a
    class nested in a function, a class defined in the same module but not
    stubbed, a name that would clash with another, becomes ``Any``: vaguer than
    the source, never wrong.
    """

    def __init__(self, module: str, local_names: set[str]) -> None:
        self.module = module
        self.local_names = local_names
        self.typing: set[str] = set()
        self.bases: set[str] = set()
        self.from_imports: dict[str, set[str]] = {}
        self._bound: dict[str, str] = {}

    @classmethod
    def for_module(cls, module_stubs: ModuleStubs) -> _StubTypes:
        local = {c.name for c in module_stubs.components if c.component_type != "FunctionComponent"}
        return cls(module_stubs.module_path, local)

    def use(self, name: str) -> str:
        """Mark a ``typing`` name as used and return it."""
        self.typing.add(name)
        return name

    def any(self) -> str:
        return self.use("Any")

    def render(self, annotation: t.Any) -> str:
        if annotation is inspect.Parameter.empty or annotation is t.Any:
            return self.any()
        if annotation is None or annotation is type(None):
            return "None"
        if isinstance(annotation, str):
            return self.any()

        origin = t.get_origin(annotation)
        args = t.get_args(annotation)
        if origin is t.Annotated:
            return self.render(args[0])
        if origin is t.Union or origin is types.UnionType:
            return " | ".join(self.render(arg) for arg in args)
        if origin is t.Literal:
            if all(isinstance(arg, (str, int, bool)) and not isinstance(arg, enum.Enum) for arg in args):
                return f"{self.use('Literal')}[{', '.join(repr(arg) for arg in args)}]"
            return self.any()
        if origin is not None:
            base = self._class_name(origin)
            if base is None:
                return self.any()
            if not args:
                return base
            if origin is collections.abc.Callable:
                params, result = args[0], args[-1]
                params_str = "..." if params is Ellipsis else f"[{', '.join(self.render(p) for p in params)}]"
                return f"{base}[{params_str}, {self.render(result)}]"
            return f"{base}[{', '.join('...' if arg is Ellipsis else self.render(arg) for arg in args)}]"
        return self._class_name(annotation) or self.any()

    def _class_name(self, cls: t.Any) -> str | None:
        if not isinstance(cls, type):
            return None
        name = cls.__name__
        module = cls.__module__
        if module == "builtins":
            return None if name in self.local_names else name
        if module == self.module:
            # Only the classes this stub itself declares exist in it.
            return name if name in self.local_names else None
        if module == "__main__" or "<" in cls.__qualname__ or "." in cls.__qualname__:
            return None
        if name in self.local_names or self._bound.get(name, module) != module:
            return None
        self._bound[name] = module
        self.from_imports.setdefault(module, set()).add(name)
        return name

    def import_lines(self) -> list[str]:
        lines = []
        if self.typing:
            lines.append(f"from typing import {', '.join(sorted(self.typing))}")
        lines.extend(sorted(self.bases))
        for module in sorted(self.from_imports):
            if module == "typing":
                continue
            lines.append(f"from {module} import {', '.join(sorted(self.from_imports[module]))}")
        return lines


def _generate_class_stub(comp: ComponentStubInfo, types_: _StubTypes | None = None) -> list[str]:
    """Generate stub for a Component or LiveComponent class."""
    types_ = types_ or _StubTypes(comp.module, {comp.name})
    lines: list[str] = []

    # Class definition
    if comp.component_type == "LiveComponent":
        base_class = "LiveComponent"
        types_.bases.add("from wireview.live_component import LiveComponent")
    else:
        base_class = "Component"
        types_.bases.add("from wireview.component import Component")
    lines.append(f"class {comp.name}({base_class}):")

    # Docstring
    if comp.docstring:
        # Handle multiline docstrings
        doc_lines = comp.docstring.split("\n")
        if len(doc_lines) == 1:
            lines.append(f'    """{comp.docstring}"""')
        else:
            lines.append('    """')
            for doc_line in doc_lines:
                lines.append(f"    {doc_line}")
            lines.append('    """')
        lines.append("")

    # Every class stub ends with two ClassVar[... Any ...] metadata lines.
    types_.use("ClassVar")
    types_.use("Any")

    # Class variables
    if comp.class_vars:
        if comp.class_vars.get("_template_name"):
            lines.append("    _template_name: ClassVar[str]")
        if comp.class_vars.get("_slots"):
            lines.append("    _slots: ClassVar[dict[str, dict[str, Any]]]")
        if comp.class_vars.get("_subscriptions_dynamic"):
            lines.append("    # Note: _subscriptions is a dynamic property")
            lines.append("    @property")
            lines.append("    def _subscriptions(self) -> set[str]: ...")
        elif comp.class_vars.get("_subscriptions"):
            lines.append("    _subscriptions: ClassVar[set[str]]")
        if comp.class_vars.get("_temporary_assigns"):
            lines.append("    _temporary_assigns: ClassVar[set[str]]")
        lines.append("")

    # Instance fields
    if comp.fields:
        for field in comp.fields:
            type_str = types_.render(field.annotation) if field.annotation is not None else field.type_str
            lines.append(f"    {field.name}: {type_str}")
        lines.append("")

    # Methods
    if comp.methods:
        for method in comp.methods:
            sig = _format_method_signature(method, types_)
            if method.binding != "method":
                lines.append(f"    @{method.binding}")
            if method.is_async:
                lines.append(f"    async def {method.name}{sig}: ...")
            else:
                lines.append(f"    def {method.name}{sig}: ...")
        lines.append("")

    # Meta attributes for IDE/LSP support
    lines.append("    # Wireview metadata for IDE support")
    lines.append(f"    __wireview_attrs__: ClassVar[dict[str, dict[str, Any]]] = {_format_attrs_dict(comp.fields)}")
    lines.append(f"    __wireview_handlers__: ClassVar[list[str]] = {comp.handlers!r}")

    return lines


def _generate_function_component_stub(comp: ComponentStubInfo, types_: _StubTypes | None = None) -> list[str]:
    """Generate stub for a FunctionComponent."""
    if types_ is not None:
        types_.bases.add("from wireview.function_component import FunctionComponent")
    lines: list[str] = []

    # Variable declaration
    lines.append(f"{comp.name}: FunctionComponent")

    # Docstring as comment
    if comp.docstring:
        lines.append(f'"""{comp.docstring}"""')

    return lines


def _format_method_signature(method: MethodInfo, types_: _StubTypes | None = None) -> str:
    """Format a method signature for a stub, keeping every parameter's kind.

    ``*args``, ``**kwargs``, keyword-only and positional-only parameters keep
    their markers: dropping the stars turned ``(q: str = "", **rest)`` into
    ``(q: str = ..., rest: Any)``, a SyntaxError (#89).
    """
    types_ = types_ or _StubTypes("", set())
    first = {"method": "self", "classmethod": "cls"}.get(method.binding)
    params = [first] if first else []
    positional_only = False
    star_written = False

    for name, param in method.parameters.items():
        if name in ("self", "cls") and first:
            continue
        kind = param.get("kind", "POSITIONAL_OR_KEYWORD")
        if positional_only and kind != "POSITIONAL_ONLY":
            params.append("/")
            positional_only = False
        if kind == "POSITIONAL_ONLY":
            positional_only = True
        if kind == "KEYWORD_ONLY" and not star_written:
            params.append("*")
            star_written = True

        if "annotation" in param:
            type_str = types_.render(param["annotation"])
        else:
            type_str = param.get("type") or types_.any()
        prefix = {"VAR_POSITIONAL": "*", "VAR_KEYWORD": "**"}.get(kind, "")
        if kind == "VAR_POSITIONAL":
            star_written = True
        default = " = ..." if param.get("has_default") and not prefix else ""
        params.append(f"{prefix}{name}: {type_str}{default}")
    if positional_only:
        params.append("/")

    params_str = ", ".join(params)
    if method.return_annotation is not inspect.Parameter.empty:
        return_type = types_.render(method.return_annotation)
    else:
        return_type = method.return_type or "None"

    return f"({params_str}) -> {return_type}"


def _format_attrs_dict(fields: list[FieldInfo]) -> str:
    """Format __wireview_attrs__ dictionary."""
    if not fields:
        return "{}"

    items = []
    for fld in fields:
        parts = [f"'type': {fld.type_str!r}", f"'required': {fld.required}"]
        if fld.default is not None:
            default_repr = _serialize_default_for_stub(fld.default)
            parts.append(f"'default': {default_repr}")
        items.append(f"'{fld.name}': {{{', '.join(parts)}}}")

    return "{" + ", ".join(items) + "}"


def _serialize_default_for_stub(value: t.Any) -> str:
    """Serialize a default value for stub file."""
    import enum

    # Handle None
    if value is None:
        return "None"

    # Handle Enum FIRST (before str check, since StrEnum is both str and Enum)
    if isinstance(value, enum.Enum):
        # For string enums, use the value
        enum_value = value.value
        return repr(enum_value)

    # Handle basic types
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return repr(value)

    # Handle empty collections
    if isinstance(value, list) and not value:
        return "[]"
    if isinstance(value, dict) and not value:
        return "{}"
    if isinstance(value, set) and not value:
        return "set()"
    if isinstance(value, tuple) and not value:
        return "()"

    # Handle collections with content - just show ellipsis
    if isinstance(value, (list, dict, set, tuple)):
        return "..."

    # Handle callable defaults (factories)
    if callable(value):
        return "..."

    # Fallback
    return "..."


# Public API for auto-generation
def generate_all_stubs(
    output_dir: str | None = None,
    app_filter: list[str] | None = None,
    quiet: bool = False,
) -> int:
    """Generate all stubs programmatically.

    This function can be called from AppConfig.ready() for auto-generation.

    Args:
        output_dir: Output directory (default: next to source files)
        app_filter: Filter by app names
        quiet: Suppress output messages

    Returns:
        Number of stub files generated
    """
    modules = collect_components_by_module(app_filter)
    files_written = 0

    for file_path, module_stubs in modules.items():
        stub_content = generate_stub_content(module_stubs)

        if output_dir:
            source_path = Path(file_path)
            try:
                rel_path = source_path.relative_to(Path.cwd())
            except ValueError:
                rel_path = Path(source_path.name)
            stub_path = Path(output_dir) / rel_path.with_suffix(".pyi")
        else:
            stub_path = Path(file_path).with_suffix(".pyi")

        # Only write if content changed
        if stub_path.exists():
            existing = stub_path.read_text(encoding="utf-8")
            if _strip_timestamp(existing) == _strip_timestamp(stub_content):
                continue

        stub_path.parent.mkdir(parents=True, exist_ok=True)
        stub_path.write_text(stub_content, encoding="utf-8")
        files_written += 1

        if not quiet:
            print(f"Generated: {stub_path}")

    return files_written
