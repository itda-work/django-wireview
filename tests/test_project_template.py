"""The starter template: from ``startproject`` to tutorial 01's page with nothing copied by hand (#131).

The wiring tutorial 01 asks for -- daphne above staticfiles, a channel layer,
``django.setup()`` before ``wireview.urls`` in ``asgi.py`` -- fails silently
when one piece is off: the page draws and nothing answers. The template writes
it all. These tests make a real project from it in a scratch directory, the way
the tutorial tells a reader to, and run it in its own process, so nothing of the
test project leaks in.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import wireview

pytestmark = pytest.mark.integration

#: Where the tutorial tells ``startproject --template`` to look.
TEMPLATE = Path(wireview.__file__).parent / "project_template"


def run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python *args`` in the generated project, as its own process."""
    env = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    return subprocess.run([sys.executable, *args], cwd=project, env=env, capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("starter")
    made = run(root, "-m", "django", "startproject", "mysite", str(root), "--template", str(TEMPLATE))
    assert made.returncode == 0, made.stderr
    return root


def test_the_template_ships_in_the_package():
    """The tutorial's path is the package's; ``make ci-build`` checks the wheel carries it."""
    assert (TEMPLATE / "manage.py-tpl").is_file()
    assert (TEMPLATE / "project_name" / "asgi.py-tpl").is_file()


def test_check_finds_nothing(project):
    result = run(project, "manage.py", "check")

    assert result.returncode == 0, result.stderr
    assert "System check identified no issues (0 silenced)." in result.stdout, result.stdout + result.stderr


def test_runserver_serves_asgi(project):
    """W013 only looks when the process is ``runserver``, so ask as runserver would."""
    probe = textwrap.dedent(
        """
        import os, sys
        sys.argv = ["manage.py", "runserver"]
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
        import django
        django.setup()
        from django.core.checks import run_checks
        print(sorted(message.id for message in run_checks(tags=["wireview"])))
        """
    )
    result = run(project, "-c", probe)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_first_page_answers_like_tutorial_01(project):
    """The page through the project's ``asgi.py``, then an input typed into it over the WebSocket."""
    probe = textwrap.dedent(
        """
        import asyncio, html, re
        from mysite.asgi import application
        from channels.testing import HttpCommunicator, WebsocketCommunicator
        from wireview.core.rendered import PROTOCOL_VERSION

        async def main():
            page = await HttpCommunicator(
                application, "GET", "/", headers=[(b"host", b"localhost")]
            ).get_response(timeout=30)
            assert page["status"] == 200, page["status"]
            body = page["body"].decode()
            for text in ("My First Wireview App", "Hello, World!", "Hello, Django!", "wireview/wireview.min.js"):
                assert text in body, text
            id, name, state = re.search(
                r'<div id="([^"]+)" data-name="([^"]+)" data-state="([^"]+)"', body
            ).groups()

            socket = WebsocketCommunicator(
                application,
                f"/__wireview__?vsn={PROTOCOL_VERSION}",
                headers=[(b"host", b"localhost:8000"), (b"origin", b"http://localhost:8000")],
            )
            connected, _ = await socket.connect(timeout=30)
            assert connected

            async def render():
                while True:
                    message = await socket.receive_json_from(timeout=30)
                    if message["command"] == "render":
                        return message

            await socket.send_json_to(
                {"command": "join", "payload": {"name": name, "state": html.unescape(state), "children": {}}}
            )
            await render()
            await socket.send_json_to({"command": "user_event", "payload": {
                "id": id, "command": "change_name",
                "implicit_args": {"name": ["Wireview"]}, "explicit_args": {},
            }})
            print(repr((await render())["payload"]["diff"]))
            await socket.disconnect()

        asyncio.run(main())
        """
    )
    result = run(project, "-c", probe)

    assert result.returncode == 0, result.stderr
    assert "Wireview" in result.stdout, result.stdout
