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
