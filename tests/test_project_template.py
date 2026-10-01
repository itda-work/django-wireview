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


def test_uploads_have_their_endpoint(project):
    """The starter page uploads nothing, so only this sees ``wireview.urls`` left out: uploads would 404.

    The path is the one ``Component`` hands the client, at the root.
    """
    probe = textwrap.dedent(
        """
        import os
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
        import django
        django.setup()
        from django.urls import resolve
        print(resolve("/__wireview_upload__/conn/c1/avatar/").url_name)
        """
    )
    result = run(project, "-c", probe)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "wireview_upload"


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


#: The bundle every page asks for. Without it the page draws and nothing joins.
BUNDLE = "/static/wireview/wireview.min.js"


@pytest.mark.parametrize(("debug", "status"), [(True, 200), (False, 404)])
def test_asgi_serves_the_bundle_in_debug(project, debug, status):
    """``uvicorn mysite.asgi:application`` serves exactly this ``application``.

    runserver wraps the project in a static files handler of its own; uvicorn
    does not, and Django's ASGI handler serves no static files. The tutorial
    offers uvicorn as the way on Windows, and there the bundle was a 404 and the
    page dead with no check to say so. Outside DEBUG the files are the web
    server's to serve, as with runserver.
    """
    probe = textwrap.dedent(
        f"""
        import asyncio, os
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
        import django
        django.setup()
        from django.conf import settings
        settings.DEBUG = {debug}
        settings.ALLOWED_HOSTS = ["localhost"]
        from mysite.asgi import application

        # As a server calls it: channels' HttpCommunicator wants a "body" in every
        # message, which ASGI leaves optional and the static files handler omits.
        async def main():
            sent, requests = [], [{{"type": "http.request", "body": b"", "more_body": False}}]
            async def receive():
                if requests:
                    return requests.pop()
                await asyncio.Future()  # no disconnect: the handler cancels this when done
            async def send(message):
                sent.append(message)
            scope = {{
                "type": "http", "asgi": {{"version": "3.0"}}, "http_version": "1.1", "method": "GET",
                "scheme": "http", "path": "{BUNDLE}", "raw_path": b"{BUNDLE}", "query_string": b"",
                "root_path": "", "headers": [(b"host", b"localhost")], "server": ("localhost", 80),
                "client": ("127.0.0.1", 1),
            }}
            await application(scope, receive, send)
            status = sent[0]["status"]
            body = b"".join(m.get("body", b"") for m in sent[1:])
            print(status, len(body))

        asyncio.run(main())
        """
    )
    result = run(project, "-c", probe)

    assert result.returncode == 0, result.stderr
    got, size = result.stdout.split()
    assert int(got) == status, result.stdout + result.stderr
    if status == 200:
        assert int(size) > 1000


def test_what_the_project_makes_for_itself_is_ignored(project):
    """In DEBUG every ``manage.py`` command writes ``hello/live.pyi`` (AUTO_GENERATE_STUBS), and
    ``migrate`` the database: neither belongs in the reader's first commit.
    """
    assert run(project, "manage.py", "check").returncode == 0
    assert (project / "hello" / "live.pyi").is_file(), "the stub this ignore rule is for"
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    made = ["hello/live.pyi", "db.sqlite3", "hello/__pycache__/live.cpython-312.pyc", ".wireview/metadata.json"]
    ignored = subprocess.run(["git", "check-ignore", *made], cwd=project, capture_output=True, text=True)

    assert ignored.stdout.split() == made, ignored.stdout + ignored.stderr
    kept = subprocess.run(["git", "check-ignore", "hello/live.py", "manage.py"], cwd=project, capture_output=True)
    assert kept.returncode == 1, kept.stdout
