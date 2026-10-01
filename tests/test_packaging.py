"""What ships, and what must not (#93)."""

import json
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def test_package_json_is_private_and_says_the_same_version():
    """The npm manifest only builds the bundle. ``private`` keeps it off npm by accident,
    and a second version number that drifts from the real one (it sat at 0.2.1 through
    0.4.0) says nothing true."""
    package = json.loads((ROOT / "package.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert package["private"] is True
    assert package["version"] == project["version"]


def test_the_classifiers_name_the_supported_range():
    """docs/COMPATIBILITY.md: the Django releases Django supports, on Python 3.12 and up."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    djangos = {c.rsplit(" :: ", 1)[1] for c in project["classifiers"] if c.startswith("Framework :: Django :: ")}
    pythons = {
        c.rsplit(" :: ", 1)[1] for c in project["classifiers"] if c.startswith("Programming Language :: Python :: 3.")
    }
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert all(f'"{v}"' in ci for v in djangos | pythons), "the CI matrix and the classifiers disagree"
    assert f"django>={min(djangos, key=lambda v: tuple(map(int, v.split('.'))))}" in project["dependencies"]


WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    import yaml  # djlint and pre-commit bring it with the dev extras

    # YAML 1.1 reads the bare key `on` as True.
    return {("on" if k is True else k): v for k, v in yaml.safe_load((WORKFLOWS / name).read_text()).items()}


#: What must pass before a tag reaches PyPI (#122): the tests, the tests on the
#: dependencies a fresh install resolves (#127) and on the lowest ones pyproject.toml
#: allows (#132), quality, and the package build.
GATE_JOBS = {"test", "test-latest", "test-lowest", "test-e2e", "lint", "typecheck", "build"}


def test_publishing_waits_for_the_whole_ci_workflow():
    """Before #122 a tag went from build straight to PyPI, with no test run at all."""
    release = _workflow("release.yml")
    ci = _workflow("ci.yml")
    jobs = release["jobs"]
    gates = {name for name, job in jobs.items() if job.get("uses") == "./.github/workflows/ci.yml"}

    assert "workflow_call" in ci["on"], "release.yml cannot call ci.yml"
    assert GATE_JOBS <= set(ci["jobs"]), f"ci.yml lost {GATE_JOBS - set(ci['jobs'])}"
    assert gates, "release.yml does not run ci.yml"
    assert all("if" not in jobs[g] for g in gates), "the gate is skipped on some runs"
    needs = jobs["publish"]["needs"]
    needs = {needs} if isinstance(needs, str) else set(needs)
    assert gates <= needs, "publish does not wait for the tests"
    assert "smoke" in needs, "publish does not wait for the wheel smoke test"
    assert "make ci-smoke" in [step.get("run") for step in jobs["smoke"]["steps"]]
    assert "if" not in jobs["smoke"], "the smoke test is skipped on some runs"


def test_the_build_starts_from_an_empty_dist():
    """``ci-smoke`` installs every wheel in ``dist/`` and ``ci-build`` checks the last by
    name, so a wheel an older build left there was smoked in place of the new one (it
    failed on the starter template, which older wheels do not have)."""
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\nci-build:", 1)[1].split("\n\n", 1)[0]

    assert "uv build --clear" in recipe


def test_the_development_status_says_whether_the_version_is_a_prerelease():
    """A final release says ``5 - Production/Stable`` and a pre-release does not. The
    classifier is bumped by hand in the release commit, and nothing read it: a 1.0.0 wheel
    would have gone to PyPI as ``4 - Beta``."""
    from packaging.version import Version

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    statuses = [c for c in project["classifiers"] if c.startswith("Development Status :: ")]
    stable = not Version(project["version"]).is_prerelease

    assert len(statuses) == 1, statuses
    assert (statuses[0] == "Development Status :: 5 - Production/Stable") == stable, (project["version"], statuses)
