"""Two test runs in one checkout must not see each other.

Several agents verifying the same copy is the ordinary case now, and a shared test
database made each run fail the other in a different place every time: "attempt to
write a readonly database", "no such table" -- which reads as a flaky suite, not as
a collision (#125). ``make test-concurrent`` runs the whole thing twice at once;
these are the deterministic properties it depends on.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")])}

TEST_DB_NAME = """
import django
django.setup()
from django.conf import settings
print(settings.DATABASES["default"]["TEST"]["NAME"])
"""


def _test_db_name_in_a_new_process() -> str:
    result = subprocess.run(
        [sys.executable, "-c", TEST_DB_NAME],
        cwd=ROOT,
        env={**ENV, "DJANGO_SETTINGS_MODULE": "testproj.settings"},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip().splitlines()[-1]


def test_each_test_process_gets_its_own_database():
    names = {_test_db_name_in_a_new_process(), _test_db_name_in_a_new_process()}
    names.add(settings.DATABASES["default"]["TEST"]["NAME"])

    assert len(names) == 3, f"two test processes would share a database: {names}"


def test_the_test_database_is_a_file():
    """The E2E server answers from its own thread, so it needs a database it can open too."""
    name = settings.DATABASES["default"]["TEST"]["NAME"]

    assert name and ":memory:" not in name and "mode=memory" not in name


def test_pytest_starts_without_the_cache_provider():
    """``--ff`` in addopts made ``-p no:cacheprovider`` an unrecognised-argument error.

    Turning the cache off is how two concurrent runs avoid racing on
    ``.pytest_cache``, so it has to work.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", __file__],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
