"""Build the documentation site: docs/ as static files under <out>/wireview/ (#159).

    <out>/wireview/                     the home page (README.md)
    <out>/wireview/<section>/<page>/    index.html, and index.md: the document, its links rewritten
    <out>/wireview/assets/              site.<hash>.css and the scripts, named by their content
    <out>/wireview/sitemap.xml          absolute URLs, no lastmod
    <out>/wireview/VERSION              the release tag the site was built from
    every text file also as <name>.gz, compressed ahead of time

The same commit builds the same bytes: files are written in a sorted order, nothing records
a time, a path on this machine or the environment, and gzip gets mtime 0 and no file name.

After writing, the build checks what it wrote (each a gate: a problem fails the build) --

- every link to a /wireview/ path or a #fragment reaches a file, and an element with that id;
- docs/site-urls.txt, the public URLs, against the pages and the redirects: a URL the list
  has and the site no longer serves is a 404 for every link already out there, so it has to
  move to docs/redirects.toml; a URL the site serves and the list lacks has to be added
  (``--update-urls`` adds it).

The document guards in tests/ are the first gate; ``make docs-site`` runs them before this.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import io
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from urllib.parse import unquote, urljoin

from . import nav
from .render import Linker, Problem, pygments_css, render, source_url

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
ASSETS = HERE / "assets"
DEFAULT_OUT = nav.ROOT / "build" / "docs-site"
URL_LIST = Path("docs") / "site-urls.txt"

COMPRESSED = (".html", ".md", ".css", ".js", ".xml", ".txt")


@dataclass
class Result:
    out: Path
    version: str
    preview: bool
    files: list[str] = field(default_factory=list)  # paths relative to out, sorted
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _hashed(name: str, content: bytes) -> str:
    stem, suffix = name.rsplit(".", 1)
    return f"{stem}.{hashlib.sha256(content).hexdigest()[:10]}.{suffix}"


def _gzip(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as stream:
        stream.write(data)
    return buffer.getvalue()


class _Writer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.written: dict[str, bytes] = {}

    def write(self, path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        if path in self.written:
            raise ValueError(f"{path} is written twice")
        self.written[path] = data

    def flush(self) -> list[str]:
        for path in sorted(self.written):
            if path.endswith(COMPRESSED):
                self.written[f"{path}.gz"] = _gzip(self.written[path])
        for path in sorted(self.written):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.written[path])
        return sorted(self.written)


# --- layout ---------------------------------------------------------------------------------------


def _sidebar(site: nav.Site, current: nav.Page) -> str:
    pages = site.pages()
    levels = {level["slug"]: level for level in site.levels()}

    def link(page: nav.Page, label: str, css: str = "") -> str:
        attrs = ' aria-current="page"' if page.url == current.url else ""
        css_attr = f' class="{css}"' if css else ""
        return f'<a{css_attr} href="{_e(page.url)}"{attrs}>{_e(label)}</a>'

    parts = [f'<div class="sidebar__section">{link(pages[0], "소개", "sidebar__heading")}</div>']
    for section in site.data["sections"]:
        index = next(page for page in pages if page.section == section["slug"] and not page.slug)
        members = [page for page in pages if page.section == section["slug"] and page.slug]
        items, level = [], None
        for page in members:
            if page.level and page.level != level:
                if items:
                    items.append("</ul>")
                level = page.level
                items.append(f'<p class="sidebar__level">{_e(levels[level]["name"])}</p><ul>')
            elif not items:
                items.append("<ul>")
            items.append(f"<li>{link(page, page.short_title)}</li>")
        if items:
            items.append("</ul>")
        parts.append(
            f'<div class="sidebar__section">{link(index, section["name"], "sidebar__heading")}{"".join(items)}</div>'
        )
    return "\n        ".join(parts)


def _toc(entries: list[tuple[int, str, str]]) -> str:
    if len(entries) < 2:
        return ""
    items = "".join(
        f'<li class="toc__h{level}"><a href="#{_e(anchor)}">{_e(text)}</a></li>' for level, anchor, text in entries
    )
    return f'<nav class="toc" aria-label="이 페이지"><p class="toc__title">이 페이지</p><ul>{items}</ul></nav>'


def _tutorial_meta(site: nav.Site, page: nav.Page) -> str:
    if not page.level:
        return ""
    level = next(level for level in site.levels() if level["slug"] == page.level)
    chips = [f"{level['name']} ({level['label']})"]
    if page.minutes:
        chips.append(f"예상 {nav.human_minutes(page.minutes)}")
    return '<p class="page-meta">' + "".join(f'<span class="chip">{_e(chip)}</span>' for chip in chips) + "</p>"


def _label(site: nav.Site, page: nav.Page) -> str:
    """What the sidebar calls a page: 소개 for the home page, a section's name for its index."""
    if not page.section:
        return "소개"
    if not page.slug:
        return next(section["name"] for section in site.data["sections"] if section["slug"] == page.section)
    return page.short_title


def _neighbours(site: nav.Site, page: nav.Page) -> tuple[nav.Page | None, nav.Page | None]:
    """The pages before and after this one in the sidebar's order, across section boundaries."""
    pages = site.pages()
    at = pages.index(page)
    return (pages[at - 1] if at > 0 else None, pages[at + 1] if at < len(pages) - 1 else None)


def _pager(site: nav.Site, page: nav.Page) -> str:
    """Previous and next page, in docs/site.toml's order: the whole site reads as one sequence.

    A tutorial's card shows its level and time; any other page's card shows its section, and so
    does a card that crosses into another section. A section's index is titled with the section's
    name, so its card says what it is instead; the home page's card says nothing more.
    """
    levels = {level["slug"]: level["name"] for level in site.levels()}
    sections = {section["slug"]: section["name"] for section in site.data["sections"]}

    def card(other: nav.Page, rel: str) -> str:
        meta = []
        if other.section and not other.slug:
            meta.append("섹션 목차")
        elif other.section and (not other.level or other.section != page.section):
            meta.append(sections[other.section])
        if other.level:
            meta.append(levels[other.level])
            if other.minutes:
                meta.append(nav.human_minutes(other.minutes))
        label = "← 이전" if rel == "prev" else "다음 →"
        return (
            f'<a class="pager__{rel}" href="{_e(other.url)}" rel="{rel}">'
            f'<span class="pager__label">{label}</span>'
            f'<span class="pager__title">{_e(_label(site, other))}</span>'
            f'<span class="pager__meta">{_e(" · ".join(meta))}</span></a>'
        )

    before, after = _neighbours(site, page)
    cards = [card(other, rel) for other, rel in ((before, "prev"), (after, "next")) if other]
    return f'<nav class="pager" aria-label="이전·다음 페이지">{"".join(cards)}</nav>' if cards else ""


def _pager_links(site: nav.Site, page: nav.Page) -> str:
    """The same neighbours as <link rel="prev|next"> in the head."""
    before, after = _neighbours(site, page)
    return "".join(
        f'<link rel="{rel}" href="{_e(other.url)}">' for other, rel in ((before, "prev"), (after, "next")) if other
    )


def _preview(version: str, preview: bool) -> tuple[str, str]:
    """The robots meta and the band a pre-release's pages carry; nothing for a final release."""
    if not preview:
        return "", ""
    band = (
        '<div class="preview-band" role="note"><strong>미리보기</strong> · '
        f"django-wireview {_e(version)} 문서입니다. 정식 릴리스 전이라 검색에 노출하지 않습니다.</div>"
    )
    return '<meta name="robots" content="noindex">', band


# --- gates ----------------------------------------------------------------------------------------


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)
            if name in ("href", "src") and value:
                self.links.append(value)


def _parse(data: bytes) -> _Links:
    parser = _Links()
    parser.feed(data.decode("utf-8"))
    return parser


def check_links(
    files: dict[str, bytes], base: str, where: dict[str, tuple[str, list[tuple[int, str]]]]
) -> list[Problem]:
    """Gate 2: every /wireview/ link and #fragment of the written HTML reaches a file and an id."""
    parsed = {path: _parse(data) for path, data in files.items() if path.endswith(".html")}
    problems = []
    for path in sorted(parsed):
        page_url = "/" + path.removesuffix("index.html")
        for link in parsed[path].links:
            # The site writes every link of its own as a /wireview/ path or a #fragment; a relative
            # link left as written is one the render already reported (no such file).
            if not link.startswith((base, "#")):
                continue
            target = urljoin(page_url, link)
            file_part, _, fragment = target.partition("#")
            file_part = unquote(file_part.partition("?")[0])
            resolved = file_part.lstrip("/") + ("index.html" if file_part.endswith("/") else "")
            message = None
            if resolved not in files:
                message = f"{link} leads nowhere (no {resolved} in the build)"
            elif fragment and resolved.endswith(".html") and unquote(fragment) not in parsed[resolved].ids:
                message = f"{link} has no target (no id {unquote(fragment)!r} on {file_part})"
            if message:
                source, lines = where.get(path, (path, []))
                line = next((number for number, href in lines if href == link and number), None)
                problems.append(Problem(f"{source}:{line}" if line else source, message))
    return problems


def check_urls(root: Path, served: set[str], redirected: set[str], update: bool) -> list[Problem]:
    """Gate 3: no public URL disappears, and every new one is listed."""
    listing = root / URL_LIST
    listed = set(listing.read_text(encoding="utf-8").split()) if listing.exists() else set()
    expected = served | redirected
    if update:
        listed |= expected
        listing.write_text("".join(f"{url}\n" for url in sorted(listed)), encoding="utf-8")
    problems = [
        Problem(
            str(URL_LIST),
            f"{url} is no longer served: move it in docs/redirects.toml (a public URL never disappears)",
        )
        for url in sorted(listed - expected)
    ]
    problems += [
        Problem(
            str(URL_LIST),
            f"{url} is new: add it to {URL_LIST} (python -m scripts.docs_site build --update-urls)",
        )
        for url in sorted(expected - listed)
    ]
    return problems


# --- the build ------------------------------------------------------------------------------------


def build(
    out: Path = DEFAULT_OUT,
    root: Path = nav.ROOT,
    version: str | None = None,
    update_urls: bool = False,
) -> Result:
    """Render the site into ``out`` (replacing it) and check it; the result lists the problems."""
    site = nav.Site.load(root)
    version = version or nav.version(root)
    tag = f"v{version}"
    preview = nav.is_prerelease(version)
    base = site.base
    origin = nav.ORIGIN
    linker = Linker(site, tag)
    result = Result(out=out, version=version, preview=preview)
    writer = _Writer(out)
    prefix = base.lstrip("/")

    def to_file(url: str, name: str) -> str:
        return f"{url.lstrip('/')}{name}"

    # Assets, named by their content so they can be cached for good.
    assets: dict[str, str] = {}
    sources = {
        "site.css": (ASSETS / "site.css").read_text(encoding="utf-8") + "\n" + pygments_css(),
        "site.js": (ASSETS / "site.js").read_text(encoding="utf-8"),
        "boot.js": (ASSETS / "boot.js").read_text(encoding="utf-8"),
    }
    for name, content in sorted(sources.items()):
        hashed = _hashed(name, content.encode("utf-8"))
        writer.write(f"{prefix}assets/{hashed}", content)
        assets[name] = f"{base}assets/{hashed}"

    page_template = Template((TEMPLATES / "page.html").read_text(encoding="utf-8"))
    redirect_template = Template((TEMPLATES / "redirect.html").read_text(encoding="utf-8"))
    robots, band = _preview(version, preview)
    where: dict[str, tuple[str, list[tuple[int, str]]]] = {}
    pages = site.pages()

    for page in pages:
        rendered = render(page, linker)
        result.problems += rendered.problems
        markdown_url = f"{page.url}index.md"
        toc = _toc(rendered.toc)
        title = page.title if page.url == base else f"{page.title} · django-wireview 문서"
        document = page_template.substitute(
            title=_e(title),
            description=_e(page.summary or rendered.description),
            robots=robots,
            canonical=_e(f"{origin}{page.url}"),
            markdown_url=_e(markdown_url),
            boot_js=assets["boot.js"],
            site_css=assets["site.css"],
            site_js=assets["site.js"],
            home_url=_e(base),
            repository=nav.REPOSITORY,
            changelog=_e(nav.pin(f"{nav.REPOSITORY}/blob/main/CHANGELOG.md", tag)),
            preview=band,
            layout_class="layout" if toc else "layout layout--no-toc",
            sidebar=_sidebar(site, page),
            title_html=f'<h1 id="{_e(rendered.title_id)}">{rendered.title_html}</h1>',
            page_meta=_tutorial_meta(site, page),
            body=rendered.body,
            pager=_pager(site, page),
            pager_links=_pager_links(site, page),
            source_url=_e(source_url(page, tag)),
            tag=_e(tag),
            toc=toc,
            version=_e(version),
        )
        html_path = to_file(page.url, "index.html")
        writer.write(html_path, document)
        writer.write(to_file(page.url, "index.md"), rendered.markdown)
        where[html_path] = (page.source, rendered.links)

    for entry in site.redirects:
        writer.write(
            to_file(entry["from"], "index.html"),
            redirect_template.substitute(
                to=_e(entry["to"]),
                canonical=_e(f"{origin}{entry['to']}"),
                boot_js=assets["boot.js"],
                site_css=assets["site.css"],
            ),
        )

    urls = "".join(f"  <url><loc>{_e(origin + page.url)}</loc></url>\n" for page in sorted(pages, key=lambda p: p.url))
    writer.write(
        f"{prefix}sitemap.xml",
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n',
    )
    writer.write(f"{prefix}VERSION", f"{tag}\n")

    result.problems += check_links(writer.written, base, where)
    result.problems += check_urls(root, {page.url for page in pages}, {e["from"] for e in site.redirects}, update_urls)

    # Write beside the old output and swap, so a server reading it never sees half a site.
    staging = out.with_name(f".{out.name}.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    writer.root = staging
    result.files = writer.flush()
    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    return result
