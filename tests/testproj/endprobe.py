"""The test project as a server process whose components record their ``leaving()`` (#211).

``SlowJoin`` takes its time joining: ``test_join_disconnect.py`` leaves while it does (#216).

``test_server_shutdown.py`` runs it under daphne and uvicorn, joins a component
per socket and sends the server SIGTERM: what each hook began and finished is
in the file ``ENDPROBE_EVENTS`` names, a line per step, written as it happens.
"""

from __future__ import annotations

import asyncio
import os
import typing as t
from pathlib import Path

from django.template import Template

from testproj.asgi import application as project
from wireview import Component


def _mark(text: str) -> None:
    with Path(os.environ["ENDPROBE_EVENTS"]).open("a") as events:
        events.write(text + "\n")


class EndProbe(Component):
    """Its ``leaving()`` takes ``seconds``. Its template is inline: no file to name (tests/test_lsp_metadata.py)."""

    seconds: float = 0

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}></div>")

    async def leaving(self):
        _mark(f"start {self.id}")
        await asyncio.sleep(self.seconds)
        _mark(f"done {self.id}")


class SlowJoin(Component):
    """Its ``joined()`` takes ``seconds``: a page that leaves meanwhile is gone when its answer is written (#216)."""

    seconds: float = 0

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<div {% tag_header %}></div>")

    async def joined(self):
        _mark(f"joining {self.id}")
        await asyncio.sleep(self.seconds)
        _mark(f"joined {self.id}")


async def application(scope: dict[str, t.Any], receive: t.Any, send: t.Any) -> None:
    """The project's, saying on stdout which readiness token it answered.

    ``server_process.wait_until_serving`` looks for the token in the server's
    log; daphne writes no access log there.
    """
    if scope["type"] == "http" and scope.get("query_string", b"").startswith(b"ready="):
        print(f"served ?{scope['query_string'].decode()} ", flush=True)
    await project(scope, receive, send)
