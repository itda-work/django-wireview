"""The documentation site's navigation as docs/site.toml declares it (#158).

One reader for the tests that hold the documents to it: tests/test_doc_site.py
(every document classified, slugs and URLs) and tests/test_tutorials.py (the
learning order, and the README and closing lines written from it).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SITE = ROOT / "docs" / "site.toml"
REDIRECTS = ROOT / "docs" / "redirects.toml"

#: The files the site has to classify: a page, or an [exclude] pattern.
CLASSIFIED = ("docs/*.md", "README.md")  # a pathspec's * crosses "/"


@dataclass(frozen=True)
class Page:
    source: str
    section: str  # "" for the home page
    slug: str  # "" for the home page and a section's index
    url: str
    level: str | None = None
    minutes: int | tuple[int, int] | None = None
    summary: str | None = None

    @property
    def path(self) -> Path:
        return ROOT / self.source

    @property
    def title(self) -> str:
        """The document's first line, "# <title>"."""
        first = self.path.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first.startswith("# "), f"{self.source} does not start with a '# ' title"
        return first[2:].strip()

    @property
    def short_title(self) -> str:
        """The title up to its subtitle: "10. Poll 앱 - 실시간 투표" is "10. Poll 앱"."""
        return self.title.split(" - ", 1)[0]


@cache
def site() -> dict:
    return tomllib.loads(SITE.read_text(encoding="utf-8"))


@cache
def redirects() -> list[dict]:
    return tomllib.loads(REDIRECTS.read_text(encoding="utf-8"))["redirects"]


def pages() -> list[Page]:
    """Every published page in navigation order: the home page, then each section's index and pages."""
    data = site()
    base = data["base"]
    found = [Page(source=data["home"]["source"], section="", slug="", url=base)]
    for section in data["sections"]:
        root = f"{base}{section['slug']}/"
        found.append(Page(source=section["index"], section=section["slug"], slug="", url=root))
        for page in section.get("pages", []):
            minutes = page.get("minutes")
            found.append(
                Page(
                    source=page["source"],
                    section=section["slug"],
                    slug=page["slug"],
                    url=f"{root}{page['slug']}/",
                    level=page.get("level"),
                    minutes=tuple(minutes) if isinstance(minutes, list) else minutes,
                    summary=page.get("summary"),
                )
            )
    return found


def tutorials() -> list[Page]:
    """The tutorials in learning order (the tutorial section's pages, not its index)."""
    return [page for page in pages() if page.section == "tutorial" and page.slug]


def levels() -> list[dict]:
    return site()["levels"]


def excluded(path: str) -> bool:
    """Whether an [exclude] pattern covers a repository path."""
    return any(_covers(pattern, path) for pattern in site()["exclude"]["paths"])


def _covers(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return path == pattern


def tracked_documents() -> list[str]:
    """The files the site has to classify: tracked, or new and not ignored, so a new page fails before it is added."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", *CLASSIFIED],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [path for path in listed if (ROOT / path).is_file()]


def _hours(minutes: int) -> str:
    hours = minutes / 60
    return f"{hours:g}"


def human_minutes(minutes: int | tuple[int, int]) -> str:
    """How the tutorial index writes a time: 30 is "30분", 90 "1.5시간", (60, 120) "1-2시간"."""
    if isinstance(minutes, tuple):
        low, high = minutes
        if low >= 60:
            return f"{_hours(low)}-{_hours(high)}시간"
        return f"{human_minutes(low)}-{human_minutes(high)}"
    if minutes < 60:
        return f"{minutes}분"
    return f"{_hours(minutes)}시간"


def total_minutes(times: list[int | tuple[int, int]]) -> tuple[int, int]:
    low = sum(t[0] if isinstance(t, tuple) else t for t in times)
    high = sum(t[1] if isinstance(t, tuple) else t for t in times)
    return low, high


SLUG = re.compile(r"[a-z0-9-]+")
