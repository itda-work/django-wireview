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


#: Taken out of ``__all__`` before 1.0 froze it (#119): a second name for what
#: ``broadcast``/``abroadcast`` do, a lookup error nothing documented, a registry
#: listing and the telemetry module's own instruments. They stay importable from
#: their modules as internals.
WITHDRAWN = {
    "wireview": ["send_notification", "asend_notification", "ComponentNotFound", "list_function_components"],
    "wireview.telemetry": ["span", "payload_size"],
}


@pytest.mark.parametrize("module,name", [(m, n) for m, names in WITHDRAWN.items() for n in names])
def test_what_was_withdrawn_before_1_0_stays_out(module, name):
    assert name not in importlib.import_module(module).__all__


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


_FENCE = re.compile(r"```(?:python|py)\n(.*?)```", re.S)
_WIRE = re.compile(r"\b(?:self|component)\.wire\.(\w+)")


def _code(path: Path) -> str:
    text = path.read_text()
    return text if path.suffix == ".py" else "\n".join(_FENCE.findall(text))


@pytest.mark.parametrize("path", USER_FACING, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_documentation_uses_only_the_public_part_of_wire(path):
    """``self.wire`` is mostly plumbing (#99).

    Three examples reached past the public part and did not run:
    ``self.wire.push_event`` and ``component.wire.user`` do not exist, and
    ``self.wire.repo`` is not an attribute of it at all.
    """
    from wireview.core.meta import PUBLIC_MEMBERS

    used = set(_WIRE.findall(_code(path)))
    assert used <= PUBLIC_MEMBERS, f"{path.relative_to(ROOT)} uses self.wire.{sorted(used - PUBLIC_MEMBERS)}"


def test_the_public_part_of_wire_exists():
    from wireview.core.meta import PUBLIC_MEMBERS, WireviewMeta

    wire = WireviewMeta(params={})
    assert all(hasattr(wire, name) for name in PUBLIC_MEMBERS)


COMPONENT_API = ROOT / "docs" / "features" / "component-api.md"


def _component_members() -> dict[str, set[str]]:
    from pydantic import BaseModel

    from wireview import Component, LiveComponent

    pydantic = set(dir(BaseModel))
    component = {n for n in dir(Component) if not n.startswith("_")} - pydantic
    live = {n for n in dir(LiveComponent) if not n.startswith("_")} - pydantic - component
    return {"Component": component, "LiveComponent": live}


def test_every_component_member_is_listed_as_public_or_internal():
    """Without underscores every member looked public, so 1.0 would have frozen all of them (#119).

    ``docs/features/component-api.md`` is the list. A new member has to be put in its
    public tables or its internal line before it ships.
    """
    text = COMPONENT_API.read_text()
    missing = []
    for owner, names in _component_members().items():
        for name in sorted(names):
            prefix = "LiveComponent." if owner == "LiveComponent" else ""
            if not re.search(rf"`(?:await )?{re.escape(prefix)}{name}\b", text):
                missing.append(f"{owner}.{name}")
    assert missing == []


def test_the_component_api_lists_no_member_that_does_not_exist():
    members = set().union(*_component_members().values())
    fields = {"id", "user", "wire", "session"}
    listed = set(re.findall(r"^\| `(?:await )?(?:LiveComponent\.)?(\w+)", COMPONENT_API.read_text(), re.M))
    assert listed - members - fields == set()


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


def test_importing_the_library_warns_nothing():
    """A warning at import is one every user sees on every start. pydantic before 2.10
    warned that ``AutoBroadcast.model_pk`` sits in its protected ``model_`` namespace, and
    the lane that installs those releases runs with the warnings plugin off (#132).

    Every UserWarning is an error, whoever raises it; a DeprecationWarning is one when it
    points at wireview or any of its modules. The deprecated module warns on purpose and is
    left out.
    """
    script = (
        "import importlib, pkgutil, warnings\n"
        "warnings.simplefilter('error', UserWarning)\n"
        # A -W module field matches the top-level name only; this matches every wireview module
        "warnings.filterwarnings('error', category=DeprecationWarning, module=r'wireview(\\.|$)')\n"
        "import django, wireview\n"
        "django.setup()\n"
        "for module in pkgutil.walk_packages(wireview.__path__, 'wireview.'):\n"
        "    if module.name != 'wireview.component':\n"
        "        importlib.import_module(module.name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"DJANGO_SETTINGS_MODULE": "testproj.settings", "PYTHONPATH": str(ROOT / "tests")},
    )
    assert result.returncode == 0, result.stderr


def test_the_warning_class_is_a_deprecation_warning():
    assert issubclass(WireviewDeprecationWarning, DeprecationWarning)


def test_the_old_module_says_what_has_no_replacement(tmp_path):
    """It re-exports two names with no public replacement; its warning named only Component."""
    script = tmp_path / "old_import_names.py"
    script.write_text(
        "import warnings\n"
        "import django\n"
        "django.setup()\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import wireview.component as old\n"
        "w = [c for c in caught if c.category.__name__ == 'WireviewDeprecationWarning']\n"
        "print(' '.join(old.__all__))\n"
        "print(str(w[0].message))\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        env={"DJANGO_SETTINGS_MODULE": "testproj.settings", "PYTHONPATH": str(ROOT / "tests")},
    )
    exported, message = result.stdout.splitlines()
    for name in exported.split():
        assert f"`{name}`" in message, name
    public = [name for name in exported.split() if name in wireview.__all__]
    internal = [name for name in exported.split() if name not in wireview.__all__]
    assert internal == ["ComponentNotFound", "MessagePayload"]
    assert "no replacement" in message
    compatibility = (ROOT / "docs" / "COMPATIBILITY.md").read_text()
    row = next(line for line in compatibility.splitlines() if line.startswith("| `wireview.component` 모듈"))
    for name in public + internal:
        assert f"`{name}`" in row, name
