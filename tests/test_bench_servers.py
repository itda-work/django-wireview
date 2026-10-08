"""The ASGI server comparison (#191): what the documents show is what the measurements say.

docs/PERFORMANCE.md shows the charts and tables ``bench/servers_chart.py`` draws from the
results it names, and docs/DEPLOYMENT.md advises against Granian on the strength of the
shutdown probe. A new measurement, a hand-edited number or a chart drawn from older files
fails here until all of them agree again. CI does not run the benchmark (it needs minutes
and, for Windows, a machine over SSH).
"""

import json
import re
from pathlib import Path

import pytest

from bench import servers_chart as chart

ROOT = Path(__file__).resolve().parent.parent
PERFORMANCE = ROOT / "docs" / "PERFORMANCE.md"
DEPLOYMENT = ROOT / "docs" / "DEPLOYMENT.md"
#: A measured-looking number: a value and its unit
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?(?: ms| KB|/s|배)(?![A-Za-z])")

pytestmark = pytest.mark.unit


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n{heading}\n")
    end = text.find("\n### ", start + len(heading) + 2)
    return text[start:end]


def _key(name: str) -> str:
    """What a result measured: the part after ``-servers-`` (machine label and setting)."""
    return name.split("-servers-", 1)[1]


def test_the_charts_are_drawn_from_the_named_results():
    for name, svg in chart.charts().items():
        path = chart.IMAGES / name
        assert path.read_text(encoding="utf-8") == svg, f"{name} is stale: run make bench-servers-charts"


def test_each_named_result_is_the_newest_of_its_kind():
    named = [name for machine in chart.MACHINES for name in machine.files] + [chart.SHUTDOWN]
    for name in named:
        same = [p for p in chart.RESULTS.glob("*-servers-*.json") if _key(p.name) == _key(name)]
        newest = max(same, key=lambda p: json.loads(p.read_text())["environment"]["date"])
        assert newest.name == name, f"servers_chart names {name}, the newest such measurement is {newest.name}"


def test_the_performance_guide_has_every_table_and_chart():
    guide = PERFORMANCE.read_text(encoding="utf-8")
    for machine in chart.MACHINES:
        assert chart.table(machine) in guide, (
            f"docs/PERFORMANCE.md's {machine.label} table differs: paste python -m bench.servers_chart --table"
        )
    assert chart.shutdown_table() in guide, "docs/PERFORMANCE.md's SIGTERM table differs"
    for name in chart.charts():
        assert f"](images/{name})" in guide, f"docs/PERFORMANCE.md does not show {name}"


def test_the_performance_guide_quotes_only_the_results():
    """The prose around the tables reads its numbers off them, so a new result shows a stale sentence."""
    section = _section(PERFORMANCE.read_text(encoding="utf-8"), "#### ASGI 서버 비교")
    tables = "\n".join([chart.table(m) for m in chart.MACHINES] + [chart.shutdown_table()])
    known = set(NUMBER.findall(tables)) | set(chart.facts().values())
    assert set(NUMBER.findall(section)) <= known, set(NUMBER.findall(section)) - known
    for key in ("macos_events_ratio", "win10_events_ratio", "macos_memory_ratio", "win10_memory_ratio"):
        assert chart.facts()[key] in section, f"the section does not say {key} = {chart.facts()[key]}"


def test_the_deployment_guide_says_what_the_shutdown_probe_measured():
    """DEPLOYMENT.md advises against Granian because leaving() does not finish on SIGTERM.
    If a new Granian lets it finish, that advice has to be rewritten, not left standing."""
    servers = chart.load(chart.SHUTDOWN)["servers"]
    granian, uvicorn = servers["granian"], servers["uvicorn-nodeflate"]
    assert granian["leaving_finished"] < granian["connections"]
    assert uvicorn["leaving_finished"] == uvicorn["connections"]
    assert "1012" in uvicorn["close_codes"]
    advice = _section(DEPLOYMENT.read_text(encoding="utf-8"), "### 권하지 않는 것: Granian")
    assert "leaving()" in advice


def test_a_server_that_dies_is_a_row_of_its_own():
    result = {
        "settings": {"items": [5, 50], "connections": 600, "layer": "memory", "processes": 1},
        "servers": {
            "daphne": {
                "rounds": [
                    {
                        "items_5": {"error": "RuntimeError: joined 500 of 600 connections, then: ..."},
                        "items_50": {m: 1.0 for m in chart.METRICS},
                    }
                ]
            }
        },
    }
    cells = chart.cells(result)
    assert cells[(5, "daphne")].rounds == 0
    assert chart._death(cells[(5, "daphne")].failed[0]) == "500/600연결에서 죽음"
    assert cells[(50, "daphne")].rounds == 1
    assert not cells[(50, "daphne")].failed
