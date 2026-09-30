"""Guard: every place that names the supported Django and Python versions agrees (#123).

``pyproject.toml`` required Django 5.2 while the README's comparison table still
listed 5.0 and 5.1, and the tutorials asked for "Django 5.0 이상": a reader on 5.0
followed the documents and was refused by pip. The support range has three
machine-readable sources -- the dependency bound, the classifiers and the CI
matrix -- and this checks them against each other, then checks what the
documents say against them.
"""

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

#: What a reader acts on. docs/design, docs/implementation and docs/legacy are records
#: of how the library was built, and VISION.md is the plan it started from.
DOCS = [
    ROOT / "README.md",
    *(path for path in sorted((ROOT / "docs").glob("*.md")) if path.name != "VISION.md"),
    *sorted((ROOT / "docs" / "features").glob("*.md")),
    *sorted((ROOT / "docs" / "tutorials").glob("*.md")),
    *sorted((ROOT / "skills").rglob("*.md")),
    *sorted((ROOT / "examples").rglob("*.md")),
]

#: "Django 5.2+", "Django 5.2 이상", "Python 3.12+": a lower bound stated to the reader.
LOWER_BOUND = re.compile(r"\b(Django|Python) ?(\d+\.\d+) ?(?:\+|이상)")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _matrix(key: str) -> list[str]:
    match = re.search(rf"^\s*{key}: \[(.*)\]\s*$", CI, re.MULTILINE)
    assert match, f"ci.yml has no {key} matrix"
    return re.findall(r'"([\d.]+)"', match.group(1))


def _classifiers(framework: str) -> list[str]:
    prefix = f"{framework} :: "
    found = [c.removeprefix(prefix) for c in PYPROJECT["project"]["classifiers"] if c.startswith(prefix)]
    return [v for v in found if re.fullmatch(r"\d+\.\d+", v)]


def _bound(requirement: str) -> str:
    match = re.search(r">=\s*([\d.]+)", requirement)
    assert match, f"no lower bound in {requirement!r}"
    return match.group(1)


DJANGO = _matrix("django-version")
PYTHON = _matrix("python-version")
DJANGO_MIN = _bound(next(d for d in PYPROJECT["project"]["dependencies"] if d.startswith("django")))
PYTHON_MIN = _bound(PYPROJECT["project"]["requires-python"])


def test_the_matrix_starts_at_the_dependency_bound():
    assert min(DJANGO, key=_version) == DJANGO_MIN
    assert min(PYTHON, key=_version) == PYTHON_MIN


def test_the_classifiers_are_the_matrix():
    assert _classifiers("Framework :: Django") == DJANGO
    assert _classifiers("Programming Language :: Python") == PYTHON


def test_the_compatibility_policy_names_the_matrix():
    text = (ROOT / "docs" / "COMPATIBILITY.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if line.startswith("Django ") and "Python" in line)
    django, _, python = line.partition("Python")
    assert re.findall(r"\d+\.\d+", django) == DJANGO, line
    assert re.findall(r"\d+\.\d+", python) == PYTHON, line


def test_the_readme_comparison_table_names_the_matrix():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| **Django** |"))
    wireview = row.strip().strip("|").split("|")[-1]
    assert re.findall(r"\d+\.\d+", wireview) == DJANGO, row


def _claims():
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in LOWER_BOUND.finditer(line):
                yield f"{path.relative_to(ROOT)}:{number}", match.group(1), match.group(2)


CLAIMS = list(_claims())


@pytest.mark.parametrize(("where", "name", "version"), CLAIMS, ids=[c[0] for c in CLAIMS])
def test_no_document_asks_for_less_than_the_package_does(where, name, version):
    minimum = {"Django": DJANGO_MIN, "Python": PYTHON_MIN}[name]
    assert _version(version) >= _version(minimum), f"{where} says {name} {version}, but pip needs {minimum}"


def test_the_documents_are_read():
    """The pattern still matches what the tutorials write, so the guard above is not empty."""
    assert {"Django", "Python"} <= {name for _, name, _ in CLAIMS}
