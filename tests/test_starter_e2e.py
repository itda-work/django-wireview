"""The starter template's first page in a browser, served the ways tutorial 01 serves it (#131, #151).

``tests/test_project_template.py`` drives the generated ``asgi.py`` in-process.
That misses what only a real ``runserver`` shows: whether it is daphne's ASGI
command or staticfiles' WSGI one, whether the static files reach the browser,
and whether the page's own bundle connects and answers typing. When one piece
of that wiring is off the page draws and nothing answers, so only a browser
sees it.

The project is made in a scratch directory by ``startproject --template``, then
``migrate`` and ``runserver`` -- or the uvicorn line the tutorial offers in its
place -- run in a process of their own, as the tutorial says. uvicorn serves the
project's ``application`` alone, without runserver's static files handler.
The server is handed to ``testproj.time_limit.own()`` so a stopped run
still stops it.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import typing as t
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from testproj.e2e_browser import WAIT_TIMEOUT, expect_text, open_live
from testproj.server_process import free_port, held_port, wait_until_serving
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
            raise AssertionError(f"{failure}\n\nThe starter's server printed:\n{self.output()}") from None


def _runserver(port: int) -> list[str]:
    # --noreload: the autoreloader serves from a child process, which
    # terminating this one would leave running.
    return ["manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"]


def _uvicorn(port: int) -> list[str]:
    """The line tutorial 01 gives in place of runserver, less ``--reload`` for the same reason."""
    return ["-m", "uvicorn", "mysite.asgi:application", "--host", "127.0.0.1", "--port", str(port)]


@contextlib.contextmanager
def _running(project: Path, port: int, command: t.Callable[[int], list[str]] = _runserver) -> t.Iterator[Starter]:
    """The project served on ``port`` by ``command``, stopped on the way out."""
    log = project / f"{command.__name__.lstrip('_')}-{port}.log"
    with log.open("wb") as out:
        proc = own(
            subprocess.Popen(
                [sys.executable, *command(port)],
                cwd=project,
                env={**_env(), "PYTHONUNBUFFERED": "1"},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        )
    try:
        wait_until_serving(port, proc, log, WAIT_TIMEOUT)
        yield Starter(f"http://127.0.0.1:{port}", log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            proc.kill()
            proc.wait()
        disown(proc)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("starter")
    _manage(root, "-m", "django", "startproject", "mysite", str(root), "--template", str(TEMPLATE))
    _manage(root, "manage.py", "migrate")
    return root


@pytest.fixture(scope="module")
def starter(project) -> t.Iterator[Starter]:
    with _running(project, free_port()) as running:
        yield running


@pytest.fixture(scope="module")
def uvicorn_starter(project) -> t.Iterator[Starter]:
    with _running(project, free_port(), _uvicorn) as running:
        yield running


def _first_page_answers_typing(page, starter: Starter) -> None:
    """Both components join, typing reaches the server and back, and the bundle came from the server."""
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


def test_the_first_page_is_live_and_answers_typing(page, starter):
    """Tutorial 01's page from daphne's runserver."""
    _first_page_answers_typing(page, starter)
    # Not staticfiles' WSGI runserver, where the page draws and no socket connects
    # (W013). The startup line itself: W013's hint quotes the phrase. Asserted
    # here rather than in a test of its own, which pytest-playwright's browser
    # parametrization would reorder into a second instance of the module fixtures.
    assert re.search(r"^Starting ASGI/Daphne version ", starter.output(), re.MULTILINE), starter.output()


def test_the_first_page_is_live_under_uvicorn(page, uvicorn_starter):
    """The same page from the uvicorn line tutorial 01 offers instead of runserver (Windows' way).

    uvicorn serves the project's ``application`` and nothing else: no static
    files handler of runserver's around it. The bundle was a 404 there and the
    page drew without a component joining.
    """
    _first_page_answers_typing(page, uvicorn_starter)


def test_a_port_another_server_holds_is_not_taken_for_the_starter(project, browser_name):
    """The port is free when chosen and runserver binds it later; in between another server can take it.

    Daphne then logs "Listen failure" and exits with 0, while the port answers --
    so "something answers on the port" is not "the starter is up". ``browser_name``
    keeps this in the browser parametrization's group, so the module fixtures are
    made once.
    """
    with held_port() as port:
        with pytest.raises(AssertionError, match="did not serve"):
            with _running(project, port):
                pass
