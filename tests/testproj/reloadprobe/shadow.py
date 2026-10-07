"""A template directory put in front of the app's, for tests that change ``reloadprobe/box.html`` (#180)."""

from __future__ import annotations

import contextlib
import copy
import typing as t
from pathlib import Path

from django.conf import settings
from django.test import override_settings

#: ``reloadprobe/box.html`` at a version: the counter, with ``version`` in front of it
BOX = (
    "{{% load wireview %}}<div {{% tag_header %}}>"
    '<span data-testid="box-version">{version}</span> <span data-testid="box-count">{{{{ count }}}}</span>'
    ' <button {{% on "click" "bump" %}} data-testid="box-bump">bump</button>'
    ' <button {{% on "click" "slow_bump" %}} data-testid="box-slow">slow</button></div>'
)
#: The same, with a tag Django cannot compile
BROKEN = '{% load wireview %}<div {% tag_header %}>{% if %}<span data-testid="box-version">broken</span></div>'


class Shadow:
    """The directory, and the box template in it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.box = root / "reloadprobe" / "box.html"
        self.box.parent.mkdir(parents=True, exist_ok=True)

    def write(self, version: str) -> Path:
        self.box.write_text(BOX.format(version=version))
        return self.box

    def break_it(self) -> Path:
        self.box.write_text(BROKEN)
        return self.box


@contextlib.contextmanager
def shadowed(root: Path, version: str = "v1") -> t.Iterator[Shadow]:
    """``TEMPLATES`` with ``root`` first in its ``DIRS``, and the box written there at ``version``."""
    shadow = Shadow(root)
    shadow.write(version)
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [str(root), *templates[0].get("DIRS", [])]
    with override_settings(TEMPLATES=templates):
        yield shadow
