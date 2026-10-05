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
def test_the_in_process_profile_takes_one_render_apart(tmp_path):
    # Eight renders at least: the thread comparison splits them over up to eight threads
    measured = _run(["inproc", "--boards", "8", "--repeat", "1"], tmp_path)["inproc"]
    for part, us in measured["board_us"].items():
        assert us > 0, part
    assert measured["ints_localized_per_render"] > 0
    assert measured["render_diff_us_one_after_another"] > 0
