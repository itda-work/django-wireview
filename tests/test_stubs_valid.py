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
