"""Django management command to extract wireview component metadata for IDE LSP support.

This command introspects all registered wireview components and outputs their metadata
as JSON, which can be used by IDE plugins (VSCode, Neovim, etc.) to provide
autocompletion, go-to-definition, and hover documentation. The extension under
``editors/vscode`` is one such reader; the shape is described in
``docs/features/editor-support.md`` and versioned by ``METADATA_VERSION``.

Usage:
    python manage.py wireview_lsp
    python manage.py wireview_lsp --output=.wireview/metadata.json
    python manage.py wireview_lsp --pretty
"""

from __future__ import annotations

import functools
import importlib.util
import inspect
import json
import re
import sys
import typing as t
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from django.core.management.base import BaseCommand, CommandParser

from wireview.core.component import ALWAYS_EXCLUDED, Component
from wireview.core.handlers import is_client_callable, is_framework_class
from wireview.event_transpiler import MODIFIER_ARGUMENTS, MODIFIERS, NUMBER_ARGUMENTS
from wireview.live_component import LiveComponent

#: The shape of the output. A reader checks the major number and refuses a newer
#: one; a key added to what is already there raises the minor number.
#: 1.1 (#156): ``kind``, ``template_path``, ``accepts_extra_kwargs`` and
#: ``properties`` on a component, ``file_path`` on a method, the fields in
#: ``Meta.exclude_fields`` (``in_state`` false), ``argument`` on a modifier, and
#: ``function_components``, ``hooks``, ``template_dirs``, ``template_builtins``
#: and ``template_libraries`` (each with its ``module`` and ``file_path``) at the top.
METADATA_VERSION = "1.1"


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


def subscriptions_are_dynamic(cls: type) -> bool:
    """Whether the component overrides ``get_subscriptions()`` (#99).

    Its channels then depend on the instance, so ``Meta.subscriptions`` is not
    the whole answer and a tool cannot list them.
    """
    return any("get_subscriptions" in vars(klass) for klass in cls.__mro__ if klass is not Component)


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

    roots = template_roots()
    for name, cls in Component._all.items():
        try:
            components[name] = extract_component_metadata(cls, roots)
        except Exception as e:
            # Log error but continue processing other components
            sys.stderr.write(f"Warning: Failed to extract metadata for {name}: {e}\n")

    builtins, libraries = extract_template_libraries()
    return {
        "version": METADATA_VERSION,
        "wireview_version": installed_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
        "function_components": extract_function_components(roots),
        "hooks": extract_hooks(),
        "modifiers": extract_modifiers(),
        "template_dirs": [str(root) for root in roots],
        "template_builtins": builtins,
        "template_libraries": libraries,
    }


def installed_version() -> str:
    """The version of the package this ran from, or "" for a source tree nothing installed."""
    try:
        return importlib_metadata.version("django-wireview")
    except importlib_metadata.PackageNotFoundError:
        return ""


def template_roots() -> list[Path]:
    """Where the project's templates live, in the order the loaders search them."""
    from wireview.features.hooks import _template_roots

    return [root.resolve() for root in _template_roots()]


def find_template(name: str | None, roots: list[Path]) -> str | None:
    """The file a template name loads, or None when no directory holds it.

    Looked up on disk rather than through the engine: loading compiles the
    template, and one syntax error in one template would hide every other.
    """
    if not name:
        return None
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            # Real, like the roots: an editor names a document by where it really is
            return str(candidate.resolve())
    return None


def extract_component_metadata(cls: type[Component], roots: list[Path] | None = None) -> dict[str, t.Any]:
    """Extract metadata from a single component class."""
    if roots is None:
        roots = template_roots()
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

    meta = cls._meta
    return {
        "name": cls._name,
        "fqn": cls._fqn,
        "app_key": f"{app_name}:{cls._name}",
        "module": module,
        "file_path": file_path,
        "line_number": line_number,
        "kind": "live_component" if issubclass(cls, LiveComponent) else "component",
        "docstring": inspect.getdoc(cls),
        "template_name": meta.template_name or "",
        "template_path": find_template(meta.template_name, roots),
        "fields": extract_fields(cls),
        "accepts_extra_kwargs": accepts_extra_kwargs(cls),
        "properties": extract_properties(cls),
        "methods": extract_methods(cls),
        "slots": dict(meta.slots),
        "subscriptions": sorted(meta.subscriptions),
        "subscriptions_is_dynamic": subscriptions_are_dynamic(cls),
        "temporary_assigns": sorted(meta.temporary_assigns),
    }


def extract_fields(cls: type[Component]) -> dict[str, dict[str, t.Any]]:
    """Extract Pydantic field information from a component class."""
    fields: dict[str, dict[str, t.Any]] = {}

    # What the framework fills in. A field the component itself keeps out of the
    # signed state (``Meta.exclude_fields``) is still one a template passes.
    skip_fields = {"id"} | ALWAYS_EXCLUDED
    exclude_fields = cls._meta.exclude_fields

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
            "annotation": without_addresses(annotation, str(annotation)) if annotation else None,
            "default": default_value,
            "required": field_info.is_required(),
            "description": field_info.description,
            "in_state": name not in exclude_fields,
        }

    return fields


# A default repr in angle brackets, whose name may hold its own: <function f.<locals>.<lambda> at 0x...>,
# <bound method A.f of <A object at 0x...>>. In it, every address goes: a weakref has two (<weakref at 0x...; to ...>)
_BRACKETED = re.compile(r"<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>")
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+(?=[>;])")
_CONTAINERS = (list, tuple, set, frozenset, dict)


def _objects(value: t.Any, seen: set[int]) -> t.Iterator[t.Any]:
    """The objects a value is made of: a collection's items, a type's arguments and Annotated's metadata.

    Strings and other data are not objects here: their text is the user's, whatever it reads like.
    """
    if id(value) in seen or isinstance(value, (str, bytes, int, float, complex)) or value is None:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        parts = [*value.keys(), *value.values()]
    elif isinstance(value, _CONTAINERS):
        parts = list(value)
    else:
        parts = [*t.get_args(value), *getattr(value, "__metadata__", ())]
    if not parts and not isinstance(value, _CONTAINERS):
        yield value
    for part in parts:
        yield from _objects(part, seen)


def without_addresses(value: t.Any, text: str) -> str:
    """``text`` (a value's repr or str) without the memory addresses of the objects in the value.

    Only the repr of an object the value is made of is touched, and in it only the
    ``<... at 0x...>`` of a default repr (an object, a function, a class's method): those
    made every run's output differ. A string in the value stays as it is.
    """
    for part in _objects(value, set()):
        try:
            written = repr(part)
        except Exception:
            continue
        stable = _BRACKETED.sub(lambda match: _ADDRESS.sub("", match.group()), written)
        if stable != written:
            text = text.replace(written, stable)
    return text


def accepts_extra_kwargs(cls: type[Component]) -> bool:
    """Whether a template may pass the component more than its fields.

    ``new()`` builds the instance from the tag's arguments and a LiveComponent's
    ``update()`` receives them on every parent render: a class that overrides
    either decides for itself what an argument means.
    """
    receivers = ("new", "update", "update_many") if issubclass(cls, LiveComponent) else ("new",)
    return any(name in vars(klass) for klass in cls.__mro__ if not is_framework_class(klass) for name in receivers)


def extract_properties(cls: type[Component]) -> dict[str, dict[str, t.Any]]:
    """The properties the component's own classes define: template variables, like fields."""
    properties: dict[str, dict[str, t.Any]] = {}
    # The nearest definition of a name is the one a template reads, whatever it is:
    # a subclass's plain attribute hides the property its parent defined
    seen: set[str] = set()
    for klass in cls.__mro__:
        if is_framework_class(klass):
            continue
        for name, value in vars(klass).items():
            if name.startswith("_") or name in seen:
                continue
            seen.add(name)
            if isinstance(value, property):
                getter = value.fget
            elif isinstance(value, functools.cached_property):
                getter = value.func
            else:
                continue
            file_path, line_number = source_location(getter)
            returns = getattr(getter, "__annotations__", {}).get("return")
            properties[name] = {
                "type": get_type_string(returns) if returns is not None else None,
                "is_async": inspect.iscoroutinefunction(getter),
                "docstring": inspect.getdoc(getter) if getter else None,
                "file_path": file_path,
                "line_number": line_number,
            }
    return properties


def source_location(obj: t.Any) -> tuple[str, int]:
    """The file and the line an object was defined at, or ``("", 0)``."""
    try:
        target = inspect.unwrap(obj)
        return inspect.getsourcefile(target) or "", inspect.getsourcelines(target)[1]
    except (TypeError, OSError):
        return "", 0


def extract_methods(cls: type[Component]) -> dict[str, dict[str, t.Any]]:
    """Extract public async methods (event handlers) from a component class."""
    methods: dict[str, dict[str, t.Any]] = {}

    # Get all class attributes
    for name in dir(cls):
        # Skip private and dunder methods
        if name.startswith("_"):
            continue

        try:
            attr = getattr(cls, name)
        except AttributeError:
            continue

        # Only include callable attributes. A nested class (``class Meta:``) is
        # callable and is not a method. Names used to be skipped unless all
        # lowercase, which dropped a handler named ``toggleAll`` as well.
        if not callable(attr) or isinstance(attr, type):
            continue

        # Check if it's a coroutine function (async method)
        # Need to unwrap validate_call decorator if present
        original_func = getattr(attr, "__wrapped__", attr)
        is_async = inspect.iscoroutinefunction(original_func)

        # Get signature
        try:
            sig = inspect.signature(original_func)
            parameters = extract_parameters(sig)
        except (ValueError, TypeError):
            parameters = {}

        # Get docstring
        docstring = inspect.getdoc(original_func)

        # A mixin's method lives in the mixin's file, not the component's
        method_file, method_line = source_location(original_func)

        methods[name] = {
            # Whether a client can call it: the check every event meets (#110)
            "is_handler": is_client_callable(cls, name),
            "is_async": is_async,
            "parameters": parameters,
            "docstring": docstring,
            "file_path": method_file,
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
    """The event modifiers the client understands.

    Read from the Modifiers class of the inline transpiler, which also listed
    ``inlinejs`` -- a modifier ``{% on %}`` refuses (#90). The list is the
    client's now (#119).
    """
    return {
        name: {
            "docstring": description,
            "description": description,
            "has_argument": name in MODIFIER_ARGUMENTS,
            # What ``{% on %}`` accepts after it: a whole number, any text, or nothing
            "argument": ("number" if name in NUMBER_ARGUMENTS else "text") if name in MODIFIER_ARGUMENTS else None,
        }
        for name, description in MODIFIERS.items()
    }


def extract_function_components(roots: list[Path]) -> dict[str, dict[str, t.Any]]:
    """The ``@function_component`` functions, by the name ``{% func %}`` takes."""
    from wireview.function_components import _registry

    found: dict[str, dict[str, t.Any]] = {}
    # The registry lists each one twice, under its name and under module.name
    for fc in {id(fc): fc for fc in _registry.values()}.values():
        file_path, line_number = source_location(fc.func)
        found[fc.name] = {
            "name": fc.name,
            "fqn": f"{fc.func.__module__}.{fc.name}",
            "module": fc.func.__module__,
            "file_path": file_path,
            "line_number": line_number,
            "docstring": inspect.getdoc(fc.func),
            "template_name": fc.template or "",
            "template_path": find_template(fc.template, roots),
            "parameters": extract_parameters(inspect.signature(fc.func)),
            "slots": dict(fc.slots),
        }
    return dict(sorted(found.items()))


def extract_hooks() -> dict[str, dict[str, t.Any]]:
    """The client hooks the apps' hook files register: what ``wire-hook`` may name."""
    from wireview.features.hooks import hook_registrations

    hooks: dict[str, dict[str, t.Any]] = {}
    for name, static_path, source, line in hook_registrations():
        hooks.setdefault(name, {"static_path": static_path, "file_path": str(source), "line_number": line})
    return hooks


# Template tags and filters

#: ``parser.parse(("else", "endif"))`` and ``parser.skip_past("endcomment")``:
#: the names a block tag reads up to, as its compile function writes them.
_PARSE_UNTIL = re.compile(r"parser\.(?:parse|skip_past)\(([^)]*)\)")
_QUOTED_NAME = re.compile(r"""["']([A-Za-z_][\w-]*)["']""")
_QUOTED_END_NAME = re.compile(r"""["'](end[A-Za-z_][\w-]*)["']""")


def _block_translate_structure(name: str) -> tuple[str, list[str]]:
    # do_block_translate ends at "end" + the name it was called by
    return f"end{name}", ["plural"]


def _block_overrides() -> dict[t.Any, t.Callable[[str], tuple[str, list[str]]]]:
    """Compile functions that build their names at run time, so the source does not show them.

    Keyed by the function, not by the tag's name: another library may register a
    tag called ``blocktranslate`` that is no block at all.
    """
    from django.templatetags.i18n import do_block_translate

    return {do_block_translate: _block_translate_structure}


def block_structure(name: str, compile_func: t.Any) -> tuple[str | None, list[str]]:
    """The end tag a block tag runs to and the tags that may stand between.

    Django does not record either: a compile function just calls
    ``parser.parse()`` with the names it will stop at. So this reads them out of
    its source -- a heuristic. A tag it cannot read is reported as not a block,
    and a reader must not take that as proof (``end`` is None for ``{% load %}``
    and for a block tag whose names are computed alike).
    """
    override = _block_overrides().get(compile_func)
    if override is not None:
        return override(name)
    try:
        # ``simple_block_tag`` closes over the name
        end_name = inspect.getclosurevars(compile_func).nonlocals.get("end_name")
    except (TypeError, ValueError):
        end_name = None
    if isinstance(end_name, str):
        return end_name, []
    try:
        source = inspect.getsource(compile_func)
    except (TypeError, OSError):
        return None, []
    names: list[str] = []
    for arguments in _PARSE_UNTIL.findall(source):
        for found in _QUOTED_NAME.findall(arguments):
            if found not in names:
                names.append(found)
    ends = [found for found in names if found.startswith("end")]
    if not ends and _PARSE_UNTIL.search(source):
        # ``parser.parse(until)``: the names are in a variable, written somewhere above
        ends = _QUOTED_END_NAME.findall(source)
    if not ends:
        return None, []
    return ends[0], [found for found in names if not found.startswith("end")]


def filter_argument(func: t.Any) -> str:
    """Whether a filter takes an argument: "none", "optional" or "required".

    Counted as ``FilterExpression.args_check`` counts, without the ``autoescape``
    parameter Django passes itself to a ``needs_autoescape`` filter.
    """
    try:
        spec = inspect.getfullargspec(inspect.unwrap(func))
    except TypeError:
        return "optional"
    args = list(spec.args)
    defaults = len(spec.defaults or ())
    if getattr(func, "needs_autoescape", False) and args[-1:] == ["autoescape"]:
        args.pop()
        defaults = max(defaults - 1, 0)
    if len(args) <= 1:
        return "none"
    return "optional" if len(args) - defaults <= 1 else "required"


def extract_library(library: t.Any) -> dict[str, t.Any]:
    """The tags and the filters one template library registers."""
    tags: dict[str, dict[str, t.Any]] = {}
    for name, compile_func in library.tags.items():
        file_path, line_number = source_location(compile_func)
        end, intermediate = block_structure(name, compile_func)
        tags[name] = {
            "docstring": inspect.getdoc(compile_func),
            "file_path": file_path,
            "line_number": line_number,
            "end": end,
            "intermediate": intermediate,
        }
    filters: dict[str, dict[str, t.Any]] = {}
    for name, func in library.filters.items():
        file_path, line_number = source_location(func)
        filters[name] = {
            "docstring": inspect.getdoc(func),
            "file_path": file_path,
            "line_number": line_number,
            "argument": filter_argument(func),
            # What django.template.defaulttags.do_filter checks: the name the function was
            # last registered under, not the name the template uses
            "forbidden_in_filter_tag": getattr(func, "_filter_name", None) in ("escape", "safe"),
        }
    return {"tags": dict(sorted(tags.items())), "filters": dict(sorted(filters.items()))}


def extract_template_libraries() -> tuple[dict[str, t.Any], dict[str, dict[str, t.Any]]]:
    """What the project's Django template engine knows: its builtins, and each library ``{% load %}`` takes.

    Read from the engine rather than listed here, so an editor offers the tags
    of the Django that is installed and of every app's ``templatetags``.
    """
    from django.template import engines
    from django.template.backends.django import DjangoTemplates

    builtins: dict[str, t.Any] = {"tags": {}, "filters": {}}
    libraries: dict[str, dict[str, t.Any]] = {}
    for backend in engines.all():
        if not isinstance(backend, DjangoTemplates):
            continue
        engine = backend.engine
        # A later builtin library overrides an earlier one, as the parser has it
        for library in engine.template_builtins:
            extracted = extract_library(library)
            builtins["tags"].update(extracted["tags"])
            builtins["filters"].update(extracted["filters"])
        for name, library in engine.template_libraries.items():
            module = engine.libraries.get(name, "")
            libraries[name] = {"module": module, "file_path": module_file(module), **extract_library(library)}
        break
    return builtins, dict(sorted(libraries.items()))


def module_file(module: str) -> str:
    """The file a module is loaded from, or "" when it has none (or cannot be found)."""
    if not module:
        return ""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return ""
    return spec.origin if spec and spec.origin and spec.has_location else ""


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
    # Annotated's metadata is for validators, not readers, and its repr may hold
    # a memory address: the stub then differed on every run (#109)
    if origin is t.Annotated:
        return get_type_string(t.get_args(annotation)[0])
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
        return without_addresses(value, repr(value))

    # Handle callable defaults (factory functions)
    if callable(value):
        return f"<factory: {value.__name__}>"

    # Fallback to string representation
    return without_addresses(value, repr(value))
