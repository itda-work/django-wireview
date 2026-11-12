"""Guard: counts and measurements the documents quote are what the repository holds (#229).

README and docs/WHY.md said ``manage.py check`` caught "17" mistakes while ``wireview/checks.py``
emitted 22 ids; docs/WHY.md quoted 0.28 ms and 1.1 ms after newer measurements had been
committed; docs/ROADMAP.md said 11 example apps for 14. Each was right once and nothing
compared it again. These tests recount from the source every run.
"""

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "wireview" / "checks.py"
PERFORMANCE = ROOT / "docs" / "PERFORMANCE.md"
RESULTS = ROOT / "bench" / "results"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# -- system checks ------------------------------------------------------------------------


def _check_ids() -> set[str]:
    """The ids ``wireview/checks.py`` can emit: ``id="wireview.Wnnn"`` in a message it builds."""
    return set(re.findall(r'id="(wireview\.W\d{3})"', CHECKS.read_text(encoding="utf-8")))


def test_the_check_ids_are_found():
    # A change in how checks.py spells its ids must not pass as "zero checks, zero claims".
    assert len(_check_ids()) > 10


# "함정 23가지를" in README, "실수 23가지(" in WHY
CHECK_COUNT = re.compile(r"(?:함정|실수) (\d+)가지")


@pytest.mark.parametrize("document", ["README.md", "docs/WHY.md"])
def test_the_count_of_checks_is_the_number_of_check_ids(document):
    counts = CHECK_COUNT.findall(_read(document))

    assert counts, f"{document} does not say how many checks there are"
    assert {int(count) for count in counts} == {len(_check_ids())}, counts


# -- tutorials ----------------------------------------------------------------------------


def _tutorials() -> list[Path]:
    return sorted((ROOT / "docs" / "tutorials").glob("[0-9][0-9]-*.md"))


# "튜토리얼 15편", "tutorials/ 15편", "[튜토리얼](tutorials/README.md)이 15편"
TUTORIAL_COUNT = re.compile(r"(?:튜토리얼|tutorials/)\S* (\d+)편|튜토리얼\]\([^)]*\)이 (\d+)편")


@pytest.mark.parametrize("document", ["docs/WHY.md", "docs/ROADMAP.md", "CLAUDE.md", "skills/wireview/SKILL.md"])
def test_the_count_of_tutorials_is_the_number_of_tutorials(document):
    counts = [int(a or b) for a, b in TUTORIAL_COUNT.findall(_read(document))]

    assert counts, f"{document} does not say how many tutorials there are"
    assert set(counts) == {len(_tutorials())}, counts


# -- docs/PERFORMANCE.md's standalone measurements ----------------------------------------

#: A row of the standalone table: value, then the source cell naming a file or "같은 파일"
ROW = re.compile(r"^\|[^|]*\| ([\d,.]+) ?(ms|B|KB|/s) \| [^|]* \| (.+) \|$")
SOURCE = re.compile(r"`bench/results/([\w.-]+\.json)`의 `([\w.]+)`")
SAME_FILE = re.compile(r"같은 파일의 `([\w.]+)`")


def _standalone_rows() -> list[tuple[str, str, str, str]]:
    """(value, unit, file, key) for each standalone row whose source names a key."""
    text = PERFORMANCE.read_text(encoding="utf-8")
    start = text.index("#### wireview 단독 측정")
    section = text[start : text.index("\n#### ", start + 1)]
    rows, current = [], None
    for line in section.split("\n"):
        if not (match := ROW.match(line)):
            continue
        value, unit, source = match.groups()
        if named := SOURCE.search(source):
            current, key = named.group(1), named.group(2)
        elif same := SAME_FILE.search(source):
            key = same.group(1)
        elif source.strip() == "같은 파일" and rows:
            key = rows[-1][3]  # the previous row's key: a ws.* entry with one field per unit
        else:
            continue
        assert current, f"{line}: 같은 파일 before any file is named"
        rows.append((value, unit, current, key))
    return rows


#: Which field of a ``ws.items_N`` entry a unit quotes
WS_FIELD = {"KB": "per_connection_kb", "/s": "events_per_s", "ms": "broadcast_ms"}


def _lookup(result: dict, key: str):
    """``timing.flat.event_ms`` -> result["timing"]["flat.event_ms"]; ``ws.items_5`` -> a dict."""
    section, _, rest = key.partition(".")
    return result[section][rest]


def test_the_standalone_table_is_parsed():
    assert len(_standalone_rows()) == 7


@pytest.mark.parametrize("row", _standalone_rows(), ids=lambda row: f"{row[2]}:{row[3]}")
def test_each_standalone_number_is_its_named_result(row):
    value, unit, file, key = row
    measured = _lookup(json.loads((RESULTS / file).read_text(encoding="utf-8")), key)
    if isinstance(measured, dict):
        measured = measured.get(WS_FIELD[unit])
    assert isinstance(measured, (int, float)), f"{file} {key} is not a number"

    stated = float(value.replace(",", ""))
    decimals = len(value.partition(".")[2])
    assert round(measured, decimals) == stated, f"{file} {key} is {measured}, the table says {value} {unit}"


def test_the_standalone_table_quotes_the_newest_timing_result():
    """The event timings come from the newest committed ``make bench`` run that measured them."""
    timed = []
    for path in RESULTS.glob("*.json"):
        if path.stem.endswith("-dirty") or path.stem.startswith("win11-"):
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(result, dict) and "flat.event_ms" in result.get("timing", {}):
            timed.append((result["meta"]["date"], path.name))
    newest = max(timed)[1]

    files = {file for _, _, file, key in _standalone_rows() if key.startswith("timing.")}
    assert files == {newest}


def test_why_quotes_no_timing_of_its_own():
    """docs/WHY.md points at PERFORMANCE.md for the server's time instead of copying it."""
    text = _read("docs/WHY.md")
    section = text[text.index("## 느리다") : text.index("\n## ", text.index("## 느리다") + 1)]

    assert not re.findall(r"\d(?:\.\d+)? ?(?:ms|B)\b", section)
