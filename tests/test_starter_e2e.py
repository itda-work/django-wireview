"""The starter template's first page in a browser, served the way tutorial 01 serves it (#131, #151).

``tests/test_project_template.py`` drives the generated ``asgi.py`` in-process.
That misses what only a real ``runserver`` shows: whether it is daphne's ASGI
command or staticfiles' WSGI one, whether the static files reach the browser,
and whether the page's own bundle connects and answers typing. When one piece
of that wiring is off the page draws and nothing answers, so only a browser
sees it.

The project is made in a scratch directory by ``startproject --template``, then
``migrate`` and ``runserver`` run in a process of their own, as the tutorial
says. The server is handed to ``testproj.time_limit.own()`` so a stopped run
still stops it.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import sys
import time
import typing as t
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live
from testproj.time_limit import disown, own

import wireview

pytestmark = pytest.mark.e2e

TEMPLATE = Path(wireview.__file__).parent / "project_template"

#: What a component on the starter page looks like once it has joined.
LIVE = '[data-name="XHello"][data-is-live="true"]'

#: Lines in the server's output that mean something went wrong on its side.
#: ``wireview.W`` is a system check warning, W013 above all: runserver is WSGI.
TROUBLE = ("Traceback", "ERROR", "Exception inside application", "wireview.W")


def _env() -> dict[str, str]:
    """The generated project's own settings, nothing of the test project's."""
    return {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}


def _manage(project: Path, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args], cwd=project, env=_env(), capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _free_port() -> int:
    # runserver binds its own socket, so the port cannot be handed over as serve()
    # does. Chosen by the OS rather than picked; the gap until runserver binds it
    # is the one left.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Starter:
    """A running ``runserver`` of the generated project, and what it has printed."""

    def __init__(self, url: str, log: Path) -> None:
        self.url = url
        self.log = log

    def output(self) -> str:
        return self.log.read_text(errors="replace")

    def trouble(self) -> list[str]:
        return [line for line in self.output().splitlines() if any(mark in line for mark in TROUBLE)]

    @contextlib.contextmanager
    def reporting(self) -> t.Iterator[None]:
        """Fail with what the server printed, as ``e2e_browser`` does with ``server_errors()``.

        The server is another process, so its log is the only place a socket
        that died on an exception differs from a slow page.
        """
        try:
            yield
        # wait_for_selector times out with Playwright's own error, not an AssertionError.
        except (AssertionError, PlaywrightTimeout) as failure:
            raise AssertionError(f"{failure}\n\nThe starter's runserver printed:\n{self.output()}") from None


def _wait_until_listening(port: int, proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"runserver exited with {proc.returncode}:\n{log.read_text(errors='replace')}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"runserver did not listen within {WAIT_TIMEOUT:g}s:\n{log.read_text(errors='replace')}")


@pytest.fixture(scope="module")
def starter(tmp_path_factory) -> t.Iterator[Starter]:
    root = tmp_path_factory.mktemp("starter")
    _manage(root, "-m", "django", "startproject", "mysite", str(root), "--template", str(TEMPLATE))
    _manage(root, "manage.py", "migrate")

    port = _free_port()
    log = root / "runserver.log"
    with log.open("wb") as out:
        # --noreload: the autoreloader serves from a child process, which
        # terminating this one would leave running.
        proc = own(
            subprocess.Popen(
                [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                cwd=root,
                env={**_env(), "PYTHONUNBUFFERED": "1"},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        )
    try:
        _wait_until_listening(port, proc, log)
        yield Starter(f"http://127.0.0.1:{port}", log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            proc.kill()
            proc.wait()
        disown(proc)


def test_the_first_page_is_live_and_answers_typing(page, starter):
    """Tutorial 01's page: both components join, and typing reaches the server and back."""
    # By path: the header asks for the bundle with a ?v= cache buster.
    statics: dict[str, int] = {}
    page.on("response", lambda r: statics.setdefault(urlsplit(r.url).path, r.status))
    crashes: list[str] = []
    page.on("pageerror", lambda error: crashes.append(str(error)))

    with starter.reporting():
        open_live(page, f"{starter.url}/", LIVE)
        heading = page.locator("h1").first
        expect_text(heading, "My First Wireview App")
        greetings = page.locator(f"{LIVE} h1")
        expect_text(greetings.nth(0), "Hello, World!")
        expect_text(greetings.nth(1), "Hello, Django!")

        page.locator(f"{LIVE} input[name=name]").nth(0).fill("Wireview")

        expect_text(greetings.nth(0), "Hello, Wireview!")
        expect_text(greetings.nth(1), "Hello, Django!")

    assert statics.get("/static/wireview/wireview.min.js") == 200, statics
    assert crashes == []
    assert starter.trouble() == [], starter.output()


def test_runserver_is_daphne(starter):
    """Not staticfiles' WSGI runserver: then the page draws and no socket connects (W013)."""
    # The startup line itself: W013's hint quotes the phrase, so it is in the output
    # exactly when runserver is not daphne's.
    assert re.search(r"^Starting ASGI/Daphne version ", starter.output(), re.MULTILINE), starter.output()
