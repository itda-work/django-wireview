"""The documentation site build, ``make docs-site`` (#159).

The site (itda.work/wireview/) is built from the release tag and shows only that release, so
the build is where a broken link, a vanished URL or a page that differs between two builds of
one commit has to fail. These tests build the repository's own docs, and small copies of a
docs tree where a gate has something to catch.

Builds take well under a second each, so this runs with ``make test``.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import subprocess
import tarfile
import threading
import urllib.request
from functools import partial
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path, PurePosixPath

import pytest

from scripts.docs_site import nav

pytestmark = pytest.mark.integration


def _missing(reason: str) -> None:
    """Skip on a developer's machine; fail where CI runs (GitHub Actions sets ``CI``), as test_nats_layer does."""
    if os.environ.get("CI", "").lower() not in ("", "0", "false"):
        pytest.fail(f"{reason}, and CI must run these tests", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


try:
    import markdown_it  # noqa: F401
    import mdit_py_plugins  # noqa: F401
    import pygments  # noqa: F401
except ImportError as error:  # pragma: no cover - the docs dependency group is a default group
    _missing(f"the docs dependency group is not installed ({error.name})")

from scripts.docs_site.build import _lead, build  # noqa: E402
from scripts.docs_site.bundle import DEFAULT_BUNDLE_DIR, PYPI_DIST, bundle, source_date_epoch  # noqa: E402
from scripts.docs_site.render import Linker  # noqa: E402
from scripts.docs_site.serve import INJECT, TEXT_TYPES, Handler, State, watched  # noqa: E402

ROOT = nav.ROOT
TAG = f"v{nav.version()}"
BLOB = f"{nav.REPOSITORY}/blob/{TAG}"
TREE = f"{nav.REPOSITORY}/tree/{TAG}"


def _hashes(out: Path) -> dict[str, str]:
    return {
        path.relative_to(out).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }


def _html(out: Path, url: str) -> str:
    return (out / url.lstrip("/") / "index.html").read_text(encoding="utf-8")


def _article(page_html: str) -> str:
    return page_html.split('<article class="prose">', 1)[1].split("</article>", 1)[0]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The repository's docs, built once for the tests that only read the output."""
    out = tmp_path_factory.mktemp("site") / "docs-site"
    result = build(out=out)
    return result


# --- the repository's own documents ----------------------------------------------------------------


def test_the_repository_docs_pass_every_gate(site):
    assert [str(problem) for problem in site.problems] == []


def test_the_same_commit_builds_the_same_bytes(site, tmp_path):
    again = build(out=tmp_path / "again")
    assert _hashes(again.out) == _hashes(site.out)


def test_every_page_has_its_html_and_its_markdown(site):
    for page in nav.pages():
        folder = site.out / page.url.lstrip("/")
        assert (folder / "index.html").is_file(), page.url
        assert (folder / "index.md").read_text(encoding="utf-8").startswith(f"# {page.title}\n"), page.url
        assert f'href="{page.url}index.md"' in _html(site.out, page.url)


def test_the_site_root_is_the_base_path(site):
    """``python -m http.server -d build/docs-site`` serves /wireview/ as itda.work does."""
    assert (site.out / "wireview" / "index.html").is_file()
    assert sorted(path.name for path in site.out.iterdir()) == ["wireview"]


def test_the_sitemap_lists_every_page_absolutely(site):
    sitemap = (site.out / "wireview" / "sitemap.xml").read_text(encoding="utf-8")
    locations = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locations == sorted(f"https://itda.work{page.url}" for page in nav.pages())
    assert "lastmod" not in sitemap


def test_the_version_file_is_the_tag(site):
    assert (site.out / "wireview" / "VERSION").read_text() == f"{TAG}\n"


def test_every_pages_header_names_the_release_and_links_its_changelog_section(site):
    """The site serves one release; each page says which at the top, not only in the footer (#196)."""
    changelog = (nav.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    (heading,) = re.findall(rf"^## (\[{re.escape(TAG[1:])}\] - [0-9-]+)$", changelog, re.MULTILINE)
    notes = f"{BLOB}/CHANGELOG.md#{nav.slug(heading)}"
    assert notes.endswith(f"#{TAG[1:].replace('.', '')}---{heading.rsplit(' ', 1)[1]}")
    for page in nav.pages():
        header = _html(site.out, page.url).split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
        (badge,) = re.findall(r'<a class="brand__version"\s+href="([^"]+)"[^>]*>([^<]+)</a>', header)
        assert badge == (notes, TAG), page.url


def test_assets_are_named_by_their_content(site):
    assets = sorted((site.out / "wireview" / "assets").iterdir())
    named = [path for path in assets if not path.name.endswith(".gz")]
    # The documents' images: README's overview and the benchmark charts (docs/images, #174)
    charts = {path.name for path in (ROOT / "docs" / "images").glob("*.svg")}
    assert charts
    assert {re.sub(r"\.[0-9a-f]{10}\.", ".", path.name) for path in named} == {
        "site.css",
        "site.js",
        "boot.js",
        "overview.jpg",
    } | charts
    for path in named:
        digest = path.name.split(".")[1]
        assert hashlib.sha256(path.read_bytes()).hexdigest().startswith(digest), path.name


#: What itda.work caches for good (website's Caddy block, #166): a name the content changes.
IMMUTABLE = re.compile(r"^/wireview/assets/.+\.[0-9a-f]{8,}\.(?:css|js)$")


def test_the_css_and_js_are_what_itda_work_caches_for_good(site):
    """The bundle contract (docs/implementation/docs-site-bundle.md): the hashed names match website's rule."""
    page = _html(site.out, "/wireview/")
    linked = re.findall(r'<(?:link rel="stylesheet"|script) (?:href|src)="(/wireview/assets/[^"]+)"', page)
    assert len(linked) == 3, linked
    assert all(IMMUTABLE.match(url) for url in linked), linked
    files = [path for path in (site.out / "wireview" / "assets").iterdir() if path.suffix in (".css", ".js")]
    assert files and all(IMMUTABLE.match(f"/wireview/assets/{path.name}") for path in files)


#: The one stylesheet the pages take from elsewhere: Pretendard, as itda.work's own pages do.
PRETENDARD = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@"


class _Loads(HTMLParser):
    """Every URL a page loads by itself: src attributes, and the stylesheets and icons it links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.urls += [value for name, value in attrs if name in ("src", "srcset", "poster", "data") and value]
        rel = (values.get("rel") or "").split()
        if tag == "link" and values.get("href") and {"stylesheet", "icon", "preload", "modulepreload"} & set(rel):
            self.urls.append(values["href"])


def test_the_pages_load_nothing_from_elsewhere_but_pretendard(site):
    """The bundle contract: no image, script or stylesheet comes from another origin (#166).

    itda.work serves the bundle under a CSP that allows its own origin and jsDelivr; an image
    from GitHub needed its own exception until the build served it.
    """
    outside = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE)
    found = []
    for path in sorted(site.out.rglob("*.html")):
        parser = _Loads()
        parser.feed(path.read_text(encoding="utf-8"))
        found += [(path.name, url) for url in parser.urls if outside.match(url) and not url.startswith(PRETENDARD)]
    for path in sorted(site.out.rglob("*.md")):
        images = re.findall(r"!\[[^\]]*\]\(([^()\s]+)", path.read_text(encoding="utf-8"))
        found += [(path.name, url) for url in images if outside.match(url) and not url.startswith(nav.ORIGIN + "/")]
    assert found == []


class _Pres(HTMLParser):
    """Each ``<pre>`` of a page, with the classes of the element it sits in."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.parents: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.parents.append(self.stack[-1][1] if self.stack else "")
        if tag not in self.VOID:
            self.stack.append((tag, dict(attrs).get("class") or ""))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def test_every_code_block_is_in_the_box_its_copy_button_goes_in(site):
    """site.js puts a copy button in each ``.code-block``; a ``<pre>`` outside one would have none (#173)."""
    total, outside = 0, []
    for path in sorted(site.out.rglob("*.html")):
        parser = _Pres()
        parser.feed(path.read_text(encoding="utf-8"))
        total += len(parser.parents)
        outside += [path.relative_to(site.out).as_posix() for parent in parser.parents if parent != "code-block"]
    assert total > 100 and outside == []


@pytest.mark.parametrize("source", ["```python\nx = 1\n```\n", "```\nplain\n```\n", "    indented\n"])
def test_a_code_block_of_every_kind_is_boxed(source):
    from scripts.docs_site.render import MD

    assert MD.render(source).startswith('<div class="code-block"><pre class="highlight"')


def test_the_readmes_image_is_in_the_build(site):
    """The home page's overview.jpg is a file of the build, and the .md names it as the HTML does."""
    image = (ROOT / "overview.jpg").read_bytes()
    src = re.search(r'<img src="([^"]+)" alt="Wireview 아키텍처 개요"', _html(site.out, "/wireview/"))
    assert src and src.group(1).startswith("/wireview/assets/overview."), src
    assert (site.out / src.group(1).lstrip("/")).read_bytes() == image
    assert f"![Wireview 아키텍처 개요]({src.group(1)})" in (site.out / "wireview" / "index.md").read_text(
        encoding="utf-8"
    )


def test_the_readmes_charts_are_in_the_build(site):
    """The home page's benchmark charts (#174) are files of the build, from the raw main URLs README names."""
    page = _html(site.out, "/wireview/")
    for chart in sorted((ROOT / "docs" / "images").glob("bench-fastapi-*.svg")):
        if chart.name == "bench-fastapi-loc.svg":
            continue  # docs/PERFORMANCE.md shows it, the README does not
        stem = chart.name.removesuffix(".svg")
        src = re.search(rf'<img src="(/wireview/assets/{stem}\.[0-9a-f]{{10}}\.svg)"', page)
        assert src, chart.name
        assert (site.out / src.group(1).lstrip("/")).read_bytes() == chart.read_bytes()


def test_every_text_file_has_its_gzip_twin(site):
    files = [path for path in site.out.rglob("*") if path.is_file()]
    text = [path for path in files if path.suffix in (".html", ".md", ".css", ".js", ".xml", ".txt")]
    assert text
    for path in text:
        packed = path.with_name(path.name + ".gz").read_bytes()
        assert gzip.decompress(packed) == path.read_bytes(), path
        # No time and no file name in the header: bytes 4-7 are the mtime, flag 0x08 a file name.
        assert packed[4:8] == b"\0\0\0\0" and not packed[3] & 0x08, path


def test_heading_ids_are_the_anchors_github_gives(site):
    """A link's anchor works the same on GitHub and on the site, Korean headings included."""
    from test_doc_links import HTML_ANCHOR, _prose, anchors

    for page in nav.pages():
        article = _article(_html(site.out, page.url))
        ids = set(re.findall(r'<h[1-6] id="([^"]+)"', article))
        html_anchors = {anchor for line in _prose(page.path) for anchor in HTML_ANCHOR.findall(line)}
        assert ids == anchors(page.path) - html_anchors, page.source


def test_the_markdown_and_the_html_link_to_the_same_places(site):
    """The two rewrites (tokens for the HTML, text for the .md) agree on every page."""
    for page in nav.pages():
        markdown = (site.out / page.url.lstrip("/") / "index.md").read_text(encoding="utf-8")
        prose = "\n".join(line for line in markdown.splitlines() if not nav.TUTORIAL_NAV.match(line))
        in_markdown = {urllib.request.unquote(target) for target in re.findall(r"\]\(([^()\s]+)\)", prose)}
        article = _article(_html(site.out, page.url))
        in_html = {urllib.request.unquote(href) for href in re.findall(r'(?:href|src)="([^"]+)"', article)}
        assert in_markdown <= in_html, (page.source, sorted(in_markdown - in_html))
        assert not [target for target in in_markdown if "/blob/main/" in target or "/tree/main/" in target]


# --- llms.txt and the published skill (#164) -------------------------------------------------------

LLMS_LINK = re.compile(r"^- \[([^\]]+)\]\(([^)\s]+)\)(?:: (.+))?$")


def _llms(out: Path) -> str:
    return (out / nav.Site.load().llms_url.lstrip("/")).read_text(encoding="utf-8")


def _llms_sections(text: str) -> dict[str, list[tuple[str, str, str | None]]]:
    sections: dict[str, list[tuple[str, str, str | None]]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:], [])
        elif current is not None and line:
            match = LLMS_LINK.match(line)
            assert match, f"not an llms.txt list item: {line!r}"
            current.append(match.groups())
    return sections


def test_llms_txt_has_the_shape_llmstxt_org_gives(site):
    text = _llms(site.out)
    lines = text.splitlines()
    assert lines[0] == "# django-wireview"
    assert lines[1] == "" and lines[2].startswith("> ")
    assert [line for line in lines if line.startswith("# ")] == [lines[0]]
    sections = list(_llms_sections(text))
    assert sections[0] == "에이전트 스킬" and sections[-1] == "Optional"
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_llms_txt_says_which_release_it_describes(site):
    assert f"이 목록은 django-wireview {TAG} 문서다." in _llms(site.out)
    assert "미리보기" not in _llms(site.out)


def test_llms_txt_lists_every_page_once_in_the_sites_order(site):
    """No hand-written index: a page added to docs/site.toml shows up, its Markdown linked absolutely."""
    sections = _llms_sections(_llms(site.out))
    listed = [url for name, items in sections.items() if name != "에이전트 스킬" for _, url, _ in items]
    pages = nav.pages()
    expected = [page for page in pages if not page.optional] + [page for page in pages if page.optional]
    assert listed == [f"https://itda.work{page.url}index.md" for page in expected]
    titles = {url: title for items in sections.values() for title, url, _ in items}
    for page in pages:
        assert titles[f"https://itda.work{page.url}index.md"] == page.title
    assert [url for _, url, _ in sections["Optional"]] == [
        f"https://itda.work{page.url}index.md" for page in pages if page.optional
    ]


def test_llms_txt_describes_a_tutorial_by_its_summary_and_a_page_by_its_first_paragraph(site):
    items = {url: description for items in _llms_sections(_llms(site.out)).values() for _, url, description in items}
    for page in nav.tutorials():
        assert items[f"https://itda.work{page.url}index.md"] == page.summary
    assert all(items.values()), [url for url, description in items.items() if not description]
    assert items["https://itda.work/wireview/reference/agent-skill/index.md"] == (
        "AI 코딩 에이전트가 wireview 앱을 제대로 짜게 만드는 배포용 스킬"
    )


def test_llms_txt_leads_with_the_skill(site):
    skill = _llms_sections(_llms(site.out))["에이전트 스킬"]
    assert [url for _, url, _ in skill] == [f"https://itda.work{file.url}" for file in nav.Site.load().skill()]
    assert skill[0][1].endswith("/SKILL.md")


def test_every_link_of_llms_txt_is_a_file_of_the_build(site):
    links = [url for items in _llms_sections(_llms(site.out)).values() for _, url, _ in items]
    assert links and all(url.startswith("https://itda.work/wireview/") for url in links)
    missing = [url for url in links if not (site.out / url.removeprefix("https://itda.work/")).is_file()]
    assert missing == []


def test_the_urls_the_readme_and_the_skill_page_name_are_files_of_the_build(site):
    """tests/test_agent_entry.py holds the README and the skill page to these URLs before the build."""
    for url in (nav.Site.load().llms_url, nav.Site.load().skill()[0].url):
        assert (site.out / url.lstrip("/")).is_file()


def test_llms_txt_sends_an_existing_project_to_the_first_tutorial(site):
    """Step 2 names tutorial 01's Markdown, absolutely: not the tutorial section's table of contents."""
    step2 = _llms(site.out).split("\n2. ", 1)[1].split("\n3. ", 1)[0]
    first = nav.tutorials()[0]
    assert step2.splitlines()[0].endswith(f"시작하기 튜토리얼은 이 문서다: https://itda.work{first.url}index.md")
    assert f"이 절을 먼저 적용한다: https://itda.work{first.url}#2-이미-있는-프로젝트에-붙이기\n" in step2
    assert (site.out / first.url.lstrip("/") / "index.md").is_file()
    index = next(page for page in nav.pages() if page.section == "tutorial" and not page.slug)
    assert f"{index.url}index.md" not in step2


def _site_page_urls(text: str, tag: str) -> str:
    """What the build makes of a skill file, done another way: hatch_build's pin, then pages to the site."""
    pages = {page.source: page.url for page in nav.pages()}

    def to_site(match: re.Match) -> str:
        path = match.group(1).rstrip("/")
        url = pages.get(path) or pages.get(f"{path}/README.md")
        return f"{nav.ORIGIN}{url}{match.group(2) or ''}" if url else match.group(0)

    pinned = nav.pin(text, tag)
    return re.sub(rf"{re.escape(nav.REPOSITORY)}/(?:blob|tree)/{tag}/([^\s|)>`#]*)(#[^\s|)>`]*)?", to_site, pinned)


def test_the_published_skill_is_the_wheels_with_its_pages_on_the_site(site):
    """Same tag as the wheel's copy (hatch_build.py pins main); a page of the site is linked there."""
    hatch_build = pytest.importorskip("hatch_build", reason="hatch_build.py needs hatchling (the dev extra)")
    for file in nav.Site.load().skill():
        source = file.path().read_text(encoding="utf-8")
        published = (site.out / file.url.lstrip("/")).read_text(encoding="utf-8")
        assert published == _site_page_urls(hatch_build.pin(source, TAG), TAG), file.source
        assert "/main/" not in published, file.source
    skill = (site.out / "wireview/agent/wireview/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: wireview\ndescription: ")
    assert "](./references/component.md)" in skill
    assert "https://itda.work/wireview/reference/checks/" in skill


def test_the_skills_relative_links_reach_its_files_where_it_is_published(site):
    for file in nav.Site.load().skill():
        text = (site.out / file.url.lstrip("/")).read_text(encoding="utf-8")
        for target in re.findall(r"\]\((\.[^)#\s]+)", text):
            reached = urllib.request.urljoin(file.url, target)
            assert (site.out / reached.lstrip("/")).is_file(), (file.source, target)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("짧다.", "짧다."),
        ("가" * 150 + ". " + "나" * 100 + ".", "가" * 150 + "."),
        ("가" * 250, "가" * 200 + "…"),
    ],
)
def test_a_long_first_paragraph_is_cut_at_a_sentence(text, expected):
    assert _lead(text) == expected


# --- link rewriting --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def linker():
    return Linker(nav.Site.load(), TAG)


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        # a page of the site, with and without an anchor
        ("docs/tutorials/03-todo-app.md", "12-live-search.md", "/wireview/tutorial/live-search/"),
        ("docs/tutorials/03-todo-app.md", "../features/csp.md#방법", "/wireview/reference/csp/#방법"),
        ("docs/features/csp.md", "./README.md", "/wireview/reference/"),
        ("docs/tutorials/09-testing-components.md", "../../README.md", "/wireview/"),
        ("docs/tutorials/README.md", "../tutorials", "/wireview/tutorial/"),
        # the same page
        ("docs/features/csp.md", "#방법", "#방법"),
        # documents the site does not publish, and other files: GitHub at the tag
        ("docs/features/README.md", "../implementation/js-commands.md", f"{BLOB}/docs/implementation/js-commands.md"),
        ("docs/ARCHITECTURE.md", "../CHANGELOG.md#unreleased", f"{BLOB}/CHANGELOG.md#unreleased"),
        ("docs/tutorials/03-todo-app.md", "../../examples/todo/", f"{TREE}/examples/todo"),
        ("docs/PERFORMANCE.md", "../bench/README.md", f"{BLOB}/bench/README.md"),
        # absolute links into main: a site page goes to the site, anything else to the tag
        (
            "README.md",
            f"{nav.REPOSITORY}/blob/main/docs/COMPATIBILITY.md#지원-범위",
            "/wireview/upgrade/compatibility/#지원-범위",
        ),
        ("README.md", f"{nav.REPOSITORY}/tree/main/docs/tutorials", "/wireview/tutorial/"),
        ("README.md", f"{nav.REPOSITORY}/tree/main/examples/todo", f"{TREE}/examples/todo"),
        ("README.md", f"{nav.REPOSITORY}/blob/main/LICENSE", f"{BLOB}/LICENSE"),
        # elsewhere: as written
        ("README.md", "https://docs.djangoproject.com/", "https://docs.djangoproject.com/"),
        ("README.md", f"{nav.REPOSITORY}/issues/87", f"{nav.REPOSITORY}/issues/87"),
        ("README.md", "mailto:dev@itda.work", "mailto:dev@itda.work"),
        # the agent skill: its copy on the site
        ("docs/features/agent-skill.md", "../../skills/wireview/SKILL.md", "/wireview/agent/wireview/SKILL.md"),
    ],
)
def test_a_link_goes_where_the_site_has_it(linker, source, target, expected):
    assert linker.target(target, source) == (expected, None)


def test_an_image_in_the_repository_is_served_by_the_site(linker):
    """Written relative or as a URL into main, an image of the repository is a hashed asset (#166)."""
    digest = hashlib.sha256((ROOT / "overview.jpg").read_bytes()).hexdigest()[:10]
    served = f"/wireview/assets/overview.{digest}.jpg"
    raw = "https://raw.githubusercontent.com/itda-work/django-wireview/main/overview.jpg"
    assert linker.target(raw, "README.md", image=True) == (served, None)
    assert linker.target("overview.jpg", "README.md", image=True) == (served, None)
    assert linker.target("../../overview.jpg", "docs/features/csp.md", image=True) == (served, None)
    assert linker.images["overview.jpg"] == served
    # A link to the image (not an image) is still a file on GitHub at the tag.
    assert linker.target("overview.jpg", "README.md") == (f"{BLOB}/overview.jpg", None)


def test_an_image_url_into_main_that_names_no_file_is_a_problem(linker):
    raw = "https://raw.githubusercontent.com/itda-work/django-wireview/main/nope.png"
    _, problem = linker.target(raw, "README.md", image=True)
    assert problem and "no such file" in problem


def test_a_link_to_a_missing_file_is_a_problem(linker):
    _, problem = linker.target("../features/nope.md", "docs/tutorials/03-todo-app.md")
    assert problem and "no such file" in problem


def test_pinning_is_hatch_builds(linker):
    """The site pins as the PyPI description does; two copies, one result."""
    hatch_build = pytest.importorskip("hatch_build", reason="hatch_build.py needs hatchling (the dev extra)")
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert nav.pin(text, TAG) == hatch_build.pin(text, TAG)
    assert nav.BRANCH_URLS == hatch_build.BRANCH_URLS


# --- tutorials ---------------------------------------------------------------------------------------


def test_the_tutorials_closing_line_becomes_the_pager(site):
    for page in nav.tutorials():
        article = _article(_html(site.out, page.url))
        assert "← 이전:" not in article and "[목차]" not in article, page.source


def _pager(page_html: str) -> str:
    pager = re.search(r'<nav class="pager".*?</nav>', page_html)
    assert pager
    return pager.group(0)


def test_every_page_links_its_neighbours_in_the_sidebars_order(site):
    pages = nav.pages()
    for at, page in enumerate(pages):
        page_html = _html(site.out, page.url)
        expected = []
        if at > 0:
            expected.append((pages[at - 1].url, "prev"))
        if at < len(pages) - 1:
            expected.append((pages[at + 1].url, "next"))
        assert re.findall(r'href="([^"]+)" rel="(prev|next)"', _pager(page_html)) == expected, page.source
        head = page_html.split("</head>", 1)[0]
        assert re.findall(r'<link rel="(prev|next)" href="([^"]+)">', head) == [(r, u) for u, r in expected]
    # The sequence crosses every section boundary: the home page starts it, the last section's last page ends it.
    assert 'rel="prev"' not in _pager(_html(site.out, pages[0].url))
    assert 'rel="next"' not in _pager(_html(site.out, pages[-1].url))


def _cards(page_html: str) -> dict[str, tuple[str, str]]:
    return {
        rel: (title, meta)
        for rel, title, meta in re.findall(
            r'rel="(prev|next)"><span class="pager__label">[^<]*</span>'
            r'<span class="pager__title">([^<]*)</span><span class="pager__meta">([^<]*)</span>',
            _pager(page_html),
        )
    }


def test_a_card_shows_a_tutorials_level_and_otherwise_the_section(site):
    tutorials = nav.tutorials()
    first, last = tutorials[0], tutorials[-1]
    sections = nav.site()["sections"]
    after_tutorials = sections[[s["slug"] for s in sections].index("tutorial") + 1]

    cards = _cards(_html(site.out, first.url))
    assert cards["prev"] == ("튜토리얼", "섹션 목차")  # the section's index, as the sidebar names it
    assert cards["next"][1] == "초급 · 1시간"  # a tutorial within the tutorials: level and time
    # The last tutorial leads across the boundary, into the next section's index.
    assert _cards(_html(site.out, last.url))["next"] == (after_tutorials["name"], "섹션 목차")
    assert _cards(_html(site.out, "/wireview/tutorial/"))["prev"] == ("소개", "")
    # Back across the boundary, a tutorial's card names the section it belongs to.
    assert _cards(_html(site.out, f"/wireview/{after_tutorials['slug']}/"))["prev"] == (
        last.short_title,
        f"튜토리얼 · {nav.levels()[-1]['name']} · {nav.human_minutes(last.minutes)}",
    )
    reference = [page for page in nav.pages() if page.section == "reference" and page.slug][1]
    assert {meta for _, meta in _cards(_html(site.out, reference.url)).values()} == {"레퍼런스"}


def test_the_sidebar_and_the_pager_name_tutorials_without_a_number(site):
    tutorials = nav.tutorials()
    page_html = _html(site.out, tutorials[1].url)
    sidebar = page_html.split('<nav id="sidebar"', 1)[1].split("</nav>", 1)[0]
    named = dict(re.findall(r'href="(/wireview/tutorial/[^"]+/)"[^>]*>([^<]*)</a>', sidebar))
    assert [named[page.url] for page in tutorials] == [page.short_title for page in tutorials]
    titles = [title for title, _ in _cards(page_html).values()] + list(named.values())
    assert not [title for title in titles if re.match(r"\d+\. ", title)]


def test_a_tutorial_shows_its_level_and_time(site):
    page = next(page for page in nav.tutorials() if page.slug == "streams-api")
    meta = re.search(r'<p class="page-meta">(.*?)</p>', _html(site.out, page.url)).group(1)
    assert "심화 (Deep Dive)" in meta and "예상 1-2시간" in meta


def test_the_published_markdown_keeps_the_closing_line_with_site_links(site):
    page = next(page for page in nav.tutorials() if page.slug == "todo-app")
    last = (site.out / page.url.lstrip("/") / "index.md").read_text(encoding="utf-8").rstrip().splitlines()[-1]
    assert last == (
        "[← 이전: Rating 앱](/wireview/tutorial/rating-app/) | [목차](/wireview/tutorial/) | "
        "[다음: Live Search →](/wireview/tutorial/live-search/)"
    )


# --- preview -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "preview"),
    [
        ("1.0.0rc4", True),
        ("1.0.0a1", True),
        ("1.0.0b2", True),
        ("1.0.0.dev3", True),
        ("1.0.0", False),
        ("1.0.1", False),
        ("2.0.0.post1", False),
    ],
)
def test_a_prerelease_is_what_packaging_says(version, preview):
    packaging = pytest.importorskip("packaging.version")
    assert nav.is_prerelease(version) is preview is packaging.Version(version).is_prerelease


@pytest.mark.parametrize(("version", "preview"), [("1.0.0rc4", True), ("1.0.0", False)])
def test_a_prerelease_builds_a_preview(tmp_path, version, preview):
    result = build(out=tmp_path / "out", version=version)
    assert result.preview is preview
    pages = list((tmp_path / "out").rglob("index.html"))
    assert len(pages) == len(nav.pages())
    for path in pages:
        text = path.read_text(encoding="utf-8")
        assert ('<meta name="robots" content="noindex">' in text) is preview, path
        assert ('class="preview-band"' in text) is preview, path
        if preview:
            assert f"django-wireview {version} 문서" in text
    assert (tmp_path / "out" / "wireview" / "VERSION").read_text() == f"v{version}\n"


# --- a docs tree of its own, for the gates ------------------------------------------------------------

SITE_TOML = """\
base = "/wireview/"

[skill]
source = "skills/s"
url = "agent/s/"

[home]
source = "README.md"

[[levels]]
slug = "intro"
name = "입문"
label = "Getting Started"

[[sections]]
slug = "guide"
name = "가이드"
index = "docs/guide.md"

[[sections.pages]]
source = "docs/next.md"
slug = "next"

[[sections]]
slug = "tutorial"
name = "튜토리얼"
index = "docs/tutorials.md"

[[sections.pages]]
source = "docs/start.md"
slug = "start"
level = "intro"
summary = "처음 만들기"

[exclude]
paths = ["docs/notes.md"]
"""


@pytest.fixture
def tree(tmp_path):
    """A repository with three pages, a note the site does not publish, and the URL list."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-02\n\n- First.\n")
    (root / "README.md").write_text("# 소개\n\n[가이드](docs/guide.md#설치)를 보세요.\n")
    (root / "docs" / "guide.md").write_text(
        "# 가이드\n\n설치를 안내한다.\n\n## 설치\n\n[다음](next.md) · [메모](notes.md)\n"
    )
    (root / "docs" / "next.md").write_text("# 다음\n\n다음 단계다.\n\n## 설치\n\n## 설치\n\n[둘째 설치](#설치-1)\n")
    (root / "docs" / "notes.md").write_text("# 메모\n")
    (root / "docs" / "tutorials.md").write_text("# 튜토리얼 목차\n\n튜토리얼의 차례다.\n\n[시작](start.md)\n")
    (root / "docs" / "start.md").write_text("# 시작\n\n처음 만든다.\n\n## 3. 이미 있는 프로젝트에 붙이기\n")
    (root / "docs" / "site.toml").write_text(SITE_TOML)
    (root / "docs" / "redirects.toml").write_text("redirects = []\n")
    (root / "skills" / "s" / "references").mkdir(parents=True)
    (root / "skills" / "s" / "SKILL.md").write_text(SKILL_MD)
    (root / "skills" / "s" / "references" / "a.md").write_text("# 참조\n\n[돌아가기](../SKILL.md)\n")
    (root / "docs" / "site-urls.txt").write_text("".join(f"{url}\n" for url in TREE_URLS))
    return root


SKILL_MD = """\
---
name: s
description: 스킬 설명.
---

# 스킬

| 할 일 | 읽을 것 |
|---|---|
| 참조하기 | [references/a.md](./references/a.md) |

정본: https://github.com/itda-work/django-wireview/blob/main/docs/guide.md#설치
"""

TREE_URLS = [
    "/wireview/",
    "/wireview/agent/s/SKILL.md",
    "/wireview/agent/s/references/a.md",
    "/wireview/guide/",
    "/wireview/guide/next/",
    "/wireview/llms.txt",
    "/wireview/tutorial/",
    "/wireview/tutorial/start/",
]


def _build(root: Path, **kwargs):
    return build(out=root.parent / "out", root=root, **kwargs)


def _problems(result) -> list[str]:
    return [str(problem) for problem in result.problems]


def test_a_small_tree_builds_clean(tree):
    result = _build(tree)
    assert _problems(result) == []
    out = result.out
    assert 'href="/wireview/guide/#설치"' in urllib.request.unquote(_html(out, "/wireview/"))
    assert f'href="{nav.REPOSITORY}/blob/v1.0.0/docs/notes.md"' in _html(out, "/wireview/guide/")
    assert '<h2 id="설치-1">' in _html(out, "/wireview/guide/next/")
    skill = (out / "wireview" / "agent" / "s" / "SKILL.md").read_text(encoding="utf-8")
    assert "](./references/a.md)" in skill and "정본: https://itda.work/wireview/guide/#설치\n" in skill
    assert _llms_sections(_llms_of(out)) == {
        "에이전트 스킬": [
            ("스킬", "https://itda.work/wireview/agent/s/SKILL.md", "스킬 설명."),
            ("참조", "https://itda.work/wireview/agent/s/references/a.md", "참조하기"),
        ],
        "소개": [("소개", "https://itda.work/wireview/index.md", "가이드를 보세요.")],
        "가이드": [
            ("가이드", "https://itda.work/wireview/guide/index.md", "설치를 안내한다."),
            ("다음", "https://itda.work/wireview/guide/next/index.md", "다음 단계다."),
        ],
        "튜토리얼": [
            ("튜토리얼 목차", "https://itda.work/wireview/tutorial/index.md", "튜토리얼의 차례다."),
            ("시작", "https://itda.work/wireview/tutorial/start/index.md", "처음 만들기"),
        ],
    }
    assert "시작하기 튜토리얼은 이 문서다: https://itda.work/wireview/tutorial/start/index.md\n" in _llms_of(out)
    existing = "https://itda.work/wireview/tutorial/start/#3-이미-있는-프로젝트에-붙이기"
    assert f"이 절을 먼저 적용한다: {existing}\n" in _llms_of(out)


def test_a_release_without_its_changelog_section_fails_the_build(tree):
    """The header's version links to the release's section of CHANGELOG.md (#196)."""
    (tree / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [0.9.0] - 2025-12-01\n")
    assert _problems(_build(tree)) == [
        'CHANGELOG.md: no "## [1.0.0]" section: the version in every page\'s header links there'
    ]


def test_a_first_tutorial_without_the_existing_project_section_fails_the_build(tree):
    (tree / "docs" / "start.md").write_text("# 시작\n\n처음 만든다.\n\n## 3. 붙이기\n")
    assert _problems(_build(tree)) == [
        'docs/start.md: no "## … 이미 있는 프로젝트에 붙이기" section: llms.txt sends a project there'
    ]


def _llms_of(out: Path) -> str:
    return (out / "wireview" / "llms.txt").read_text(encoding="utf-8")


def test_a_skill_link_that_leads_nowhere_fails_the_build_at_its_line(tree):
    (tree / "skills" / "s" / "references" / "a.md").write_text("# 참조\n\n[없음](./gone.md)\n")
    assert _problems(_build(tree)) == ["skills/s/references/a.md:3: ./gone.md (no such file)"]


def test_a_skill_url_to_a_missing_anchor_fails_the_build_at_its_line(tree):
    (tree / "skills" / "s" / "SKILL.md").write_text(SKILL_MD.replace("#설치", "#없는-절"))
    problems = _problems(_build(tree))
    assert len(problems) == 1 and problems[0].startswith("skills/s/SKILL.md:12: ")
    assert "no id '없는-절'" in problems[0]


def test_a_skill_file_the_skill_does_not_route_to_fails_the_build(tree):
    (tree / "skills" / "s" / "references" / "b.md").write_text("# 둘째\n")
    problems = _problems(_build(tree))
    assert "skills/s/SKILL.md: no route to references/b.md: llms.txt has nothing to say about it" in problems
    assert any("/wireview/agent/s/references/b.md is new" in problem for problem in problems)


def test_a_preview_says_so_in_llms_txt(tree):
    assert "v1.0.0rc1 문서다. 정식 릴리스 전 미리보기다." in _llms_of(_build(tree, version="1.0.0rc1").out)


def test_a_broken_anchor_fails_the_build_at_its_line(tree):
    (tree / "README.md").write_text("# 소개\n\n본문\n\n[가이드](docs/guide.md#없는-절)를 보세요.\n")
    assert _problems(_build(tree)) == [
        "README.md:5: /wireview/guide/#%EC%97%86%EB%8A%94-%EC%A0%88 has no target (no id '없는-절' on /wireview/guide/)"
    ]


def test_a_broken_anchor_on_the_same_page_fails_the_build(tree):
    (tree / "docs" / "next.md").write_text("# 다음\n\n다음 단계다.\n\n## 설치\n\n[셋째 설치](#설치-2)\n")
    problems = _problems(_build(tree))
    assert len(problems) == 1 and problems[0].startswith("docs/next.md:7: ") and "no id '설치-2'" in problems[0]


def test_a_link_to_a_missing_file_fails_the_build(tree):
    (tree / "docs" / "guide.md").write_text(
        "# 가이드\n\n설치를 안내한다.\n\n## 설치\n\n[다음](next.md) · [없음](gone.md)\n"
    )
    assert _problems(_build(tree)) == ["docs/guide.md:7: gone.md (no such file)"]


def test_the_documents_images_are_copied_into_the_build_once(tree):
    """Two documents show one image, one by a relative path and one by its URL into main (#166)."""
    image = b"\x89PNG\r\n\x1a\nnot really"
    (tree / "docs" / "img").mkdir()
    (tree / "docs" / "img" / "flow.png").write_bytes(image)
    raw = "https://raw.githubusercontent.com/itda-work/django-wireview/main/docs/img/flow.png"
    (tree / "README.md").write_text(f"# 소개\n\n[가이드](docs/guide.md#설치)를 보세요.\n\n![흐름]({raw})\n")
    (tree / "docs" / "next.md").write_text("# 다음\n\n다음 단계다.\n\n![흐름](img/flow.png)\n")
    result = _build(tree)
    assert _problems(result) == []
    served = f"/wireview/assets/flow.{hashlib.sha256(image).hexdigest()[:10]}.png"
    assert (result.out / served.lstrip("/")).read_bytes() == image
    assert [name for name in result.files if name.startswith("wireview/assets/flow.")] == [served.lstrip("/")]
    assert f'<img src="{served}" alt="흐름" />' in _html(result.out, "/wireview/")
    assert f'<img src="{served}" alt="흐름" />' in _html(result.out, "/wireview/guide/next/")
    assert f"![흐름]({served})" in (result.out / "wireview" / "guide" / "next" / "index.md").read_text()


def test_an_image_url_into_main_that_names_no_file_fails_the_build(tree):
    raw = "https://raw.githubusercontent.com/itda-work/django-wireview/main/docs/img/gone.png"
    (tree / "README.md").write_text(f"# 소개\n\n[가이드](docs/guide.md#설치)를 보세요.\n\n![흐름]({raw})\n")
    assert _problems(_build(tree)) == [f"README.md:5: {raw} (no such file in the repository)"]


def test_a_vanished_url_fails_until_it_redirects(tree):
    urls = tree / "docs" / "site-urls.txt"
    urls.write_text(urls.read_text() + "/wireview/guide/old/\n")
    problems = _problems(_build(tree))
    assert len(problems) == 1 and "/wireview/guide/old/ is no longer served" in problems[0]
    assert "docs/redirects.toml" in problems[0]

    (tree / "docs" / "redirects.toml").write_text(
        '[[redirects]]\nfrom = "/wireview/guide/old/"\nto = "/wireview/guide/next/"\n'
    )
    result = _build(tree)
    assert _problems(result) == []
    moved = (result.out / "wireview" / "guide" / "old" / "index.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh" content="0; url=/wireview/guide/next/">' in moved
    assert '<link rel="canonical" href="https://itda.work/wireview/guide/next/">' in moved
    assert '<meta name="robots" content="noindex">' in moved
    assert '<a href="/wireview/guide/next/">' in moved


def test_a_renamed_skill_file_fails_until_it_redirects_and_leaves_a_note_at_its_old_url(tree):
    """A published file is a public URL like a page: renaming it needs a redirect, which leaves a
    file of its kind at the old path naming the new one (#164)."""
    references = tree / "skills" / "s" / "references"
    (references / "a.md").rename(references / "b.md")
    (tree / "skills" / "s" / "SKILL.md").write_text(SKILL_MD.replace("references/a.md", "references/b.md"))
    urls = tree / "docs" / "site-urls.txt"
    urls.write_text(urls.read_text() + "/wireview/agent/s/references/b.md\n")
    problems = _problems(_build(tree))
    assert len(problems) == 1 and "/wireview/agent/s/references/a.md is no longer served" in problems[0]

    (tree / "docs" / "redirects.toml").write_text(
        '[[redirects]]\nfrom = "/wireview/agent/s/references/a.md"\nto = "/wireview/agent/s/references/b.md"\n'
    )
    result = _build(tree)
    assert _problems(result) == []
    old = result.out / "wireview" / "agent" / "s" / "references" / "a.md"
    assert old.read_text(encoding="utf-8") == (
        "이 파일은 옮겨졌다.\n새 주소: https://itda.work/wireview/agent/s/references/b.md\n"
    )
    assert not (old / "index.html").exists() and (old.parent / "a.md.gz").is_file()


@pytest.mark.parametrize(
    ("source", "target", "problem"),
    [
        ("/wireview/agent/s/old.md", "/wireview/guide/", "a page moves to a page (ending in /), a file to a file"),
        ("/wireview/agent/s/old.md", "/wireview/llms.txt", "a file moves to a file with the same extension"),
        ("/wireview/agent/s/old.md", "/wireview/agent/s/gone.md", "is not a published file"),
    ],
)
def test_a_redirect_the_rules_refuse_fails_the_build(tree, source, target, problem):
    urls = tree / "docs" / "site-urls.txt"
    urls.write_text(urls.read_text() + f"{source}\n")
    (tree / "docs" / "redirects.toml").write_text(f'[[redirects]]\nfrom = "{source}"\nto = "{target}"\n')
    problems = _problems(_build(tree))
    assert any(p.startswith("docs/redirects.toml: ") and problem in p for p in problems), problems


def test_a_link_shape_in_a_skill_code_block_or_code_span_is_code(tree):
    """``handlers["save"](payload)`` is a call, not a link; the rewrite leaves it, and so does the gate."""
    (tree / "skills" / "s" / "references" / "a.md").write_text(
        '# 참조\n\n```python\nresult = handlers["save"](payload)\n```\n\n`handlers["x"](y)`를 부른다.\n'
    )
    assert _problems(_build(tree)) == []


def test_a_site_url_in_a_skill_code_block_is_still_checked(tree):
    (tree / "skills" / "s" / "references" / "a.md").write_text(
        "# 참조\n\n```bash\ncurl https://itda.work/wireview/nope/\n```\n"
    )
    problems = _problems(_build(tree))
    assert len(problems) == 1 and problems[0].startswith("skills/s/references/a.md:4: /wireview/nope/ leads nowhere")


def test_a_skill_file_without_a_title_fails_the_build_without_a_traceback(tree):
    (tree / "skills" / "s" / "references" / "a.md").write_text("#### 참조\n\n```\n# 주석\n```\n")
    assert _problems(_build(tree)) == ["skills/s/references/a.md: no H1: llms.txt has no title for it"]


def test_every_broken_link_on_a_line_is_reported_once(tree):
    """The rewrite reports the missing file; the gate still reports the other link on that line."""
    (tree / "skills" / "s" / "references" / "a.md").write_text(
        "# 참조\n\n[없음](./gone.md) · https://itda.work/wireview/nope/\n"
    )
    problems = _problems(_build(tree))
    assert len(problems) == 2, problems
    assert problems[0] == "skills/s/references/a.md:3: ./gone.md (no such file)"
    assert problems[1].startswith("skills/s/references/a.md:3: /wireview/nope/ leads nowhere")


@pytest.mark.parametrize(
    ("lead", "problem"),
    [
        ("> 설정: `X = True`. 기본은 꺼져 있다.", "opens with a label (설정:)"),
        ("> 동작하는 예제: [examples/x/](notes.md) — 다른 사용자에게 보낸다.", "opens with a label (동작하는 예제:)"),
        ("Note: 아직 실험 중이다.", "opens with a label (Note:)"),
        ("#83에서 바뀐 동작이다.", "opens with an issue number"),
        ("(#83) 바뀐 동작이다.", "opens with an issue number"),
        ("[다음](next.md) · [메모](notes.md)", "is links only"),
        ("> [examples/x/](notes.md)", "is links only"),
    ],
)
def test_a_first_paragraph_that_does_not_say_what_the_page_is_fails_the_build(tree, lead, problem):
    """llms.txt and the meta tag describe a page by its first paragraph (#165)."""
    (tree / "docs" / "guide.md").write_text(f"# 가이드\n\n{lead}\n\n## 설치\n\n[다음](next.md)\n")
    problems = _problems(_build(tree))
    assert problems == [f"docs/guide.md:3: the first paragraph {problem}: say what the page is first"]


@pytest.mark.parametrize(
    "lead",
    [
        "설정 하나로 켜는 기능이다: `X = True`.",  # a colon past the opening words
        "Phoenix LiveView의 boost와 같다. 설정: `X = True`.",
        "[다음](next.md)에서 이어지는 설치 단계다.",  # a link inside a sentence
        "`settings.WIREVIEW`의 키 전부다.",
        "청크가 서버에서 처리되는 방식이다(#83).",
    ],
)
def test_a_first_paragraph_that_says_what_the_page_is_passes(tree, lead):
    (tree / "docs" / "guide.md").write_text(f"# 가이드\n\n{lead}\n\n## 설치\n\n[다음](next.md)\n")
    assert _problems(_build(tree)) == []


def test_a_page_with_a_summary_is_not_described_by_its_first_paragraph(tree):
    """A tutorial's summary in docs/site.toml is its description, so its first paragraph is free."""
    (tree / "docs" / "start.md").write_text("# 시작\n\n> 예제: [메모](notes.md)\n\n## 이미 있는 프로젝트에 붙이기\n")
    assert _problems(_build(tree)) == []


def test_a_new_url_fails_until_it_is_listed(tree):
    urls = tree / "docs" / "site-urls.txt"
    urls.write_text("/wireview/\n/wireview/guide/\n/wireview/gone/\n")
    problems = _problems(_build(tree))
    assert any("/wireview/guide/next/ is new: add it to docs/site-urls.txt" in p for p in problems)
    assert any("/wireview/llms.txt is new" in p for p in problems)

    # --update-urls adds what is new and never drops what vanished: that stays a failure.
    problems = _problems(_build(tree, update_urls=True))
    assert urls.read_text().splitlines() == sorted([*TREE_URLS, "/wireview/gone/"])
    assert len(problems) == 1 and "/wireview/gone/ is no longer served" in problems[0]


def test_the_url_list_is_the_repositorys_pages_sorted():
    listed = (ROOT / "docs" / "site-urls.txt").read_text(encoding="utf-8").splitlines()
    assert listed == sorted(listed)
    public = nav.Site.load().public_urls()
    assert set(listed) >= public
    assert {"/wireview/llms.txt", "/wireview/agent/wireview/SKILL.md"} <= public


# --- the release asset (#160) ------------------------------------------------------------------------


def test_the_bundle_is_named_by_the_tag_and_holds_the_build(site, tmp_path):
    target = bundle(site=site.out, out_dir=tmp_path / "site-dist")
    assert target.name == f"docs-site-{TAG}.tar.gz"
    with tarfile.open(target) as archive:
        files = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
            if member.isfile()
        }
        names = archive.getnames()
    built = {path.relative_to(site.out).as_posix(): path.read_bytes() for path in site.out.rglob("*") if path.is_file()}
    assert files == built
    assert all(name == "wireview" or name.startswith("wireview/") for name in names), names
    assert files["wireview/VERSION"] == f"{TAG}\n".encode()


def test_the_bundle_holds_what_itda_work_checks(site, tmp_path):
    """The checks website's deploy recipe makes before it unpacks (docs/implementation/docs-site-bundle.md).

    A bundle that fails one of them is not deployed; this fails here first.
    """
    target = bundle(site=site.out, out_dir=tmp_path)
    with tarfile.open(target, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts, member.name
            assert member.isfile() or member.isdir(), member.name
            assert path.parts[0] == "wireview", member.name
        names = {member.name for member in members if member.isfile()}
        assert {"wireview/index.html", "wireview/llms.txt", "wireview/sitemap.xml", "wireview/VERSION"} <= names
        version = archive.extractfile("wireview/VERSION").read().decode("utf-8").strip()  # type: ignore[union-attr]
    assert version == TAG and target.name == f"docs-site-{version}.tar.gz"


def test_the_contract_names_tests_that_exist():
    """Every test the bundle contract cites is one of this module's, so the table cannot outlive them."""
    contract = (ROOT / "docs" / "implementation" / "docs-site-bundle.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"`(test_\w+)`", contract))
    assert len(cited) >= 10, cited
    assert sorted(name for name in cited if not callable(globals().get(name))) == []


def _commit_time() -> int:
    return int(subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT, capture_output=True, text=True).stdout)


def test_the_bundle_records_nothing_of_the_machine(site, tmp_path, monkeypatch):
    """Sorted, the commit's time, no owners, the modes normalised -- whatever the umask or tar here."""
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    (site.out / "wireview" / "VERSION").chmod(0o600)
    try:
        target = bundle(site=site.out, out_dir=tmp_path)
    finally:
        (site.out / "wireview" / "VERSION").chmod(0o644)
    with tarfile.open(target) as archive:
        members = archive.getmembers()
    commit = _commit_time()
    assert commit > 0
    assert [m.name for m in members] == sorted(m.name for m in members)
    for member in members:
        assert (member.mtime, member.uid, member.gid, member.uname, member.gname) == (commit, 0, 0, "", ""), member.name
        assert member.mode == (0o755 if member.isdir() else 0o644), member.name
        assert member.isdir() or member.isfile(), member.name
    packed = target.read_bytes()
    assert packed[4:8] == b"\0\0\0\0" and not packed[3] & 0x08


def test_the_same_commit_bundles_the_same_bytes(site, tmp_path):
    first = bundle(site=site.out, out_dir=tmp_path / "one").read_bytes()
    again = build(out=tmp_path / "rebuilt")
    os.utime(again.out / "wireview" / "VERSION", (1_000_000_000, 1_000_000_000))
    assert bundle(site=again.out, out_dir=tmp_path / "two").read_bytes() == first


def _mtimes(target: Path) -> set[int]:
    with tarfile.open(target) as archive:
        return {member.mtime for member in archive.getmembers()}


def test_source_date_epoch_sets_the_members_time(site, tmp_path, monkeypatch):
    """The reproducible-builds convention wins over the commit's time; the gzip header stays 0."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    target = bundle(site=site.out, out_dir=tmp_path)
    assert _mtimes(target) == {1_700_000_000}
    assert target.read_bytes()[4:8] == b"\0\0\0\0"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "yesterday")
    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        bundle(site=site.out, out_dir=tmp_path)


def test_without_git_the_bundle_asks_for_source_date_epoch(site, tmp_path, monkeypatch):
    """No commit to read a time from: packing stops rather than fall back to 0 or to now."""
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    (tmp_path / "export").mkdir()
    with pytest.raises(RuntimeError, match="set SOURCE_DATE_EPOCH"):
        source_date_epoch(tmp_path / "export")
    assert source_date_epoch() == _commit_time()


def test_the_bundle_leaves_one_bundle_in_its_directory(site, tmp_path):
    (tmp_path / "docs-site-v0.0.1.tar.gz").write_bytes(b"old")
    (tmp_path / "keep.txt").write_text("not a bundle")
    target = bundle(site=site.out, out_dir=tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([target.name, "keep.txt"])


def test_the_bundle_never_goes_into_dist(site, tmp_path, monkeypatch):
    """release.yml uploads dist/ to PyPI whole; a tarball there would go up as a package file."""
    assert PYPI_DIST == ROOT / "dist"
    assert PYPI_DIST not in DEFAULT_BUNDLE_DIR.resolve().parents and DEFAULT_BUNDLE_DIR.resolve() != PYPI_DIST
    # Against a stand-in, so a guard that stopped working writes nothing into the real dist/.
    dist = tmp_path / "dist"
    monkeypatch.setattr("scripts.docs_site.bundle.PYPI_DIST", dist)
    for inside in (dist, dist / "docs"):
        with pytest.raises(ValueError, match="dist/"):
            bundle(site=site.out, out_dir=inside)
    assert not dist.exists()


def test_the_bundle_needs_a_build(tmp_path):
    with pytest.raises(FileNotFoundError, match="make docs-site"):
        bundle(site=tmp_path / "nothing", out_dir=tmp_path / "out")


# --- docs-serve --------------------------------------------------------------------------------------


def test_the_reload_script_is_not_in_the_build(site):
    leaked = [
        path.relative_to(site.out).as_posix()
        for path in site.out.rglob("*")
        if path.is_file() and not path.name.endswith(".gz") and b"__docs_dev__" in path.read_bytes()
    ]
    assert leaked == []


def test_the_server_adds_the_reload_script_to_html_only(site):
    state = State()
    state.problems = ["README.md:1: something"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site.out), state=state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        page = urllib.request.urlopen(f"{base}/wireview/tutorial/").read()
        assert INJECT in page and page.count(INJECT) == 1
        assert INJECT not in (site.out / "wireview" / "tutorial" / "index.html").read_bytes()
        markdown = urllib.request.urlopen(f"{base}/wireview/tutorial/index.md").read()
        assert INJECT not in markdown
        state_json = urllib.request.urlopen(f"{base}/__docs_dev__/state").read()
        assert b"README.md:1: something" in state_json
        assert b"location.reload" in urllib.request.urlopen(f"{base}/__docs_dev__/reload.js").read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("url", "content_type"),
    [
        ("/wireview/tutorial/", "text/html; charset=utf-8"),
        ("/wireview/tutorial/index.html", "text/html; charset=utf-8"),
        ("/wireview/llms.txt", "text/plain; charset=utf-8"),
        ("/wireview/tutorial/index.md", "text/markdown; charset=utf-8"),
        ("/wireview/agent/wireview/SKILL.md", "text/markdown; charset=utf-8"),
        ("/wireview/sitemap.xml", "application/xml; charset=utf-8"),
        ("/__docs_dev__/reload.js", "text/javascript; charset=utf-8"),
    ],
)
def test_the_server_says_its_text_files_are_utf_8(site, url, content_type):
    """Korean in a .txt or .md is mojibake when the browser guesses the encoding (#165)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site.out), state=State()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}{url}") as response:
            assert response.headers["Content-Type"] == content_type
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_every_text_file_the_build_writes_has_a_charset(site):
    """Each kind of text file in the output gets a utf-8 type, and each such type is one the build writes."""
    files = [path for path in site.out.rglob("*") if path.is_file() and path.suffix not in ("", ".gz")]
    # A text file is one the build compresses; an image is served with its own type and no charset.
    suffixes = {path.suffix for path in files if path.with_name(path.name + ".gz").is_file()}
    assert suffixes == set(TEXT_TYPES), suffixes
    assert {path.suffix for path in files} - suffixes == {".jpg", ".svg"}
    assert all(value.endswith("; charset=utf-8") for value in TEXT_TYPES.values())


def test_the_server_watches_what_the_build_reads():
    files = {path.relative_to(ROOT).as_posix() for path in watched(ROOT)}
    assert {"README.md", "docs/site.toml", "docs/redirects.toml", "docs/site-urls.txt"} <= files
    assert {"skills/wireview/SKILL.md", "skills/wireview/references/component.md"} <= files
    assert "scripts/docs_site/templates/llms.txt" in files
    assert "docs/tutorials/03-todo-app.md" in files
    assert {"scripts/docs_site/templates/page.html", "scripts/docs_site/assets/site.css"} <= files
