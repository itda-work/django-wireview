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


@pytest.fixture(scope="module")
def output(tmp_path_factory) -> str:
    directory = tmp_path_factory.mktemp("row_guard")
    (directory / "test_probe.py").write_text(PROBE)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "testproj.row_guard", "-p", "no:cacheprovider"]
        + ["--ds", "testproj.settings", "--nomigrations", "-rA", "test_probe.py"],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")])},
        capture_output=True,
        text=True,
        timeout=50,
    )
    return result.stdout + result.stderr


def test_an_async_write_under_a_rolled_back_test_fails_it(output):
    assert "ERROR test_probe.py::test_async_write_in_a_rolled_back_test" in output, output
    assert "left committed rows behind: {'bookmarks_bookmark': 1}" in output


@pytest.mark.parametrize("name", ["test_async_write_in_a_flushed_test", "test_sync_write_in_a_rolled_back_test"])
def test_a_write_that_does_not_outlive_its_test_passes(output, name):
    assert f"PASSED test_probe.py::{name}" in output, output
    assert f"ERROR test_probe.py::{name}" not in output, output
