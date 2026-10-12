"""Guard: the example tables and counts say what examples/ holds.

The root README said "14 example apps" over a table of 11 rows: the chat series
had been added to the index (examples/README.md) and to the count, and not to the
root table, whose descriptions had drifted from the index's too. Nothing compared
them. The index is the canonical table: every example directory has a row there,
and the root table's rows are the index's, word for word.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

INDEX_ROW = re.compile(r"^\| \[([a-z0-9_]+)\]\(\./\1/\) \| (.+?) \| .+ \|$", re.MULTILINE)
ROOT_ROW = re.compile(
    # The columns after the description (the screenshot, #213) are tests/test_example_media.py's
    r"^\| \[([a-z0-9_]+)\]\(https://github\.com/itda-work/django-wireview/tree/main/examples/\1\) "
    r"\| (.+?) \|(?: .+ \|)?$",
    re.MULTILINE,
)
# "예제 앱 14개" in the index and CLAUDE.md, "예제 앱 14개가" in the root README
COUNT = re.compile(r"예제 앱 (\d+)개")


def _directories() -> set[str]:
    """Each example app: a directory of examples/ that is a Python package."""
    return {path.name for path in EXAMPLES.iterdir() if (path / "__init__.py").is_file()}


def _first_section(text: str) -> str:
    """The text before the first second-level heading: the index's own table, not the chat series'."""
    return re.split(r"^## ", text, maxsplit=1, flags=re.MULTILINE)[0]


def _index() -> list[tuple[str, str]]:
    return INDEX_ROW.findall(_first_section((EXAMPLES / "README.md").read_text(encoding="utf-8")))


def _root_table() -> list[tuple[str, str]]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text[text.index("\n## 예제\n") :]
    return ROOT_ROW.findall(_first_section(section.split("\n", 2)[2]))


def test_the_index_has_a_row_for_every_example_and_no_other():
    names = [name for name, _ in _index()]

    assert len(names) == len(set(names)), names
    assert set(names) == _directories()


def test_the_root_tables_rows_are_the_indexs():
    """The root README's table is the index's, in its order and its words: the index is where they are written."""
    assert _root_table() == _index()


@pytest.mark.parametrize("document", ["README.md", "examples/README.md", "CLAUDE.md"])
def test_the_count_of_examples_is_the_number_of_directories(document):
    counts = COUNT.findall((ROOT / document).read_text(encoding="utf-8"))

    assert counts, f"{document} does not say how many examples there are"
    assert {int(count) for count in counts} == {len(_directories())}, counts
