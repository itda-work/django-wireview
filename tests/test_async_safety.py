"""The suite runs with Django's async-safety check on (#120).

``DJANGO_ALLOW_ASYNC_UNSAFE`` used to be set by every test entry point. It turns
off the check that stops the ORM from running on an event loop, so the suite was
green while the bookmarks and notifications pages could not join on a real
server. These tests keep it from coming back quietly.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import SynchronousOnlyOperation

import conftest

ROOT = Path(__file__).resolve().parent.parent

#: Everything that starts the suite, a server for it, or the benchmarks.
ENTRY_POINTS = [
    "Makefile",
    "tests/e2e.sh",
    ".github/workflows/ci.yml",
    "bench/run.py",
    "tests/testproj/e2e_server.py",
    "tests/test_multiworker_uploads.py",
    "tests/test_distributed_uploads.py",
]


@pytest.mark.unit
@pytest.mark.parametrize("path", ENTRY_POINTS)
def test_no_entry_point_turns_the_check_off(path):
    assert not re.search(r"DJANGO_ALLOW_ASYNC_UNSAFE", (ROOT / path).read_text()), path


@pytest.mark.unit
def test_the_suite_refuses_to_start_with_the_flag_set():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_public_api.py"],
        cwd=ROOT,
        env={**os.environ, "DJANGO_ALLOW_ASYNC_UNSAFE": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "DJANGO_ALLOW_ASYNC_UNSAFE is set" in result.stderr


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_orm_on_the_event_loop_fails_here_as_it_does_on_a_server():
    with pytest.raises(SynchronousOnlyOperation):
        User.objects.count()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_e2e_exemption_covers_only_its_own_thread(monkeypatch):
    """The exemption names the Playwright thread; the live server's thread is not it."""
    monkeypatch.setattr(conftest, "_exempt_thread", threading.Thread())

    with pytest.raises(SynchronousOnlyOperation):
        User.objects.count()
