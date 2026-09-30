"""A hung test stops the run with every thread's stack (#148, ``testproj/time_limit.py``).

The case that matters is the one the ready-made plugins miss: a test that has
already failed and then hangs in its teardown. Each run here is a pytest of its
own, so the stop ends that process and not this one.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent

FAILS_THEN_HANGS = """
import threading

import pytest


@pytest.fixture
def stuck():
    yield
    threading.Event().wait()


def test_fails_then_hangs(stuck):
    assert False
"""

TWO_WITHIN = """
import time


def test_first():
    time.sleep(0.6)


def test_second():
    time.sleep(0.6)
"""


def _pytest(tmp_path: Path, source: str, limit: float) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_probe.py").write_text(source)
    (tmp_path / "pytest.ini").write_text(f"[pytest]\ntest_time_limit = {limit}\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "testproj.time_limit", "-p", "no:django", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
        capture_output=True,
        text=True,
        # Well inside this run's own limit, so a stop that never comes fails this test.
        timeout=30,
    )
    return result


def test_a_test_that_hangs_after_failing_stops_the_run_with_the_stacks(tmp_path):
    result = _pytest(tmp_path, FAILS_THEN_HANGS, limit=1)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "test_probe.py::test_fails_then_hangs took more than 1s" in result.stderr
    # The stack names where it hangs, not just that it did.
    assert re.search(r'test_probe\.py", line \d+ in stuck', result.stderr), result.stderr


def test_the_limit_is_per_test_not_per_run(tmp_path):
    """Two tests within the limit each, past it together: the timer ends with each test."""
    result = _pytest(tmp_path, TWO_WITHIN, limit=1)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "took more than" not in result.stderr
