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
import tarfile
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

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

from scripts.docs_site.build import build  # noqa: E402
from scripts.docs_site.bundle import DEFAULT_BUNDLE_DIR, PYPI_DIST, bundle  # noqa: E402
from scripts.docs_site.render import Linker  # noqa: E402
from scripts.docs_site.serve import INJECT, Handler, State, watched  # noqa: E402

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


def test_assets_are_named_by_their_content(site):
    assets = sorted((site.out / "wireview" / "assets").iterdir())
    named = [path for path in assets if not path.name.endswith(".gz")]
    assert {re.sub(r"\.[0-9a-f]{10}\.", ".", path.name) for path in named} == {"site.css", "site.js", "boot.js"}
    for path in named:
        digest = path.name.split(".")[1]
        assert hashlib.sha256(path.read_bytes()).hexdigest().startswith(digest), path.name


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
    ],
)
def test_a_link_goes_where_the_site_has_it(linker, source, target, expected):
    assert linker.target(target, source) == (expected, None)


def test_an_image_in_the_repository_loads_from_the_tag(linker):
    raw = "https://raw.githubusercontent.com/itda-work/django-wireview/main/overview.jpg"
    assert linker.target(raw, "README.md", image=True) == (raw.replace("/main/", f"/{TAG}/"), None)
    pinned = f"https://raw.githubusercontent.com/itda-work/django-wireview/{TAG}/overview.jpg"
    assert linker.target("overview.jpg", "README.md", image=True) == (pinned, None)


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

[exclude]
paths = ["docs/notes.md"]
"""


@pytest.fixture
def tree(tmp_path):
    """A repository with three pages, a note the site does not publish, and the URL list."""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n')
    (root / "README.md").write_text("# 소개\n\n[가이드](docs/guide.md#설치)를 보세요.\n")
    (root / "docs" / "guide.md").write_text("# 가이드\n\n## 설치\n\n[다음](next.md) · [메모](notes.md)\n")
    (root / "docs" / "next.md").write_text("# 다음\n\n## 설치\n\n## 설치\n\n[둘째 설치](#설치-1)\n")
    (root / "docs" / "notes.md").write_text("# 메모\n")
    (root / "docs" / "site.toml").write_text(SITE_TOML)
    (root / "docs" / "redirects.toml").write_text("redirects = []\n")
    (root / "docs" / "site-urls.txt").write_text("/wireview/\n/wireview/guide/\n/wireview/guide/next/\n")
    return root


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


def test_a_broken_anchor_fails_the_build_at_its_line(tree):
    (tree / "README.md").write_text("# 소개\n\n본문\n\n[가이드](docs/guide.md#없는-절)를 보세요.\n")
    assert _problems(_build(tree)) == [
        "README.md:5: /wireview/guide/#%EC%97%86%EB%8A%94-%EC%A0%88 has no target (no id '없는-절' on /wireview/guide/)"
    ]


def test_a_broken_anchor_on_the_same_page_fails_the_build(tree):
    (tree / "docs" / "next.md").write_text("# 다음\n\n## 설치\n\n[셋째 설치](#설치-2)\n")
    problems = _problems(_build(tree))
    assert len(problems) == 1 and problems[0].startswith("docs/next.md:5: ") and "no id '설치-2'" in problems[0]


def test_a_link_to_a_missing_file_fails_the_build(tree):
    (tree / "docs" / "guide.md").write_text("# 가이드\n\n## 설치\n\n[다음](next.md) · [없음](gone.md)\n")
    assert _problems(_build(tree)) == ["docs/guide.md:5: gone.md (no such file)"]


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


def test_a_new_url_fails_until_it_is_listed(tree):
    urls = tree / "docs" / "site-urls.txt"
    urls.write_text("/wireview/\n/wireview/guide/\n/wireview/gone/\n")
    problems = _problems(_build(tree))
    assert any("/wireview/guide/next/ is new: add it to docs/site-urls.txt" in p for p in problems)

    # --update-urls adds what is new and never drops what vanished: that stays a failure.
    problems = _problems(_build(tree, update_urls=True))
    assert urls.read_text() == "/wireview/\n/wireview/gone/\n/wireview/guide/\n/wireview/guide/next/\n"
    assert len(problems) == 1 and "/wireview/gone/ is no longer served" in problems[0]


def test_the_url_list_is_the_repositorys_pages_sorted():
    listed = (ROOT / "docs" / "site-urls.txt").read_text(encoding="utf-8").splitlines()
    assert listed == sorted(listed)
    assert set(listed) >= {page.url for page in nav.pages()}


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


def test_the_bundle_records_nothing_of_the_machine(site, tmp_path):
    """Sorted, no times, no owners, the modes normalised -- whatever the umask or tar here."""
    (site.out / "wireview" / "VERSION").chmod(0o600)
    try:
        target = bundle(site=site.out, out_dir=tmp_path)
    finally:
        (site.out / "wireview" / "VERSION").chmod(0o644)
    with tarfile.open(target) as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == sorted(m.name for m in members)
    for member in members:
        assert (member.mtime, member.uid, member.gid, member.uname, member.gname) == (0, 0, 0, "", ""), member.name
        assert member.mode == (0o755 if member.isdir() else 0o644), member.name
        assert member.isdir() or member.isfile(), member.name
    packed = target.read_bytes()
    assert packed[4:8] == b"\0\0\0\0" and not packed[3] & 0x08


def test_the_same_commit_bundles_the_same_bytes(site, tmp_path):
    first = bundle(site=site.out, out_dir=tmp_path / "one").read_bytes()
    again = build(out=tmp_path / "rebuilt")
    os.utime(again.out / "wireview" / "VERSION", (1_000_000_000, 1_000_000_000))
    assert bundle(site=again.out, out_dir=tmp_path / "two").read_bytes() == first


def test_the_bundle_leaves_one_bundle_in_its_directory(site, tmp_path):
    (tmp_path / "docs-site-v0.0.1.tar.gz").write_bytes(b"old")
    (tmp_path / "keep.txt").write_text("not a bundle")
    target = bundle(site=site.out, out_dir=tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([target.name, "keep.txt"])


def test_the_bundle_never_goes_into_dist(site):
    """release.yml uploads dist/ to PyPI whole; a tarball there would go up as a package file."""
    assert PYPI_DIST not in DEFAULT_BUNDLE_DIR.resolve().parents and DEFAULT_BUNDLE_DIR.resolve() != PYPI_DIST
    for inside in (PYPI_DIST, PYPI_DIST / "docs"):
        with pytest.raises(ValueError, match="dist/"):
            bundle(site=site.out, out_dir=inside)
    assert not list(PYPI_DIST.glob("docs-site-*"))


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


def test_the_server_watches_what_the_build_reads():
    files = {path.relative_to(ROOT).as_posix() for path in watched(ROOT)}
    assert {"README.md", "docs/site.toml", "docs/redirects.toml", "docs/site-urls.txt"} <= files
    assert "docs/tutorials/03-todo-app.md" in files
    assert {"scripts/docs_site/templates/page.html", "scripts/docs_site/assets/site.css"} <= files
