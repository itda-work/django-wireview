"""Guard: the tutorials lead where the site navigation says they do (#126, #158).

``docs/tutorials/README.md`` once recommended 01 -> 02 -> 10 -> 11 -> 03 -> 12 ..., while
each tutorial's "next" link went 01 -> 02 -> 03 ... -> 09 and stopped there, so
10 to 15 could not be reached by following the tutorials at all. The files keep
their numbers (examples and the feature reference link to them by name).

The learning order, each tutorial's level and estimated time live in
``docs/site.toml``, which also builds the documentation site. The README's
learning path, its level headings, its time table and total, and every
tutorial's closing navigation line are written for GitHub readers; each is
checked here against what ``docs/site.toml`` implies. A tutorial's short title
in all of them is its own ``# <title>`` up to the first " - ".
"""

import re
from pathlib import Path

import pytest

from scripts.docs_site import nav as site_nav

pytestmark = pytest.mark.unit

TUTORIALS = Path(__file__).resolve().parent.parent / "docs" / "tutorials"
INDEX = (TUTORIALS / "README.md").read_text(encoding="utf-8")
FILES = sorted(p.name for p in TUTORIALS.glob("[0-9][0-9]-*.md"))

LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
NAV = site_nav.TUTORIAL_NAV

#: The tutorials in the order docs/site.toml lists them.
PATH = site_nav.tutorials()
ORDER = [Path(page.source).name for page in PATH]
TITLES = {Path(page.source).name: page.short_title for page in PATH}
LEVELS = {level["slug"]: level for level in site_nav.levels()}


def _section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


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


@pytest.mark.parametrize("file", FILES)
def test_a_title_carries_no_number(file):
    """The files are numbered in the order they were written, and the path reads them in another:
    a numbered title made the sidebar and this README read 01, 02, 10, 11, 03, 12 ... The list's
    position and its level headings tell the order; the file names keep the numbers for links."""
    title = _text(file).split("\n", 1)[0]
    assert title.startswith("# ")
    assert not re.match(r"\d+\. ", title[2:]), f"{file}: {title}"


def test_the_order_lists_every_tutorial_once():
    assert sorted(ORDER) == FILES
    assert len(ORDER) == len(set(ORDER))


def test_the_path_visits_each_level_once_in_order():
    ranks = [list(LEVELS).index(page.level) for page in PATH]
    assert ranks == sorted(ranks)


def test_the_learning_path_is_the_order():
    """The README's path: a "### <level> (<label>)" heading, then its tutorials as "- [title](file) - summary"."""
    expected: list[str] = []
    for page in PATH:
        level = LEVELS[page.level]
        heading = f"### {level['name']} ({level['label']})"
        if heading not in expected:
            expected.append(heading)
        expected.append(f"- [{page.short_title}]({Path(page.source).name}) - {page.summary}")
    written = [line for line in _section(INDEX, "학습 경로").splitlines() if line.startswith(("### ", "- "))]
    assert written == expected


def test_the_time_table_is_the_order():
    rows = [line for line in _section(INDEX, "학습 시간 예상").splitlines() if line.startswith("| ")][1:]
    expected = [
        f"| {LEVELS[page.level]['name']} | {page.short_title} | {site_nav.human_minutes(page.minutes)} |"
        for page in PATH
    ]
    assert rows == expected


def test_the_total_time_is_the_sum():
    low, high = site_nav.total_minutes([page.minutes for page in PATH])
    total = site_nav.human_minutes((low, high) if low != high else low)
    assert f"**총 학습 시간**: 약 {total}" in _section(INDEX, "학습 시간 예상").splitlines()


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
