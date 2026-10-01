"""The README's examples run as written.

README.md is the PyPI front page, so its examples are the first a reader copies.
The Streams example named an item template the component never looks for, and
its item template read ``message`` where the context holds ``item``: copied, it
failed with ``TemplateDoesNotExist``, and given the template by hand it drew
empty items.

Each example here is taken out of the README by its heading, not retyped, so the
test reads what the reader reads.
"""

import itertools
import re
import types
from pathlib import Path

import pytest
from django.test import override_settings

from wireview import Component, mount

pytestmark = pytest.mark.integration

README = Path(__file__).resolve().parent.parent / "README.md"
FENCE = re.compile(r"^```(\w*)\s*$")
PATH = re.compile(r"`([\w/.-]+\.html)`")


def _section(title: str, sub: str) -> list[str]:
    """Lines under ``## title`` → ``### sub``, up to the next heading of either level."""
    lines = README.read_text(encoding="utf-8").split("\n")
    start = lines.index(f"## {title}")
    start = lines.index(f"### {sub}", start)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith(("## ", "### ")))
    return lines[start + 1 : end]


def _blocks(lines: list[str]) -> list[tuple[str, str, str]]:
    """(language, code, the paragraph before the fence) for each fenced block."""
    found, i, caption, paragraph = [], 0, "", []
    while i < len(lines):
        match = FENCE.match(lines[i])
        if match:
            body = []
            i += 1
            while not FENCE.match(lines[i]):
                body.append(lines[i])
                i += 1
            found.append((match.group(1), "\n".join(body), caption))
        elif lines[i].strip():
            paragraph.append(lines[i])
            caption = " ".join(paragraph)
        else:
            paragraph = []
        i += 1
    return found


class _Rows:
    """Stands in for a sliced QuerySet: what ``async for`` and ``[:n]`` need."""

    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *_):
        return self

    def __getitem__(self, s):
        return _Rows(self.rows[s])

    async def __aiter__(self):
        for row in self.rows:
            yield row


MESSAGES = [types.SimpleNamespace(pk=pk, sender=f"user{pk}", text=f"hello {pk}") for pk in (3, 2, 1)]


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_the_streams_example_renders_its_items():
    blocks = _blocks(_section("Streams API", "기본 사용법"))
    html = [(code, caption) for lang, code, caption in blocks if lang == "html"]
    (python,) = [code for lang, code, _ in blocks if lang == "python"]
    assert len(html) == 2, "a container template and an item template"

    message = types.SimpleNamespace(objects=_Rows(MESSAGES))
    namespace = {"Component": Component, "Message": message, "__name__": "readme_streams"}
    exec(python, namespace)  # noqa: S102 - the README's own code
    component = next(
        v for v in namespace.values() if isinstance(v, type) and issubclass(v, Component) and v is not Component
    )

    (container, _), (item, caption) = html
    item_path = PATH.search(caption)
    assert item_path, f"the item template's caption names its file: {caption!r}"
    templates = {component._meta.template_name: container, item_path.group(1): item}
    engine = {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", templates)]},
    }
    with override_settings(TEMPLATES=[engine]):
        view = await mount(component)
        page = view.render()

    assert 'wire-stream="messages"' in page
    items = view.stream_items("messages")
    assert [i["id"] for i in items] == ["messages-1", "messages-2", "messages-3"]
    for row, entry in zip(reversed(MESSAGES), items, strict=True):
        assert f'id="messages-{row.pk}"' in entry["html"]
        assert row.sender in entry["html"]
        assert row.text in entry["html"]


def _signature(name: str) -> str:
    """``JS.<name>``'s signature as the README writes it: defaults shown, ``*`` before keyword-only."""
    import inspect

    from wireview import JS

    parts, star = [], False
    for param in list(inspect.signature(getattr(JS, name)).parameters.values())[1:]:
        if param.kind is param.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        parts.append(param.name if param.default is param.empty else f"{param.name}={param.default!r}")
    return f"{name}({', '.join(parts)})"


def test_the_js_commands_are_listed_with_their_signatures():
    """Copied as listed, ``toggle(show=...)`` and ``push(event, value)`` raised TypeError."""
    listed = re.findall(r"^- `(\w+)\((.*?)\)`", "\n".join(_section("JS 명령 빌더", "사용 가능한 명령")), re.M)
    assert len(listed) >= 15
    assert [f"{name}({args})" for name, args in listed] == [_signature(name) for name, _ in listed]


def test_the_transition_forms_listed_are_the_ones_js_takes():
    """It said only a ``("class", ms)`` tuple, so a reader never learned that a string is class names alone."""
    import ast
    import typing as t

    from wireview.js import JS, Transition

    lines = _section("JS 명령 빌더", "사용 가능한 명령")
    start = next(i for i, x in enumerate(lines) if x.startswith("`transition`"))
    line = " ".join(itertools.takewhile(str.strip, lines[start:]))
    accepted_part, rest = line.split("이다.", 1)
    forms = [ast.literal_eval(code) for code in re.findall(r"`([(\"{][^`]*)`", accepted_part)]
    accepted = [arg for arg in t.get_args(Transition) if arg is not type(None)]
    assert [type(form) for form in forms] == [str, tuple, dict] and len(accepted) == 3, line
    for form in forms:
        JS().hide(transition=form)
    # And what it says about a duration in the string
    assert "ValueError" in rest
    with pytest.raises(ValueError):
        JS().hide(transition="fade-out 200ms")


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _headings() -> list[str]:
    found, fenced = [], False
    for line in README.read_text(encoding="utf-8").split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (match := re.match(r"^#{1,6} (.+)$", line)):
            found.append(match.group(1))
    return found


def test_each_heading_has_its_own_anchor():
    """Two headings with one slug: GitHub gives the second ``-1``, and the table of contents opens the first.

    "모델 구독" and "설정" each had a subsection of that name above the section
    the contents linked to.
    """
    slugs = [_slug(h) for h in _headings()]
    assert sorted({s for s in slugs if slugs.count(s) > 1}) == []


def test_the_contents_link_to_headings():
    targets = set(re.findall(r"\]\(#([^)]+)\)", README.read_text(encoding="utf-8")))
    assert targets
    assert sorted(targets - {_slug(h) for h in _headings()}) == []


BUNDLE = README.parent / "wireview" / "static" / "wireview" / "wireview.min.js"
TUTORIAL_01 = README.parent / "docs" / "tutorials" / "01-getting-started.md"


@pytest.mark.skipif(not BUNDLE.exists(), reason="wireview.min.js is built by make build-js")
@pytest.mark.parametrize("doc", [README, TUTORIAL_01], ids=lambda p: p.name)
def test_the_bundle_size_is_the_built_one(doc):
    """The README said "~10KB compressed" of a bundle that was 71 KB, 22 KB gzipped.

    Within a quarter either way: the number is a reader's estimate, not a budget.
    """
    import gzip

    stated = re.search(r"약 (\d+)KB, gzip 약 (\d+)KB", doc.read_text(encoding="utf-8"))
    assert stated, f"{doc.name} states the bundle's size"
    data = BUNDLE.read_bytes()
    for kb, actual in zip(map(int, stated.groups()), (len(data), len(gzip.compress(data))), strict=True):
        assert 0.75 * actual <= kb * 1000 <= 1.25 * actual, (kb, actual)


def test_the_hook_example_says_where_its_file_goes():
    """An inline ``<script>`` runs before the bundle, and not at all after a boosted navigation."""
    (_, code, _), *_ = (b for b in _blocks(_section("JavaScript Hooks", "Hook 정의")) if b[0] == "javascript")
    assert re.match(r"// (\w+)/static/\1/hooks/\w+\.js\n", code), code.split("\n")[0]


#: Where the README's links to the rest of the repository point. PyPI shows the
#: README as the project page, with no repository to resolve a relative link in.
REPOSITORY = "https://github.com/itda-work/django-wireview/blob/main/"
DIRECTORY = "https://github.com/itda-work/django-wireview/tree/main/"
RAW = "https://raw.githubusercontent.com/itda-work/django-wireview/main/"


def _targets() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'src="([^"]+)"', text)


def test_no_link_is_relative():
    """On PyPI ``./docs/...`` and ``overview.jpg`` resolved against pypi.org: 33 broken links and no picture."""
    relative = [t for t in _targets() if not t.startswith(("#", "http://", "https://", "mailto:"))]
    assert relative == []


def test_the_repository_links_name_files_that_exist():
    """Absolute links are no longer checked by GitHub's renderer: a moved document would 404 quietly."""
    missing = []
    for target in _targets():
        for base in (REPOSITORY, DIRECTORY, RAW):
            if target.startswith(base):
                path = target.removeprefix(base).split("#")[0]
                if not (README.parent / path).exists():
                    missing.append(target)
    assert missing == []


def test_the_package_page_links_to_its_own_release():
    """PyPI keeps every release's page. Pinned to main, 1.0.0's page would show the docs of whatever came later,
    and a moved file would break the links of every older page. The build points them at the release's tag."""
    import tomllib

    import hatch_build

    hatch = tomllib.loads((README.parent / "pyproject.toml").read_text())["tool"]["hatch"]
    config = hatch["metadata"]["hooks"]["custom"]
    metadata = {"version": "1.2.3"}
    hatch_build.CustomMetadataHook(str(README.parent), config).update(metadata)

    urls = metadata["urls"]
    assert urls["Documentation"] == "https://github.com/itda-work/django-wireview/tree/v1.2.3/docs"
    assert urls["Changelog"] == "https://github.com/itda-work/django-wireview/blob/v1.2.3/CHANGELOG.md"
    assert urls["Issues"] == "https://github.com/itda-work/django-wireview/issues"

    assert metadata["readme"]["content-type"] == "text/markdown"
    text = metadata["readme"]["text"]
    assert "/main/" not in text
    assert text.count("/v1.2.3/") == README.read_text(encoding="utf-8").count("/main/") > 0
    assert text.replace("/v1.2.3/", "/main/") == README.read_text(encoding="utf-8")
