"""The README's examples run as written.

README.md is the PyPI front page, so its examples are the first a reader copies.
The Streams example named an item template the component never looks for, and
its item template read ``message`` where the context holds ``item``: copied, it
failed with ``TemplateDoesNotExist``, and given the template by hand it drew
empty items.

Each example here is taken out of the README by its heading, not retyped, so the
test reads what the reader reads.
"""

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
