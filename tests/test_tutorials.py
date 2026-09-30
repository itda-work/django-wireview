"""Guard: the tutorials lead where their index says they do (#126).

``docs/tutorials/README.md`` recommended 01 -> 02 -> 10 -> 11 -> 03 -> 12 ..., while
each tutorial's "next" link went 01 -> 02 -> 03 ... -> 09 and stopped there, so
10 to 15 could not be reached by following the tutorials at all. The files keep
their numbers (examples and the feature reference link to them by name); the
index is the order, and every tutorial's closing navigation line is derived
from it here.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TUTORIALS = Path(__file__).resolve().parent.parent / "docs" / "tutorials"
INDEX = (TUTORIALS / "README.md").read_text(encoding="utf-8")
FILES = sorted(p.name for p in TUTORIALS.glob("[0-9][0-9]-*.md"))

LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
NAV = re.compile(r"^\[(← |목차\])")


def _section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


#: (title, file) in the order the index recommends.
PATH = re.findall(r"^- \[([^\]]+)\]\(([^)]+)\)", _section(INDEX, "학습 경로"), re.MULTILINE)
ORDER = [file for _, file in PATH]
TITLES = dict((file, title) for title, file in PATH)


def _expected_nav(file: str) -> str:
    i = ORDER.index(file)
    parts = []
    if i > 0:
        parts.append(f"[← 이전: {TITLES[ORDER[i - 1]]}]({ORDER[i - 1]})")
    parts.append("[목차](README.md)")
    if i < len(ORDER) - 1:
        parts.append(f"[다음: {TITLES[ORDER[i + 1]]} →]({ORDER[i + 1]})")
    return " | ".join(parts)


def _text(file: str) -> str:
    return (TUTORIALS / file).read_text(encoding="utf-8")


def test_the_index_lists_every_tutorial_once():
    assert sorted(ORDER) == FILES
    assert len(ORDER) == len(set(ORDER))


def test_the_time_table_follows_the_path():
    rows = [line for line in _section(INDEX, "학습 시간 예상").splitlines() if line.startswith("| ")][1:]
    numbers: list[str] = []
    for row in rows:
        cell = row.strip("|").split("|")[1].strip()
        if match := re.fullmatch(r"(\d\d)-(\d\d)", cell):
            numbers += [f"{n:02d}" for n in range(int(match.group(1)), int(match.group(2)) + 1)]
        else:
            numbers.append(cell[:2])
    assert numbers == [file[:2] for file in ORDER]


@pytest.mark.parametrize("file", FILES)
def test_each_tutorial_ends_with_the_navigation_the_index_implies(file):
    navs = [line for line in _text(file).splitlines() if NAV.match(line)]
    assert navs == [_expected_nav(file)]
    assert _text(file).rstrip().endswith(_expected_nav(file))


@pytest.mark.parametrize("file", FILES)
def test_next_steps_point_forward(file):
    """A "다음 단계" list that names a tutorial names one that comes later on the path."""
    text = _text(file)
    if "\n## 다음 단계\n" not in text:
        return
    section = "\n".join(line for line in _section(text, "다음 단계").splitlines() if not NAV.match(line))
    for _, target in LINK.findall(section):
        name = target.removeprefix("./")
        if name in ORDER:
            assert ORDER.index(name) > ORDER.index(file), f"{file} sends the reader back to {name}"


@pytest.mark.parametrize("file", FILES)
def test_prerequisites_come_earlier(file):
    text = _text(file)
    if "\n## 전제 조건\n" not in text:
        return
    for _, target in LINK.findall(_section(text, "전제 조건")):
        name = target.removeprefix("./")
        if name in ORDER:
            assert ORDER.index(name) < ORDER.index(file), f"{file} requires {name}, which comes after it"


@pytest.mark.parametrize("file", ["README.md", *FILES])
def test_every_relative_link_resolves(file):
    missing = []
    for _, target in LINK.findall(_text(file)):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        if not (TUTORIALS / target.split("#", 1)[0]).exists():
            missing.append(target)
    assert missing == []
