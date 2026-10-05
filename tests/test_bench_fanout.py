"""The broadcast fan-out profile (#176): what the design quotes is what was measured, and the probes still probe.

docs/design/broadcast-fanout.md quotes ``bench/results/5a4f037-fanout-profile.json``
through ``bench.fanout_profile.facts()`` and ``chart()``. A hand-edited number, or a
number from another measurement, fails here.

``bench/fanout_profile.py`` wraps the library's internals from the outside
(``WireviewMeta._collect_context``, ``Rendered.from_marked_html``, Channels'
``aclose_old_connections`` ...). Renaming one breaks the wrapping; worse, a wrapped
function the render path no longer calls records nothing and the profile quietly
shows zero for that stage -- what #145 did to ``make bench``. Neither CI nor pyright
reads bench/, so both modes run here once at a tiny size.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bench import fanout_profile

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "design" / "broadcast-fanout.md"
#: A number in a table cell: 1,548.6 or 47.6% or 210.6~217.0
CELL_NUMBER = re.compile(r"\d[\d,]*\.\d+%?")


@pytest.fixture(scope="module")
def result():
    return json.loads(fanout_profile.RESULT.read_text())


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n{heading}")
    end = text.find("\n### ", start + len(heading) + 1)
    return text[start : end if end != -1 else None]


#: Which section of the design quotes which facts, by key prefix
SECTIONS = {
    "### 2-2.": ("wireview.stage.", "wireview.share.", "wireview.diff.", "wireview.calls.", "group"),
    "### 2-3.": ("fastapi.stage.", "fastapi.share.", "fastapi.per_connection"),
    "### 2-4.": ("inproc.", "trip."),
    "### 2-5.": ("scale.",),
    "### 2-6.": ("threads.",),
}


@pytest.mark.unit
def test_the_design_quotes_every_fact_where_it_belongs(result):
    text = DESIGN.read_text(encoding="utf-8")
    facts = fanout_profile.facts(result)
    for key, value in facts.items():
        heading = next((h for h, prefixes in SECTIONS.items() if key.startswith(prefixes)), "## 2. 결과")
        where = _section(text, heading) if heading.startswith("###") else text[: text.index("\n## 3. ")]
        assert value in where, f"{heading} does not say {key} = {value}"


@pytest.mark.unit
def test_the_measured_tables_hold_only_the_result(result):
    """Every number in the tables of §2-1..§2-4 is one ``facts()`` gives: none typed in by hand."""
    text = DESIGN.read_text(encoding="utf-8")
    values = set()
    for value in fanout_profile.facts(result).values():
        values.update(CELL_NUMBER.findall(value))
    for heading in ("### 2-1.", "### 2-2.", "### 2-3.", "### 2-4."):
        rows = [line for line in _section(text, heading).splitlines() if line.startswith("|")]
        quoted = {n for row in rows for n in CELL_NUMBER.findall(row)}
        assert quoted <= values, f"{heading} quotes numbers the result does not: {quoted - values}"


@pytest.mark.unit
def test_the_design_shows_the_charts_drawn_from_the_result(result):
    text = DESIGN.read_text(encoding="utf-8")
    for block in fanout_profile.chart(result).split("\n\n"):
        assert block in text, (
            f"the design's chart is stale; run python -m bench.fanout_profile --chart {fanout_profile.RESULT}"
        )


def _run(args: list[str], tmp_path: Path) -> dict:
    # The modes pick their own settings: the suite's must not leak in
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    output = tmp_path / "result.json"
    done = subprocess.run(
        [sys.executable, "-m", "bench.fanout_profile", *args, "--output", str(output)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(output.read_text())


@pytest.mark.integration
def test_the_fan_out_probes_see_every_stage(tmp_path):
    measured = _run(
        ["--only", "wireview", "--rounds", "1", "--connections", "3", "--warmup", "1", "--broadcasts", "1"], tmp_path
    )
    summary = measured["implementations"]["wireview"]["summary"]
    assert summary["frames"] == 3
    # Each wrapped function ran: one that the render path stopped calling counts zero
    for call in (
        "loop.layer.send (deepcopy + put)",
        "loop.layer._clean_expired",
        "loop.dispatch (wall)",
        "loop.await close_old_connections trip (wall)",
        "loop.notification handler (wall)",
        "loop.send_render (wall)",
        "loop.render_diff (wall)",
        "loop.await render trip (wall)",
        "loop.diff",
        "loop.diff: parse markers",
        "loop.diff: settle",
        "loop.diff: compare",
        "loop.encode_json",
        "loop.ws_send",
        "worker.thread_handler",
        "worker.close_old_connections",
        "worker.collect_context",
        "worker.template render",
        "worker.sign_state",
    ):
        assert summary["calls"].get(call, 0) >= 3, f"the probe on {call} saw nothing: {summary['calls']}"
    for label, used, _wall in fanout_profile.stages(summary, "wireview"):
        assert used >= 0 or label.startswith("loop: 그 밖"), label


@pytest.mark.integration
def test_the_fan_out_probes_see_the_shared_board(tmp_path):
    """BENCH_SHARED_RENDER reaches the server, and the probes on the shared path record (#176)."""
    measured = _run(
        ["--only", "wireview-shared", "--rounds", "1", "--connections", "3", "--warmup", "1", "--broadcasts", "1"],
        tmp_path,
    )
    summary = measured["implementations"]["wireview-shared"]["summary"]
    assert summary["frames"] == 3
    for call in (
        "loop.shared: key",
        "loop.shared: with_state",
        "loop.diff: against last",
        "loop.sign_state",
        "loop.send_render outside the shared render",
    ):
        assert summary["calls"].get(call, 0) >= 3, f"the probe on {call} saw nothing: {summary['calls']}"
    # One render for the clicker's event and one for the broadcast, whatever the connections
    assert summary["calls"]["worker.template render"] == 2
    assert summary["calls"]["loop.shared: parse"] == 2
    for label, used, _wall in fanout_profile.stages(summary, "wireview-shared"):
        assert used >= 0 or label.startswith("loop: 그 밖"), label


@pytest.mark.integration
def test_the_in_process_profile_takes_one_render_apart(tmp_path):
    # Eight renders at least: the thread comparison splits them over up to eight threads
    measured = _run(["inproc", "--boards", "8", "--repeat", "1"], tmp_path)["inproc"]
    for part, us in measured["board_us"].items():
        assert us > 0, part
    assert measured["ints_localized_per_render"] > 0
    assert measured["render_diff_us_one_after_another"] > 0
    assert all(us > 0 for us in measured["shared_render_us"].values())


# -- stage 1 (B): §6 ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stage_b():
    return json.loads(fanout_profile.RESULT_B.read_text())


def _stage_b_section() -> str:
    text = DESIGN.read_text(encoding="utf-8")
    return text[text.index("\n## 6. ") : text.index("\n## 7. ")]


@pytest.mark.unit
def test_section_6_quotes_every_fact_of_stage_b(stage_b):
    section = _stage_b_section()
    facts = {**fanout_profile.progress_facts(stage_b), **fanout_profile.compare_facts()}
    for key, value in facts.items():
        assert value in section, f"§6 does not say {key} = {value}"


@pytest.mark.unit
def test_section_6_tables_hold_only_the_results(stage_b):
    values = set()
    for value in {**fanout_profile.progress_facts(stage_b), **fanout_profile.compare_facts()}.values():
        values.update(CELL_NUMBER.findall(value))
    rows = [line for line in _stage_b_section().splitlines() if line.startswith("|")]
    quoted = {n for row in rows for n in CELL_NUMBER.findall(row)}
    assert quoted <= values, f"§6 tables quote numbers the results do not: {quoted - values}"


@pytest.mark.unit
def test_section_6_shows_the_charts_drawn_from_the_results(result, stage_b):
    section = _stage_b_section()
    for block in fanout_profile.progress_chart(result, stage_b).split("\n\n"):
        assert block in section, "§6's chart is stale: print bench.fanout_profile.progress_chart() again"


@pytest.mark.integration
def test_the_click_profile_times_every_action(tmp_path):
    measured = _run(["clicks", "--clicks", "3", "--warmup", "1", "--gaps", "0"], tmp_path)["clicks"]
    for action in ("increment", "insert"):
        assert measured["actions"][action]["0.0"]["median_ms"] > 0


# -- stage 2 (A): §7 ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stage_a():
    return json.loads(fanout_profile.RESULT_A.read_text())


def _stage_a_section() -> str:
    text = DESIGN.read_text(encoding="utf-8")
    return text[text.index("\n## 7. ") :]


@pytest.mark.unit
def test_section_7_quotes_every_fact_of_stage_a(stage_a):
    section = _stage_a_section()
    for key, value in fanout_profile.shared_facts(stage_a).items():
        if key.startswith("a.stage."):
            label = key.removeprefix("a.stage.").split(": ", 1)[-1]
            assert f"| {label} | " in section and f" | {value} |" in section, f"§7-3 does not say {key} = {value}"
        else:
            assert value in section, f"§7 does not say {key} = {value}"


@pytest.mark.unit
def test_section_7_measured_tables_hold_only_the_results(stage_a, stage_b):
    """§7-2..§7-5: every number in a table is one the results give, §6's quoted beside A's."""
    values = set()
    facts = {**fanout_profile.shared_facts(stage_a), **fanout_profile.progress_facts(stage_b)}
    for value in facts.values():
        values.update(CELL_NUMBER.findall(value))
    section = _stage_a_section()
    measured = section[section.index("\n### 7-2.") : section.index("\n### 7-6.")]
    rows = [line for line in measured.splitlines() if line.startswith("|")]
    quoted = {n for row in rows for n in CELL_NUMBER.findall(row)}
    assert quoted <= values, f"§7 tables quote numbers the results do not: {quoted - values}"


@pytest.mark.unit
def test_section_7_shows_the_charts_drawn_from_the_results(stage_a, stage_b):
    section = _stage_a_section()
    for block in fanout_profile.shared_chart(stage_b, stage_a).split("\n\n"):
        assert block in section, "§7's chart is stale: print bench.fanout_profile.shared_chart() again"


@pytest.mark.unit
def test_the_performance_guide_quotes_stage_a(stage_a):
    guide = (ROOT / "docs" / "PERFORMANCE.md").read_text(encoding="utf-8")
    part = guide[guide.index("#### 브로드캐스트를 많은 연결이 받을 때") : guide.index("### 기대치")]
    facts = fanout_profile.shared_facts(stage_a)
    for key in ("wireview", "wireview-shared", "fastapi"):
        assert f"| {facts[f'a.{key}.fanout']} | {facts[f'a.{key}.per_connection']} |" in part, key
    assert fanout_profile.shared_performance_chart(stage_a) in part
    assert fanout_profile.RESULT_A.name in part
