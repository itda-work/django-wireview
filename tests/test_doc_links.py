"""Guard: every relative link in the repository's Markdown reaches a file, and its anchor a heading.

Anchors broke without a sound: a heading reworded, a section renamed, and the link that
pointed at it opened the top of the page. The release review found them by running its
own checker over the files it touched; this runs it over every tracked document.
"""

import re
import subprocess
from functools import cache
from pathlib import Path
from urllib.parse import unquote

import pytest

from scripts.docs_site.nav import Slugger, slug

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

FENCE = re.compile(r"^(```|~~~)")
# [text](target) and [text](target "title"); an image's ![alt](src) is the same shape.
LINK = re.compile(r"\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
HTML_ANCHOR = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")


def _documents() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [ROOT / path for path in tracked if (ROOT / path).is_file()]


def _prose(path: Path) -> list[str]:
    """The lines outside fenced code blocks, with inline code removed."""
    lines, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.lstrip()):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(re.sub(r"`[^`]*`", lambda m: m.group(0) if "](" not in m.group(0) else "", line))
    return lines


@cache
def anchors(path: Path) -> frozenset[str]:
    """The anchors GitHub gives the headings (the site's heading ids are the same), and the HTML anchors."""
    found: set[str] = set()
    slugger = Slugger()
    for line in _prose(path):
        found.update(HTML_ANCHOR.findall(line))
        if match := HEADING.match(line):
            found.add(slugger(match.group(1)))
    return frozenset(found)


def _broken(path: Path) -> list[str]:
    broken = []
    for number, line in enumerate(_prose(path), 1):
        for target in LINK.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("//"):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / unquote(file_part)).resolve() if file_part else path
            if not dest.exists():
                broken.append(f"{target} (no such file)")
            elif anchor and dest.suffix == ".md" and unquote(anchor).lower() not in anchors(dest):
                broken.append(f"{target} (no such heading)")
    return broken


@pytest.mark.parametrize("doc", _documents(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_and_anchor_resolves(doc: Path):
    assert _broken(doc) == []


def test_the_slug_is_githubs():
    """The anchors the documents already rely on, as GitHub writes them."""
    assert slug("9. `AUTO_BROADCAST`는 `senders`에 적은 모델만 알린다 (보안)") == (
        "9-auto_broadcast는-senders에-적은-모델만-알린다-보안"
    )
    assert slug("1.0.0rc4에서 1.0으로") == "100rc4에서-10으로"
    assert slug("WebSocket의 Origin") == "websocket의-origin"
