"""tests/e2e.sh passes pytest the paths it is given, and its defaults only without them.

The script always put ``tests examples`` in front of its arguments, so
``./tests/e2e.sh tests/test_streams_e2e.py`` collected every E2E suite and ran
them all. These tests run the script with ``uv`` swapped for a stub that records
what it was asked to run.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.unit

DEFAULTS = ["tests", "examples"]


def run_script(tmp_path: Path, *args: str) -> list[str]:
    """The arguments tests/e2e.sh hands ``uv run pytest`` for ``args``."""
    record = tmp_path / "argv"
    stub = tmp_path / "uv"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > {record}\n')
    os.chmod(stub, 0o755)
    subprocess.run(
        ["bash", str(ROOT / "tests" / "e2e.sh"), *args],
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "WIREVIEW_TEST_LAYER": "memory"},
        check=True,
        capture_output=True,
        timeout=60,
    )
    argv = record.read_bytes().split(b"\0")[:-1]
    assert [a.decode() for a in argv[:2]] == ["run", "pytest"]
    return [a.decode() for a in argv[2:]]


def paths_in(argv: list[str]) -> list[str]:
    """The positional arguments pytest gets ahead of its options."""
    return argv[: argv.index("-m")]


def test_without_a_path_the_defaults_run(tmp_path):
    assert paths_in(run_script(tmp_path)) == DEFAULTS


def test_options_and_their_values_are_not_paths(tmp_path):
    # `-k tests` names a directory that exists, as a keyword
    argv = run_script(tmp_path, "-k", "tests or streams", "-x", "-o", "test_time_limit=0", "-k", "tests")
    assert paths_in(argv) == DEFAULTS
    assert argv[-7:] == ["-k", "tests or streams", "-x", "-o", "test_time_limit=0", "-k", "tests"]


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_streams_e2e.py",
        "examples/todo",
        "tests/test_streams_e2e.py::test_dom_id_names_each_item",
        "tests/test_streams_e2e.py::test_dom_id_names_each_item[chromium]",
    ],
)
def test_a_path_replaces_the_defaults(tmp_path, path):
    argv = run_script(tmp_path, "-k", "dom_id", path)
    assert paths_in(argv) == []
    assert argv[-3:] == ["-k", "dom_id", path]


def test_an_option_value_taken_for_a_path_still_runs_every_e2e_suite():
    # `--cov wireview` drops the defaults (the script lists only some options
    # that take a value). That is safe while pytest collects from the rootdir:
    # with no `testpaths`, the rootdir holds the same suites as `tests examples`.
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert "testpaths" not in options, "tests/e2e.sh: an option value taken for a path now narrows the run"
