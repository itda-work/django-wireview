"""Guard: the table at the top of docs/UPGRADING.md sends every reader through every section they need.

1.0.0rc4-to-1.0 was added above the table's sections, and every row still ended at the
section before it -- the rc4 row said there was nothing to change -- so a reader who
trusted the table missed two silent changes.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

UPGRADING = Path(__file__).resolve().parent.parent / "docs" / "UPGRADING.md"


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _migration_sections() -> list[str]:
    """The ``## <from>에서 <to>`` sections, newest first as the document orders them."""
    return [_slug(h) for h in re.findall(r"^## (.+에서 .+)$", UPGRADING.read_text(), re.MULTILINE)]


def _rows() -> dict[str, str]:
    table = UPGRADING.read_text().split("## 어디서 오나", 1)[1].split("\n## ", 1)[0]
    return {
        cells[0]: cells[1]
        for line in table.splitlines()
        if line.startswith("| ") and not line.startswith("| 쓰던") and not set(line) <= set("|- ")
        for cells in [[c.strip() for c in line.strip("|").split("|")]]
    }


def test_every_row_ends_with_the_newest_section():
    newest = _migration_sections()[0]

    assert {version: links.rsplit("(#", 1)[-1].split(")", 1)[0] for version, links in _rows().items()} == {
        version: newest for version in _rows()
    }


def test_every_row_reads_its_sections_oldest_first():
    """Sections are newest first in the document; a row lists them in the order to apply them."""
    order = _migration_sections()
    for version, links in _rows().items():
        sections = [a for a in re.findall(r"\(#([^)]+)\)", links) if a in order]
        assert sections == sorted(sections, key=order.index, reverse=True), version
