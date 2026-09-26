"""The public API is ``wireview.__all__`` and nothing else (#98).

1.0 promises not to break what is public, so what is public has to be written
down and kept in step: the export table, ``__all__``, the type-checking
imports, and every import the user-facing documentation shows. A submodule
path in a tutorial is a promise nobody meant to make.
"""

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

import wireview
from wireview import WireviewDeprecationWarning

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

#: What a reader copies from. docs/design, docs/implementation and docs/legacy
#: are records of how the library was built and may name its insides.
USER_FACING = [
    ROOT / "README.md",
    *sorted((ROOT / "docs" / "features").glob("*.md")),
    *sorted((ROOT / "docs" / "tutorials").glob("*.md")),
    *sorted((ROOT / "skills").rglob("*.md")),
    *sorted((ROOT / "examples").rglob("*.py")),
    *sorted((ROOT / "examples").rglob("*.md")),
]

#: Modules a project names by path rather than importing names from them:
#: the URLconf that routes the socket and the upload endpoint.
INTEGRATION_MODULES = {"wireview.urls"}

_IMPORT = re.compile(r"^\s*from (wireview(?:\.[\w.]+)?) import (\([^)]*\)|[^\n#]+)", re.M)


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    found = []
    for module, names in _IMPORT.findall(path.read_text()):
        found.append((module, [n.split(" as ")[0].strip() for n in names.strip("()").split(",") if n.strip()]))
    return found


def test_all_is_the_export_table():
    assert list(wireview.__all__) == list(wireview._EXPORTS)


@pytest.mark.parametrize("name", wireview.__all__)
def test_every_public_name_resolves(name):
    value = getattr(wireview, name)
    module = importlib.import_module(wireview._EXPORTS[name], "wireview")
    assert value is (module if wireview._EXPORTS[name] == f".{name}" else getattr(module, name))


def test_type_checkers_see_every_public_name():
    tree = ast.parse((ROOT / "wireview" / "__init__.py").read_text())
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for stmt in node.body:
                if isinstance(stmt, ast.ImportFrom):
                    seen.update(alias.asname or alias.name for alias in stmt.names)
    assert seen == set(wireview.__all__)


def test_no_public_name_is_also_a_submodule():
    """Importing ``wireview.x`` sets the package attribute ``x`` to the module.

    ``function_component`` was both: once the template tags had imported the
    submodule, ``from wireview import function_component`` handed out the
    module, and ``@function_component`` failed with "module is not callable".
    """
    submodules = {path.stem for path in (ROOT / "wireview").glob("*.py")}
    submodules |= {path.name for path in (ROOT / "wireview").iterdir() if (path / "__init__.py").exists()}
    # A module exported as itself is the same object either way
    modules = {name for name, module in wireview._EXPORTS.items() if module == f".{name}"}
    assert not submodules & (set(wireview.__all__) - modules)


def test_an_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError):
        wireview.NoSuchThing  # noqa: B018


@pytest.mark.parametrize("path", USER_FACING, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_documentation_imports_only_public_names(path):
    for module, names in _imports(path):
        if module in INTEGRATION_MODULES:
            continue
        assert module == "wireview", f"{path.relative_to(ROOT)} imports from {module}; use `from wireview import ...`"
        missing = [n for n in names if n not in wireview.__all__]
        assert not missing, f"{path.relative_to(ROOT)} imports {missing}, which wireview does not export"


def test_the_library_does_not_import_its_deprecated_module():
    for path in (ROOT / "wireview").rglob("*.py"):
        if path.name == "component.py" and path.parent.name == "wireview":
            continue
        source = path.read_text()
        assert "wireview.component import" not in source, path
        assert not re.search(r"^\s*from \.\.?component import", source, re.M) or path.parent.name == "core", path


def test_the_old_module_warns_at_the_line_that_imports_it(tmp_path):
    script = tmp_path / "old_import.py"
    script.write_text(
        "import warnings\n"
        "import django\n"
        "django.setup()\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    from wireview.component import Component\n"
        "w = [c for c in caught if c.category.__name__ == 'WireviewDeprecationWarning']\n"
        "print(len(w), w[0].filename.endswith('old_import.py'), w[0].lineno)\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        env={"DJANGO_SETTINGS_MODULE": "testproj.settings", "PYTHONPATH": str(ROOT / "tests")},
    )
    assert result.stdout.split() == ["1", "True", "6"]


def test_the_warning_class_is_a_deprecation_warning():
    assert issubclass(WireviewDeprecationWarning, DeprecationWarning)
