"""Generated stubs are valid Python that binds every name it uses (#89).

A stub replaces its module for the type checker, so the source's own imports do
not exist there. Two things broke projects that ran ``ruff check`` over the
generated ``live.pyi``: annotations copied as source text (``t.Any`` with ``t``
unbound, F821) and ``**kwargs`` written without its stars, which after a
parameter with a default is a SyntaxError. The fixture below has both, in a
module with ``from __future__ import annotations`` and ``import typing as t``,
the way the report's component was written.

Beyond the report's two cases, every stub is checked the same way: it parses,
every name it reads is bound in it, and every name it imports is read.
"""

from __future__ import annotations

import ast
import builtins
import datetime
import typing as t
from decimal import Decimal
from enum import Enum

import pytest

from wireview.component import Component
from wireview.management.commands.wireview_stubs import (
    ModuleStubs,
    collect_components_by_module,
    extract_component_stub_info,
    generate_stub_content,
)

pytestmark = pytest.mark.unit

T = t.TypeVar("T")


class Local:
    """A class of this module that the stub does not declare."""


class StubProbe(Component, public=False):
    """Written the way #89's component was: an aliased typing module, keyword catch-alls."""

    _template_name = "stub_probe.html"

    when: datetime.date | None = None
    price: Decimal = Decimal(0)
    mode: t.Literal["a", "b"] = "a"
    tags: t.List[str] = []
    local: t.Any = None

    @classmethod
    def new(cls, wire: t.Any, **kwargs: t.Any): ...

    @staticmethod
    def helper(value: int, /, scale: float = 1.0, *, exact: bool = False) -> float:
        return value * scale

    async def search(self, q: str = "", **_ignored: t.Any) -> None: ...

    async def pick(self, *choices: str, first: t.Optional[int] = None) -> t.Dict[str, t.Any]:
        return {}

    async def odd(self, thing: Local, generic: T, hook: t.Callable[[int], t.Awaitable[str]]) -> Local:
        return thing

    async def later(self, value: NotImportedAnywhere) -> None: ...  # noqa: F821 -- stays a string


def undefined_and_unused(source: str) -> tuple[set[str], set[str]]:
    """Names a stub reads but never binds, and names it imports but never reads."""
    tree = ast.parse(source)
    bound = set(dir(builtins))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                bound.add(name)
                imported.add(name)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if node in tree.body:
                bound.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node in tree.body:
            bound.add(node.target.id)
    read = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    return read - bound, imported - read


def stub_for(*classes: type[Component]) -> str:
    module = ModuleStubs(module_path=__name__, file_path=__file__)
    module.components = [extract_component_stub_info(cls, "Component") for cls in classes]
    return generate_stub_content(module)


def test_the_reported_stub_is_valid_python():
    source = stub_for(StubProbe)

    ast.parse(source)
    undefined, unused = undefined_and_unused(source)
    assert not undefined, f"names the stub reads but never binds: {sorted(undefined)}\n{source}"
    assert not unused, f"imports the stub never reads: {sorted(unused)}\n{source}"


def test_catch_all_parameters_keep_their_stars():
    source = stub_for(StubProbe)

    assert "    @classmethod\n    def new(cls, wire: Any, **kwargs: Any) -> None: ..." in source
    assert "async def search(self, q: str = ..., **_ignored: Any) -> None: ..." in source
    assert "async def pick(self, *choices: str, first: int | None = ...) -> dict[str, Any]: ..." in source


def test_positional_only_and_keyword_only_markers_are_kept():
    source = stub_for(StubProbe)

    assert (
        "    @staticmethod\n    def helper(value: int, /, scale: float = ..., *, exact: bool = ...) -> float: ..."
        in source
    )


def test_types_are_imported_or_become_any():
    source = stub_for(StubProbe)

    assert "from datetime import date" in source and "when: date | None" in source
    assert "from decimal import Decimal" in source and "price: Decimal" in source
    assert "mode: Literal['a', 'b']" in source
    assert "tags: list[str]" in source
    # A class only the source module has, a TypeVar and an unresolvable name: all Any.
    assert "async def odd(self, thing: Any, generic: Any, hook: Callable[[int], Awaitable[str]]) -> Any: ..." in source
    assert "from collections.abc import Awaitable, Callable" in source
    assert "async def later(self, value: Any) -> None: ..." in source


@pytest.mark.django_db
def test_every_stub_this_project_generates_is_valid_python():
    """The fixtures and examples in testproj cover the shapes real components take."""
    modules = collect_components_by_module()
    assert modules, "no components collected; the check below would prove nothing"

    problems = {}
    for module_path, module_stubs in modules.items():
        source = generate_stub_content(module_stubs)
        try:
            ast.parse(source)
        except SyntaxError as error:
            problems[module_path] = f"SyntaxError: {error}"
            continue
        undefined, unused = undefined_and_unused(source)
        if undefined or unused:
            problems[module_path] = f"undefined {sorted(undefined)}, unused {sorted(unused)}"

    assert not problems, problems


# --- Follow-up from the implementation review (docs/design/stubs-input-values-review-codex-2026-09-19.md) ---

P = t.ParamSpec("P")


class Outer:
    class Widget:
        """A nested class with the same name as a component of this module."""


class Widget(Component, public=False):
    """A component whose name a nested class of the module also has."""

    _template_name = "widget.html"

    async def take(self, inner: Outer.Widget) -> None: ...


# A class named like a builtin, from another module: importing it would rebind int.
ShadowInt = type("int", (), {"__module__": "stub_review_external", "__qualname__": "int"})


class Color(Enum):
    RED = (1, 2)
    BLUE = object()


class ReviewProbe(Component, public=False):
    """Docstring with \"\"\"triple quotes\"\"\" and a backslash \\ in it"""

    _template_name = "review_probe.html"

    ratio: float = float("inf")
    missing: float = float("nan")
    color: Color = Color.BLUE

    async def hook(self, callback: t.Callable[P, int], prefixed: t.Callable[t.Concatenate[str, P], int]) -> None: ...

    async def stream(self, handle: t.IO[str], binary: t.BinaryIO) -> t.TextIO: ...

    async def shadow(self, weird: ShadowInt, real: int) -> None: ...

    async def accepts(self, cls: int, /) -> None: ...

    @staticmethod
    def utility(self: int) -> int:
        return self

    async def receiver_only(self, /, **options: t.Any) -> None: ...


def test_review_findings_all_render_valid_stubs():
    source = stub_for(ReviewProbe, Widget)

    ast.parse(source)
    undefined, unused = undefined_and_unused(source)
    assert not undefined, f"{sorted(undefined)}\n{source}"
    assert not unused, f"{sorted(unused)}\n{source}"


def test_a_paramspec_callable_is_written_with_dots_not_a_wrong_arity():
    source = stub_for(ReviewProbe)

    assert "callback: Callable[..., int], prefixed: Callable[..., int]" in source


def test_classes_from_typing_itself_are_imported_from_typing():
    source = stub_for(ReviewProbe)

    assert "handle: IO[str], binary: BinaryIO) -> TextIO" in source
    typing_line = next(line for line in source.splitlines() if line.startswith("from typing import"))
    assert {"IO", "BinaryIO", "TextIO"} <= set(typing_line.removeprefix("from typing import ").split(", "))


def test_an_import_never_shadows_a_builtin_or_a_component():
    source = stub_for(ReviewProbe, Widget)

    assert "weird: Any, real: int" in source
    assert "stub_review_external" not in source
    assert "async def take(self, inner: Any) -> None: ..." in source


def test_parameters_are_dropped_by_position_not_by_name():
    source = stub_for(ReviewProbe)

    assert "async def accepts(self, cls: int, /) -> None: ..." in source
    assert "    @staticmethod\n    def utility(self: int) -> int: ..." in source
    assert "async def receiver_only(self, /, **options: Any) -> None: ..." in source


def test_defaults_that_are_not_literals_become_ellipsis():
    source = stub_for(ReviewProbe)
    attrs = next(line for line in source.splitlines() if "__wireview_attrs__" in line)

    assert "inf" not in attrs and "nan" not in attrs and "object at" not in attrs
    assert "'ratio': {'type': 'float', 'required': False, 'default': ...}" in attrs


def test_a_docstring_with_triple_quotes_stays_valid():
    source = stub_for(ReviewProbe)

    ast.parse(source)
    assert "triple quotes" in source


def test_a_class_without_a_docstring_does_not_inherit_the_base_ones():
    class Bare(Component, public=False):
        _template_name = "bare.html"

    source = stub_for(Bare)

    assert "Base class for wireview components" not in source


def test_a_component_named_any_does_not_clash_with_typing_any():
    namespace: dict = {}
    exec(  # noqa: S102 -- a class that must be literally named Any
        "from wireview.component import Component\n"
        "class Any(Component, public=False):\n"
        "    _template_name = 'any.html'\n"
        "    value: int = 0\n"
        "    async def act(self, payload: dict) -> None: ...\n",
        namespace,
    )
    any_component = namespace["Any"]
    any_component.__module__ = __name__

    source = stub_for(any_component)

    ast.parse(source)
    undefined, unused = undefined_and_unused(source)
    assert not undefined and not unused, source
    assert "import typing as _typing" in source and "_typing.Any" in source


def test_a_field_that_is_not_an_identifier_is_left_out_of_the_declarations():
    from pydantic import create_model

    keyworded = create_model(
        "KeywordFields", __base__=Component, __cls_kwargs__={"public": False}, **{"class": (int, 1)}
    )
    keyworded.__module__ = __name__

    source = stub_for(keyworded)

    ast.parse(source)
    assert "'class':" in source  # still in the metadata


def test_each_annotation_is_evaluated_once():
    from wireview.management.commands.wireview_stubs import _resolved_signature

    calls = []

    def side_effect():
        calls.append(1)
        return int

    def handler(a, b): ...

    handler.__annotations__ = {"a": "side_effect()", "b": "Unbound"}
    handler.__globals__["side_effect"] = side_effect
    try:
        sig = _resolved_signature(handler)
    finally:
        del handler.__globals__["side_effect"]

    assert calls == [1]
    assert sig.parameters["a"].annotation is int
    assert sig.parameters["b"].annotation == "Unbound"
