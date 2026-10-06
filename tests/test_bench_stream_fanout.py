"""The stream fan-out measurement (#178): what the documents quote is what was measured.

docs/PERFORMANCE.md, docs/features/broadcast.md, docs/design/broadcast-patch.md §11
and CHANGELOG.md quote ``bench/results/3ab5818-stream-fanout.json`` (D2) and
``cd6a6ae-stream-fanout-d1.json`` (D1) through ``bench.compare_fastapi.stream_fanout``.
A hand-edited number, or one from another measurement, fails here.
"""

from pathlib import Path

import pytest

from bench.compare_fastapi import stream_fanout

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design" / "broadcast-patch.md"
PERFORMANCE = ROOT / "docs" / "PERFORMANCE.md"
FEATURE = ROOT / "docs" / "features" / "broadcast.md"
CHANGELOG = ROOT / "CHANGELOG.md"


@pytest.fixture(scope="module")
def d2():
    return stream_fanout.load()


@pytest.fixture(scope="module")
def d1():
    return stream_fanout.load(stream_fanout.D1_RESULT)


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n{heading}")
    end = text.find("\n### ", start + len(heading) + 1)
    return text[start : end if end != -1 else None]


def _range(values: list[float], unit: str) -> str:
    return f"{min(values):.1f}~{max(values):.1f} {unit}"


def test_the_measurement_is_what_it_says_it_is(d1, d2):
    for result in (d1, d2):
        assert result["method"]["connections"] == 1000
        assert result["method"]["rounds"] == 3
        for layer in stream_fanout.LAYERS:
            assert len(result["summary"]["wireview-broadcast"][layer]["fanout_ms_rounds"]) == 3
    # D2 met what the design asked of it: at most 30 ms on Redis and NATS, one process
    for layer in ("redis", "nats"):
        assert d2["summary"]["wireview-broadcast"][layer]["fanout_ms"] <= 30


def test_the_design_shows_both_stages_and_the_comparison(d1, d2):
    text = DESIGN.read_text(encoding="utf-8")
    stages = _section(text, "### 11-2.")
    assert stream_fanout.stages_table(d1, d2) in stages
    assert stream_fanout.stages_chart(d1, d2) in stages
    comparison = _section(text, "### 11-3.")
    assert stream_fanout.table(d2) in comparison
    assert stream_fanout.chart(d2) in comparison


def test_the_design_summary_quotes_the_range_of_d2(d2):
    broadcast = d2["summary"]["wireview-broadcast"]
    fanout = _range([x["fanout_ms"] for x in broadcast.values()], "ms")
    cpu = _range([x["cpu_per_connection_us"] for x in broadcast.values()], "µs")
    text = DESIGN.read_text(encoding="utf-8")
    assert f"{fanout}, 연결당 {cpu} (실측, §11)" in _section(text, "## 8.")
    assert f"연결당 CPU {cpu}" in _section(text, "### 11-2.")


def test_the_frame_sizes_quoted_are_the_measured_ones(d2):
    facts = stream_fanout.facts(d2)
    wireview, fastapi = facts["wireview-broadcast.redis.bytes"], facts["fastapi.none.bytes"]
    assert f"프레임은 wireview {wireview}, FastAPI {fastapi}다" in DESIGN.read_text(encoding="utf-8")
    assert f"({wireview} 대 {fastapi})" in PERFORMANCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("document", [PERFORMANCE, FEATURE], ids=["PERFORMANCE", "feature"])
def test_the_guides_show_the_table_and_the_chart(d2, document):
    text = document.read_text(encoding="utf-8")
    assert stream_fanout.table(d2) in text
    assert stream_fanout.chart(d2) in text
    assert f"bench/results/{stream_fanout.RESULT.name}" in text


def test_the_changelog_quotes_the_same_ranges(d2):
    summary = d2["summary"]
    broadcast = [x["fanout_ms"] for x in summary["wireview-broadcast"].values()]
    notified = [x["fanout_ms"] for x in summary["wireview"].values()]
    entry = " ".join(CHANGELOG.read_text(encoding="utf-8").split())
    assert f"takes {min(broadcast):.1f} to {max(broadcast):.1f} ms" in entry
    assert f"against {min(notified):.1f} to {max(notified):.1f} ms" in entry


def test_the_readme_quotes_the_same_ranges(d2):
    summary = d2["summary"]
    broadcast = [x["fanout_ms"] for x in summary["wireview-broadcast"].values()]
    notified = [x["fanout_ms"] for x in summary["wireview"].values()]
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert f"하면 {_range(notified, 'ms')}, `Broadcast`는 {_range(broadcast, 'ms')}였다" in readme
