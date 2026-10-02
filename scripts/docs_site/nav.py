"""The documentation site's navigation as docs/site.toml declares it (#158, #159).

The one reading of docs/site.toml and docs/redirects.toml: the site build
(scripts/docs_site/build.py) renders from it, and the tests that hold the
documents to it import it -- tests/test_doc_site.py (every document classified,
slugs and URLs), tests/test_tutorials.py (the learning order, and the README
and closing lines written from it) and tests/test_doc_links.py (the heading
anchors). Standard library only: the tests run where the docs build's
dependencies are not installed.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

#: The files the site has to classify: a page, or an [exclude] pattern.
CLASSIFIED = ("docs/*.md", "README.md")  # a pathspec's * crosses "/"

#: Where the site is published, and where the files it does not publish live.
ORIGIN = "https://itda.work"
REPOSITORY = "https://github.com/itda-work/django-wireview"

#: A tutorial's closing navigation line, "[← 이전: …] | [목차](README.md) | [다음: … →]".
TUTORIAL_NAV = re.compile(r"^\[(← |목차\])")

SLUG = re.compile(r"[a-z0-9-]+")

# hatch_build.py's pin() rewrites the same three prefixes; tests/test_docs_site_build.py
# holds the two to the same result (hatch_build.py needs hatchling, this module nothing).
BRANCH_URLS = (
    f"{REPOSITORY}/blob/main/",
    f"{REPOSITORY}/tree/main/",
    "https://raw.githubusercontent.com/itda-work/django-wireview/main/",
)


def pin(text: str, tag: str) -> str:
    """Point the repository's ``main`` URLs in a text at a tag."""
    for url in BRANCH_URLS:
        text = text.replace(url, url.removesuffix("main/") + f"{tag}/")
    return text


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: link text kept, lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


class Slugger:
    """A page's heading anchors in order: a repeated heading gets "-1", "-2", ... as GitHub does."""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def __call__(self, heading: str) -> str:
        base = slug(heading)
        count = self.seen.get(base, 0)
        self.seen[base] = count + 1
        return base if count == 0 else f"{base}-{count}"


@dataclass(frozen=True)
class Page:
    source: str
    section: str  # "" for the home page
    slug: str  # "" for the home page and a section's index
    url: str
    level: str | None = None
    minutes: int | tuple[int, int] | None = None
    summary: str | None = None
    root: Path = field(default=ROOT, compare=False, repr=False)
    optional: bool = False  # under llms.txt's "## Optional": an agent may skip it

    @property
    def path(self) -> Path:
        return self.root / self.source

    @property
    def title(self) -> str:
        """The document's first line, "# <title>"."""
        first = self.path.read_text(encoding="utf-8").split("\n", 1)[0]
        assert first.startswith("# "), f"{self.source} does not start with a '# ' title"
        return first[2:].strip()

    @property
    def short_title(self) -> str:
        """The title up to its subtitle: "Poll 앱 - 실시간 투표" is "Poll 앱"."""
        return self.title.split(" - ", 1)[0]


@dataclass(frozen=True)
class Published:
    """A file the site publishes as it is, not as a page: the agent skill's Markdown."""

    source: str
    url: str

    def path(self, root: Path = ROOT) -> Path:
        return root / self.source


@dataclass(frozen=True)
class Site:
    """docs/site.toml and docs/redirects.toml of one checkout (the repository, or a test's copy)."""

    root: Path
    data: dict
    redirects: list[dict]

    @classmethod
    def load(cls, root: Path = ROOT) -> Site:
        data = tomllib.loads((root / "docs" / "site.toml").read_text(encoding="utf-8"))
        redirects = tomllib.loads((root / "docs" / "redirects.toml").read_text(encoding="utf-8"))["redirects"]
        return cls(root=root, data=data, redirects=redirects)

    @property
    def base(self) -> str:
        return self.data["base"]

    def pages(self) -> list[Page]:
        """Every published page in navigation order: the home page, then each section's index and pages."""
        base, root = self.base, self.root
        found = [Page(source=self.data["home"]["source"], section="", slug="", url=base, root=root)]
        for section in self.data["sections"]:
            top = f"{base}{section['slug']}/"
            optional = section.get("optional", False)
            found.append(
                Page(source=section["index"], section=section["slug"], slug="", url=top, root=root, optional=optional)
            )
            for page in section.get("pages", []):
                minutes = page.get("minutes")
                found.append(
                    Page(
                        source=page["source"],
                        section=section["slug"],
                        slug=page["slug"],
                        url=f"{top}{page['slug']}/",
                        level=page.get("level"),
                        minutes=tuple(minutes) if isinstance(minutes, list) else minutes,
                        summary=page.get("summary"),
                        root=root,
                        optional=optional,
                    )
                )
        return found

    @property
    def llms_url(self) -> str:
        """Where llms.txt is served: the site's map for an agent (#164)."""
        return f"{self.base}llms.txt"

    def skill(self) -> list[Published]:
        """The agent skill's Markdown as [skill] places it: SKILL.md first, then the rest by path."""
        if "skill" not in self.data:
            return []
        source, url = self.data["skill"]["source"], f"{self.base}{self.data['skill']['url']}"
        folder = self.root / source
        names = sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*.md"))
        names.sort(key=lambda name: name != "SKILL.md")
        return [Published(source=f"{source}/{name}", url=f"{url}{name}") for name in names]

    def public_urls(self) -> set[str]:
        """Every URL a link out there may name: the pages, llms.txt and the skill's files."""
        return {page.url for page in self.pages()} | {self.llms_url} | {file.url for file in self.skill()}

    def tutorials(self) -> list[Page]:
        """The tutorials in learning order (the tutorial section's pages, not its index)."""
        return [page for page in self.pages() if page.section == "tutorial" and page.slug]

    def levels(self) -> list[dict]:
        return self.data["levels"]

    def excluded(self, path: str) -> bool:
        """Whether an [exclude] pattern covers a repository path."""
        return any(_covers(pattern, path) for pattern in self.data["exclude"]["paths"])


@cache
def _repository() -> Site:
    return Site.load(ROOT)


def site() -> dict:
    return _repository().data


def redirects() -> list[dict]:
    return _repository().redirects


def pages() -> list[Page]:
    return _repository().pages()


def tutorials() -> list[Page]:
    return _repository().tutorials()


def levels() -> list[dict]:
    return _repository().levels()


def excluded(path: str) -> bool:
    return _repository().excluded(path)


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


def version(root: Path = ROOT) -> str:
    """The package version pyproject.toml declares; the site is built for the tag ``v<version>``."""
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


#: PEP 440 pre- and development releases: 1.0.0a1, 1.0.0b2, 1.0.0rc4, 1.0.0.dev3 (and their spellings).
_PRERELEASE = re.compile(r"(a|b|c|rc|alpha|beta|pre|preview|dev)[-_.]?\d*", re.IGNORECASE)


def is_prerelease(version: str) -> bool:
    """Whether a version is a PEP 440 pre-release, as packaging's ``Version.is_prerelease`` says."""
    public = version.split("+", 1)[0]
    return bool(_PRERELEASE.search(public.split("!", 1)[-1].lstrip("vV")))


def docs_group(root: Path = ROOT) -> list[str]:
    """The names of the docs dependency group's packages, for runs that install no project groups."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [re.match(r"[A-Za-z0-9._-]+", requirement).group(0) for requirement in data["dependency-groups"]["docs"]]  # type: ignore[union-attr]


if __name__ == "__main__":
    # `make test-latest` and `make test-lowest` run outside the project, where uv installs no groups:
    # this prints the --with arguments that bring the docs build's packages along.
    print(" ".join(f"--with {name}" for name in docs_group()))
