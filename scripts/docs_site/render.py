"""Markdown to the site's HTML, and the link rewriting both the HTML and the published Markdown share.

A link in a document is written for GitHub: relative to the file, or an absolute URL into the
repository's ``main``. On the site it becomes one of three things --

- a page the site publishes: that page's path (``/wireview/tutorial/todo-app/``), anchor kept;
- any other file of the repository: GitHub at the release tag (``blob/v1.0.0/examples/...``),
  as hatch_build.py pins the PyPI description -- except an image, which the site serves itself
  (``/wireview/assets/overview.<hash>.jpg``), so a page loads nothing but its fonts from
  elsewhere (#166);
- anything else (another site, ``mailto:``): unchanged.

A relative link to a file that does not exist is a problem the build reports.

The agent skill is published as it is, Markdown beside the pages (``rewrite_published``): a
relative link between its files stays as written, since they keep their layout on the site,
and a page of the site is written as an absolute URL, so a bare URL in a table can name it too.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.common.utils import unescapeAll
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygmentize
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from . import nav

SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
FENCE = re.compile(r"^\s*(```|~~~)")
# [text](target) and [text](target "title"); an image's ![alt](src) is the same shape (tests/test_doc_links.py).
MD_LINK = re.compile(r"(\]\()([^()\s]+)((?:\s+\"[^\"]*\")?\))")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
RAW_ATTR = re.compile(r"""\b(href|src)=(["'])(.*?)\2""")
MAIN_URL = re.compile(rf"^{re.escape(nav.REPOSITORY)}/(?:blob|tree)/main/([^#?]*)(#.*)?$")
RAW_MAIN_URL = re.compile(rf"^{re.escape(nav.BRANCH_URLS[2])}([^#?]*)$")
# A URL into main written as text, not as a link's target: the agent skill's tables name pages so.
BARE_MAIN_URL = re.compile(rf"{re.escape(nav.REPOSITORY)}/(?:blob|tree)/main/[^\s|)>`\"']*")
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

_FORMATTER = HtmlFormatter(nowrap=True)


def hashed_name(name: str, content: bytes) -> str:
    """``site.css`` as ``site.<10 hex>.css``: a name that changes with the content, so it can be cached for good."""
    stem, dot, suffix = name.rpartition(".")
    digest = hashlib.sha256(content).hexdigest()[:10]
    return f"{stem}.{digest}.{suffix}" if dot else f"{name}.{digest}"


@dataclass(frozen=True)
class Problem:
    where: str  # "docs/features/csp.md:12", or a path alone
    message: str
    link: str = ""  # the link as written, when the problem is one link's

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class Rendered:
    title_html: str
    title_id: str
    body: str
    toc: list[tuple[int, str, str]]  # (level, id, text) of the h2 and h3 headings
    markdown: str  # the source with its links rewritten
    description: str  # the first paragraph's text, cut at 200 characters for the meta tag
    lead: str  # the first paragraph's text, whole
    lead_line: int = 0  # the first paragraph's 1-based source line
    lead_unlinked: str = ""  # the first paragraph's text outside its links
    problems: list[Problem] = field(default_factory=list)
    links: list[tuple[int, str]] = field(default_factory=list)  # (source line, href) of every rewritten link


class Linker:
    """Rewrites a document's link targets for the site (the module docstring's three cases)."""

    def __init__(self, site: nav.Site, tag: str) -> None:
        self.root = site.root
        self.base = site.base
        self.tag = tag
        self.urls = {page.source: page.url for page in site.pages()}
        self.published = {file.source: file.url for file in site.skill()}
        #: The repository's images the documents show, by path: the site path each is served at.
        #: The build writes them; the Linker only names them.
        self.images: dict[str, str] = {}

    def _page_url(self, path: str) -> str | None:
        """The site path of a repository path (a file, or a directory whose README.md is a page)."""
        path = path.rstrip("/")
        if path in self.urls:
            return self.urls[path]
        if path in self.published:
            return self.published[path]
        return self.urls.get(f"{path}/README.md" if path else "README.md")

    def _image(self, path: str) -> str:
        """The site path of a repository image: an asset named by its content, as the css and js are."""
        if path not in self.images:
            name = path.rsplit("/", 1)[-1]
            self.images[path] = f"{self.base}assets/{hashed_name(name, (self.root / path).read_bytes())}"
        return self.images[path]

    def _github(self, path: str) -> str:
        kind = "tree" if (self.root / path).is_dir() else "blob"
        return nav.pin(f"{nav.REPOSITORY}/{kind}/main/{path}", self.tag)

    def target(self, target: str, source: str, image: bool = False) -> tuple[str, str | None]:
        """The target as the site writes it, and the problem with it, if any."""
        if SCHEME.match(target) or target.startswith("//"):
            if image and (match := RAW_MAIN_URL.match(target)):
                path = unquote(match.group(1))
                if not (self.root / path).is_file():
                    return target, f"{target} (no such file in the repository)"
                return self._image(path), None
            if match := MAIN_URL.match(target):
                url = self._page_url(unquote(match.group(1)))
                if url and not image:
                    return url + (match.group(2) or ""), None
            return nav.pin(target, self.tag), None
        if target.startswith("#"):
            return target, None
        file_part, hash_, anchor = target.partition("#")
        fragment = hash_ + anchor
        resolved = (self.root / source).parent / unquote(file_part)
        try:
            path = resolved.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return target, f"{target} leaves the repository"
        if not resolved.exists():
            return target, f"{target} (no such file)"
        path = "" if path == "." else path
        if image and resolved.is_file():
            return self._image(path), None
        if not image and (url := self._page_url(path)):
            return url + fragment, None
        return self._github(path) + fragment, None

    def published_target(self, target: str, source: str, image: bool = False) -> tuple[str, str | None]:
        """As ``target`` for a file published as it is (the module docstring's last paragraph)."""
        new, problem = self.target(target, source, image)
        relative = not SCHEME.match(target) and not target.startswith(("/", "#"))
        if relative and new.partition("#")[0] in self.published.values():
            return target, problem
        if new.startswith("/"):
            return nav.ORIGIN + new, problem
        return new, problem


def _highlight(code: str, lang: str) -> str:
    """A code block: the ``<pre>`` inside the ``.code-block`` box site.js puts its copy button in (#173).

    The box, not the ``<pre>``, holds the button: the ``<pre>`` scrolls sideways, and the button
    would scroll away with a long line.
    """
    name = lang.split()[0] if lang else ""
    try:
        lexer = get_lexer_by_name(name) if name else None
    except ClassNotFound:
        lexer = None
    body = pygmentize(code, lexer, _FORMATTER) if lexer else html.escape(code)
    label = f' data-lang="{html.escape(name)}"' if name else ""
    return f'<div class="code-block"><pre class="highlight"{label}><code>{body}</code></pre></div>\n'


def _markdown() -> MarkdownIt:
    # GitHub-flavoured as far as the documents go: tables, strikethrough, task lists and footnotes.
    # Raw HTML passes through; the documents are this repository's own.
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.use(tasklists_plugin).use(footnote_plugin)

    def heading_open(self, tokens, idx, options, env):
        token = tokens[idx]
        return f'<{token.tag} id="{html.escape(token.meta["id"])}">'

    def heading_close(self, tokens, idx, options, env):
        token = tokens[idx]
        anchor = html.escape(token.meta["id"])
        return f'<a class="heading-anchor" href="#{anchor}" aria-label="이 절의 링크">#</a></{token.tag}>\n'

    # Every code block is _highlight's: the stock fence rule takes a highlighter's output as it is
    # only when it starts with <pre>, and the indented code block's never asks one.
    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        return _highlight(token.content, unescapeAll(token.info).strip())

    def code_block(self, tokens, idx, options, env):
        return _highlight(tokens[idx].content, "")

    md.add_render_rule("heading_open", heading_open)
    md.add_render_rule("fence", fence)
    md.add_render_rule("code_block", code_block)
    md.add_render_rule("heading_close", heading_close)
    return md


MD = _markdown()


def _plain(inline: Token, links: bool = True) -> str:
    """An inline token's text; without ``links``, the text a link's label holds is left out."""
    parts, depth = [], 0
    for child in inline.children or []:
        if child.type == "link_open":
            depth += 1
        elif child.type == "link_close":
            depth -= 1
        elif depth and not links:
            continue
        elif child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def _line_of(lines: list[str], span: list[int] | None, needle: str) -> int:
    """The 1-based line of the block a link sits in that mentions it (the block's first line otherwise)."""
    if not span:
        return 0
    for number in range(span[0], span[1]):
        if needle and needle in lines[number]:
            return number + 1
    return span[0] + 1


def _without_tutorial_nav(text: str) -> str:
    """Blank the closing navigation line; the site writes its own. Blank, so line numbers hold."""
    out, fenced = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
        out.append("" if not fenced and nav.TUTORIAL_NAV.match(line) else line)
    return "\n".join(out)


def rewrite_markdown(text: str, source: str, linker: Linker, published: bool = False) -> tuple[str, list[Problem]]:
    """The document with its link targets rewritten, outside code blocks and code spans.

    ``published``: a file the site publishes as it is (``rewrite_published``), whose bare URLs
    into main are rewritten as well.
    """
    out, problems, fenced = [], [], False
    for number, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            fenced = not fenced
        if fenced or FENCE.match(line):
            out.append(line)
            continue
        pieces, last = [], 0
        for code in CODE_SPAN.finditer(line):
            pieces.append(_rewrite_links(line[last : code.start()], source, linker, number, problems, published))
            pieces.append(code.group(0))
            last = code.end()
        pieces.append(_rewrite_links(line[last:], source, linker, number, problems, published))
        out.append("".join(pieces))
    return "\n".join(out), problems


def _rewrite_links(
    text: str, source: str, linker: Linker, number: int, problems: list[Problem], published: bool = False
) -> str:
    target = linker.published_target if published else linker.target

    def replace(match: re.Match) -> str:
        image = bool(re.search(r"!\[[^\]]*$", text[: match.start()]))
        new, problem = target(match.group(2), source, image=image)
        if problem:
            problems.append(Problem(f"{source}:{number}", problem, match.group(2)))
        return match.group(1) + new + match.group(3)

    def replace_bare(match: re.Match) -> str:
        new, problem = target(match.group(0), source)
        if problem:
            problems.append(Problem(f"{source}:{number}", problem, match.group(0)))
        return new

    text = MD_LINK.sub(replace, text)
    # A link's target rewritten above names a tag or the site, never main: this sees bare URLs only.
    return BARE_MAIN_URL.sub(replace_bare, text) if published else text


def rewrite_published(text: str, source: str, linker: Linker) -> tuple[str, list[Problem]]:
    """A file the site publishes as it is: links rewritten, any URL into main left (a code block's) pinned."""
    rewritten, problems = rewrite_markdown(text, source, linker, published=True)
    return nav.pin(rewritten, linker.tag), problems


def front_matter(text: str) -> dict[str, str]:
    """The ``key: value`` lines of a skill's front matter (one line each, as SKILL.md writes them)."""
    match = FRONT_MATTER.match(text)
    if not match:
        return {}
    pairs = (line.partition(":") for line in match.group(1).splitlines())
    return {key.strip(): value.strip() for key, sep, value in pairs if sep}


def render(page: nav.Page, linker: Linker) -> Rendered:
    """A page's title, body and table of contents, its links rewritten for the site."""
    text = page.path.read_text(encoding="utf-8")
    lines = text.split("\n")
    source_text = _without_tutorial_nav(text) if page.level else text
    env: dict = {}  # the footnotes the parse collects, for the render
    tokens = MD.parse(source_text, env)
    problems: list[Problem] = []
    links: list[tuple[int, str]] = []
    toc: list[tuple[int, str, str]] = []
    slugger = nav.Slugger()

    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            token.meta["id"] = slugger(inline.content)
            tokens[index + 2].meta["id"] = token.meta["id"]
            level = int(token.tag[1])
            if level in (2, 3):
                toc.append((level, token.meta["id"], _plain(inline)))
        if token.type == "inline":
            for child in token.children or []:
                attr = {"link_open": "href", "image": "src"}.get(child.type)
                if attr:
                    raw = str(child.attrs[attr])
                    new, problem = linker.target(raw, page.source, image=child.type == "image")
                    line = _line_of(lines, token.map, unquote(raw.partition("#")[0]) or unquote(raw))
                    if problem:
                        problems.append(Problem(f"{page.source}:{line}", problem))
                    child.attrSet(attr, new)
                    links.append((line, new))
                elif child.type == "html_inline":
                    child.content = _rewrite_raw(child.content, page, linker, problems)
        elif token.type == "html_block":
            token.content = _rewrite_raw(token.content, page, linker, problems)

    first = tokens[0] if tokens else None
    if not (first and first.type == "heading_open" and first.tag == "h1"):
        raise ValueError(f"{page.source} does not start with a '# ' title")
    title_html = MD.renderer.render(tokens[1:2], MD.options, env)
    body = MD.renderer.render(tokens[3:], MD.options, env)
    # The Markdown pass sees the same links as the HTML pass, which reports their problems.
    markdown, _ = rewrite_markdown(text, page.source, linker)
    opens = [i for i, token in enumerate(tokens) if token.type == "paragraph_open"]
    lead = " ".join(_plain(tokens[opens[0] + 1]).split()) if opens else ""
    return Rendered(
        description=lead[:200],
        lead=lead,
        lead_line=(tokens[opens[0]].map or [0])[0] + 1 if opens else 0,
        lead_unlinked=" ".join(_plain(tokens[opens[0] + 1], links=False).split()) if opens else "",
        title_html=title_html,
        title_id=first.meta["id"],
        body=body,
        toc=toc,
        markdown=markdown,
        problems=problems,
        links=links,
    )


def _rewrite_raw(content: str, page: nav.Page, linker: Linker, problems: list[Problem]) -> str:
    def replace(match: re.Match) -> str:
        new, problem = linker.target(html.unescape(match.group(3)), page.source, image=match.group(1) == "src")
        if problem:
            problems.append(Problem(page.source, problem))
        return f"{match.group(1)}={match.group(2)}{html.escape(new)}{match.group(2)}"

    return RAW_ATTR.sub(replace, content)


def pygments_css() -> str:
    """Token colours for code blocks: one-dark in both themes, as itda.work's code blocks are."""
    css = HtmlFormatter(style="one-dark").get_style_defs(".highlight")
    rules = [line for line in css.splitlines() if line.startswith(".highlight ") and " .hll " not in line]
    return "\n".join(rules) + "\n"


def source_url(page: nav.Page, tag: str) -> str:
    """The page's document on GitHub at the release tag."""
    return nav.pin(f"{nav.REPOSITORY}/blob/main/{page.source}", tag)
