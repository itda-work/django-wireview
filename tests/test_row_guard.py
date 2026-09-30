"""A test that leaves committed rows behind fails (#133, ``testproj/row_guard.py``).

Each case runs in a pytest of its own, so the leak it makes on purpose cannot
reach this run's database.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import pytest

from testproj.bookmarks.models import Bookmark


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_write_in_a_rolled_back_test():
    await Bookmark.objects.acreate(title="leak", url="https://example.com/leak")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_write_in_a_flushed_test():
    await Bookmark.objects.acreate(title="kept", url="https://example.com/kept")


@pytest.mark.django_db
def test_sync_write_in_a_rolled_back_test():
    Bookmark.objects.create(title="sync", url="https://example.com/sync")
"""


# The leak last: the last test is torn down with the database. One whose teardown
# breaks is counted all the same, so the test after it is not blamed.
LAST = """
import pytest

from testproj.bookmarks.models import Bookmark


@pytest.fixture
def breaks():
    yield
    raise RuntimeError("the teardown broke")


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_leak_whose_teardown_breaks(breaks):
    await Bookmark.objects.acreate(title="broken", url="https://example.com/broken")


@pytest.mark.django_db
def test_an_innocent_one_after_it():
    assert Bookmark.objects.count() == 1


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_last_one_leaks():
    await Bookmark.objects.acreate(title="last", url="https://example.com/last")
"""


def _pytest(directory: Path, source: str, *targets: str) -> str:
    (directory / "test_probe.py").write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "testproj.row_guard", "-p", "no:cacheprovider"]
        + ["--ds", "testproj.settings", "--nomigrations", "-rA", *(targets or ["test_probe.py"])],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")])},
        capture_output=True,
        text=True,
        timeout=50,
    )
    return result.stdout + result.stderr


@pytest.fixture(scope="module")
def output(tmp_path_factory) -> str:
    return _pytest(tmp_path_factory.mktemp("row_guard"), PROBE)


def test_an_async_write_under_a_rolled_back_test_fails_it(output):
    assert "ERROR test_probe.py::test_async_write_in_a_rolled_back_test" in output, output
    assert "left committed rows behind: {'bookmarks_bookmark': 1}" in output


@pytest.mark.parametrize("name", ["test_async_write_in_a_flushed_test", "test_sync_write_in_a_rolled_back_test"])
def test_a_write_that_does_not_outlive_its_test_passes(output, name):
    assert f"PASSED test_probe.py::{name}" in output, output
    assert f"ERROR test_probe.py::{name}" not in output, output


@pytest.fixture(scope="module")
def last(tmp_path_factory) -> str:
    return _pytest(tmp_path_factory.mktemp("row_guard_last"), LAST)


def test_the_last_test_of_a_run_is_seen(last):
    assert "ERROR test_probe.py::test_the_last_one_leaks" in last, last
    assert "test_the_last_one_leaks left committed rows behind: {'bookmarks_bookmark': 1}" in last, last


def test_a_teardown_that_breaks_keeps_its_leak(last):
    assert "ERROR test_probe.py::test_a_leak_whose_teardown_breaks - RuntimeError" in last, last
    assert "RuntimeError: the teardown broke" in last, last
    # Its leak is told beside its own error, and not laid on the next test.
    assert "test_a_leak_whose_teardown_breaks left committed rows behind" in last, last
    assert "PASSED test_probe.py::test_an_innocent_one_after_it" in last, last
    assert "ERROR test_probe.py::test_an_innocent_one_after_it" not in last, last


def test_one_test_run_alone_is_seen(tmp_path):
    output = _pytest(tmp_path, PROBE, "test_probe.py::test_async_write_in_a_rolled_back_test")
    assert "ERROR test_probe.py::test_async_write_in_a_rolled_back_test" in output, output
    assert "left committed rows behind: {'bookmarks_bookmark': 1}" in output, output
