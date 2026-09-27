"""Guard: every ✅ in ``docs/FEATURE-GAP.md`` names a test that runs the feature.

Four features marked ✅ did not work (#75, #84, #83, #67): nothing stood between
"the table says so" and "the code does so". Each row of the comparison table in
section 2 now ends with an evidence cell of pytest node ids, and these tests fail
when a ✅ row has none, when a named test does not exist, or when the overview's
counts drift from the table.

Whether a named test really exercises its feature is a review question, not a
parse; the criteria are written next to the overview in the document itself.
"""

import ast
import re
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "FEATURE-GAP.md"

# Sections whose rows have not been audited yet (#110). A row here is expected to
# lack evidence; once its section is filled the xfail turns into a strict XPASS
# and the section must come off this list.
UNAUDITED = {f"2.{n}" for n in range(1, 16)}

SECTION = re.compile(r"^### (2\.\d+) ")
DIVIDER = re.compile(r"^\|[-:| ]+\|$")
STATUS_KINDS = {"✅": "지원", "🟡": "부분 지원", "🟠": "미지원", "⚪": "설계상 제외"}


def _rows():
    """Yield (section, feature, status, evidence) for each row of section 2."""
    section = None
    for line in DOC.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            section = None
        if match := SECTION.match(line):
            section = match.group(1)
            continue
        if section is None or not line.startswith("|") or DIVIDER.match(line.strip()):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == "기능":
            assert cells[-1] == "근거", f"{section}: the table has no evidence column"
            continue
        assert len(cells) == 5, f"{section}: expected 5 cells, got {len(cells)}: {line}"
        feature, _, _, status, evidence = cells
        yield section, feature.strip("*"), status, evidence


ROWS = list(_rows())
SUPPORTED = [row for row in ROWS if row[2].startswith("✅")]


def _node_ids(evidence: str) -> list[str]:
    return [part.strip().strip("`") for part in re.split(r"<br\s*/?>", evidence) if part.strip()]


@cache
def _definitions(path: Path) -> set[tuple[str, ...]]:
    """Every test node path in a file: ('test_x',) and ('TestY', 'test_x')."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add((node.name,))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add((node.name, child.name))
    return found


def _missing(node_id: str) -> str | None:
    """Why a node id does not resolve, or None if it does."""
    file, _, rest = node_id.partition("::")
    path = ROOT / file
    if not rest or not path.is_file():
        return f"no such test file: {file}"
    parts = tuple(part.split("[", 1)[0] for part in rest.split("::"))
    if parts not in _definitions(path):
        return f"no such test: {node_id}"
    return None


def _row_id(row) -> str:
    return f"{row[0]} {row[1]}"


def test_the_table_is_parsed():
    # A formatting change that hides rows from the parser must not pass as "no rows, no failures".
    assert len(ROWS) > 100
    assert {row[0] for row in ROWS} == {f"2.{n}" for n in range(1, 16)}


def test_the_unaudited_sections_exist():
    assert UNAUDITED <= {row[0] for row in ROWS}


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(row, marks=pytest.mark.xfail(strict=True, reason="not audited yet (#110)"))
        if row[0] in UNAUDITED
        else row
        for row in SUPPORTED
    ],
    ids=_row_id,
)
def test_every_supported_feature_names_its_evidence(row):
    section, feature, _, evidence = row
    node_ids = _node_ids(evidence)
    assert node_ids, f"{section} {feature} is ✅ but names no test that runs it"
    problems = [problem for node_id in node_ids if (problem := _missing(node_id))]
    assert not problems, problems


@pytest.mark.parametrize("row", [row for row in ROWS if _node_ids(row[3])], ids=_row_id)
def test_named_evidence_exists(row):
    # Also for rows that are not ✅: a stale node id is a lie wherever it is written.
    problems = [problem for node_id in _node_ids(row[3]) if (problem := _missing(node_id))]
    assert not problems, problems


def test_every_status_is_a_known_kind():
    unknown = [_row_id(row) for row in ROWS if row[2][:1] not in STATUS_KINDS]
    assert not unknown


def test_every_unsupported_row_has_a_gap_and_an_issue():
    # The overview promises this for 🟠.
    rows = [row for row in ROWS if row[2].startswith("🟠")]
    bare = [_row_id(row) for row in rows if not re.search(r"GAP-\d{3}", row[2]) or not re.search(r"#\d+", row[2])]
    assert not bare


def test_the_overview_counts_the_table():
    text = DOC.read_text(encoding="utf-8")
    overview = text[text.index("## 개요") : text.index("## 1.")]
    counts = Counter(row[2][:1] for row in ROWS)

    total = re.search(r"비교표 (\d+)행", overview)
    assert total and int(total.group(1)) == len(ROWS)
    for mark, label in STATUS_KINDS.items():
        stated = re.search(rf"^\| {mark} {label}[^|]*\| (\d+) \|$", overview, re.MULTILINE)
        assert stated, f"the overview has no row for {mark} {label}"
        assert int(stated.group(1)) == counts[mark], f"{mark} {label}"

    extras = re.search(r"✅ 중 (\d+)행은 Phoenix에 없는", overview)
    assert extras and int(extras.group(1)) == sum(row[2].startswith("✅ 추가 기능") for row in ROWS)
