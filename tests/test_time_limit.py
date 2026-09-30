"""A hung test stops the run with every thread's stack (#148, ``testproj/time_limit.py``).

The case that matters is the one the ready-made plugins miss: a test that has
already failed and then hangs in its teardown. Each run here is a pytest of its
own, so the stop ends that process and not this one.
"""

import os
import re
import subprocess
import sys
import time
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


OWNS_A_CHILD = """
import pathlib
import subprocess
import sys
import threading

from testproj.time_limit import own


def test_hangs_with_a_child():
    child = own(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
    pathlib.Path("child.pid").write_text(str(child.pid))
    threading.Event().wait()
"""

AT_THE_PROMPT = """
def test_stops_at_a_breakpoint():
    breakpoint()
"""

UNDER_A_DEBUGGER = """
import time


def test_runs_under_a_debugger():
    time.sleep(1.5)
"""


def _command(tmp_path: Path, source: str, limit: float) -> list[str]:
    (tmp_path / "test_probe.py").write_text(source)
    (tmp_path / "pytest.ini").write_text(f"[pytest]\ntest_time_limit = {limit}\n")
    return [sys.executable, "-m", "pytest", "-p", "testproj.time_limit", "-p", "no:django", "-p", "no:cacheprovider"]


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT / "tests")}


def _pytest(tmp_path: Path, source: str, limit: float, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [*_command(tmp_path, source, limit), *args],
        cwd=tmp_path,
        env=_env(),
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


def test_a_stop_says_how_to_turn_it_off(tmp_path):
    result = _pytest(tmp_path, FAILS_THEN_HANGS, limit=1)

    assert "-o test_time_limit=0" in result.stderr, result.stderr


def test_a_stop_terminates_the_children_it_was_handed(tmp_path):
    result = _pytest(tmp_path, OWNS_A_CHILD, limit=1)

    assert result.returncode == 1, result.stdout + result.stderr
    pid = int((tmp_path / "child.pid").read_text())
    # The signal is sent before the run ends; the child's own exit takes a moment.
    deadline = time.monotonic() + 10
    while _alive(pid):
        assert time.monotonic() < deadline, f"child {pid} outlived the stopped run"
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_debugger_prompt_is_not_a_hang(tmp_path):
    """``breakpoint()`` (and ``--pdb``) enter pytest's pdb: the timer stops there."""
    proc = subprocess.Popen(
        _command(tmp_path, AT_THE_PROMPT, limit=1),
        cwd=tmp_path,
        env=_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Sit at the prompt past the limit, as someone reading the stack would.
    time.sleep(2.5)
    stdout, stderr = proc.communicate("continue\n", timeout=30)

    assert proc.returncode == 0, stdout + stderr
    assert "took more than" not in stderr


def test_a_test_under_another_debugger_has_no_limit(tmp_path):
    """An IDE's debugger shows as the trace function of the thread the test starts on."""
    (tmp_path / "pydevd_probe.py").write_text("def trace(frame, event, arg):\n    return None\n")
    (tmp_path / "conftest.py").write_text("import sys\n\nfrom pydevd_probe import trace\n\nsys.settrace(trace)\n")
    result = subprocess.run(
        _command(tmp_path, UNDER_A_DEBUGGER, limit=1),
        cwd=tmp_path,
        env={**_env(), "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(tmp_path)])},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "took more than" not in result.stderr
