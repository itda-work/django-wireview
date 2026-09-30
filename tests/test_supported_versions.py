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
from test_packaging import _workflow

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI_JOBS = _workflow("ci.yml")["jobs"]

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


def _matrix(job: str, key: str) -> list[str]:
    matrix = CI_JOBS[job]["strategy"]["matrix"]
    assert key in matrix, f"ci.yml's {job} job has no {key} matrix"
    return [str(value) for value in matrix[key]]


def _classifiers(framework: str) -> list[str]:
    prefix = f"{framework} :: "
    found = [c.removeprefix(prefix) for c in PYPROJECT["project"]["classifiers"] if c.startswith(prefix)]
    return [v for v in found if re.fullmatch(r"\d+\.\d+", v)]


def _bound(requirement: str) -> str:
    match = re.search(r">=\s*([\d.]+)", requirement)
    assert match, f"no lower bound in {requirement!r}"
    return match.group(1)


DJANGO = _matrix("test", "django-version")
PYTHON = _matrix("test", "python-version")
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


#: The channel layers docs/COMPATIBILITY.md supports across processes: its table row's
#: package, verified version and broker release, and the WIREVIEW_TEST_LAYER that runs
#: E2E on it (#130).
LAYER_ROW = re.compile(r"^\| \w+ \| `(channels-[a-z]+)` \| ([\d.]+) \| [^|]*?([\d.]+) \|", re.MULTILINE)
LAYER_OF_PACKAGE = {"channels-nats": "nats", "channels-redis": "redis"}
#: The image each layer's broker runs from in ci.yml.
BROKER_IMAGE = {"channels-nats": "nats", "channels-redis": "redis"}


def _layer_table() -> dict[str, tuple[str, str]]:
    text = (ROOT / "docs" / "COMPATIBILITY.md").read_text(encoding="utf-8")
    return {package: (version, broker) for package, version, broker in LAYER_ROW.findall(text)}


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_the_layer_table_says_the_versions_the_lock_tests():
    """Bumping a layer in uv.lock without rerunning its E2E lane and updating the table fails here."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked: dict[str, list[str]] = {}
    for package in lock["package"]:
        locked.setdefault(package["name"], []).append(package["version"])
    table = {package: version for package, (version, _) in _layer_table().items()}

    assert set(table) == set(LAYER_OF_PACKAGE), table
    # One version per layer: a lock that forks by Python version runs the E2E lane on one of
    # them and the other only where nobody looks (#150).
    assert {name: locked[name] for name in table} == {name: [version] for name, version in table.items()}


def test_ci_runs_e2e_on_every_layer_the_table_supports():
    assert set(_matrix("test-e2e", "layer")) == {LAYER_OF_PACKAGE[name] for name in _layer_table()}


def test_ci_runs_the_broker_releases_the_table_names():
    """Every image ci.yml starts a broker from, as a service or to lift nats-server out of, is the table's release."""
    for package, (_, release) in _layer_table().items():
        name = BROKER_IMAGE[package]
        tags = {
            service["image"].partition(":")[2]
            for job in CI_JOBS.values()
            for service in job.get("services", {}).values()
            if service["image"].partition(":")[0] == name
        }
        tags |= {tag for job in CI_JOBS.values() for tag in re.findall(rf"(?<![\w/:=-]){name}:(?!//)(\S+)", _runs(job))}

        assert tags, f"ci.yml never runs {name}"
        assert {tag.partition("-")[0] for tag in tags} == {release}, (
            f"{package}: the table says {release}, ci.yml {tags}"
        )


def test_every_ci_job_that_runs_the_nats_layer_tests_has_nats_server():
    """tests/test_nats_layer.py fails in CI without the binary, rather than skipping the floor's only check (#132)."""
    unit = {
        name
        for name, job in CI_JOBS.items()
        if re.search(r"make (ci-test|test-latest|test-lowest)(?![\w-])", _runs(job))
    }

    assert unit >= {"test", "test-latest", "test-lowest"}, unit
    assert {name for name in unit if "NATS_SERVER=" not in _runs(CI_JOBS[name])} == set()
