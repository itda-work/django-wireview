"""The FastAPI comparison (#174): what the documents show is what the measurement says.

README and docs/PERFORMANCE.md quote the result ``bench/compare_fastapi/chart.py`` names,
through charts it draws and numbers it formats. A new measurement, a hand-edited number
or a chart drawn from an older file fails here until all of them agree again.

CI does not run the benchmark itself (it needs node, a browser and a few minutes), so the
parts of it that can rot without one -- the wireview side's component, the timing wrapper,
the line counter -- are run here too.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from bench.compare_fastapi import chart, loc
from bench.compare_fastapi.store import FEED_SIZE, Store
from bench.compare_fastapi.timing import Timed, kind

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PERFORMANCE = ROOT / "docs" / "PERFORMANCE.md"
RAW = "https://raw.githubusercontent.com/itda-work/django-wireview/main/"
#: A measured-looking number: a value and its unit
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)? (?:ms|KB|B)(?![A-Za-z])")
#: Numbers the comparison's prose may quote that are not measurements: a frame at 60 Hz, the
#: process switch the first-load setup avoids, the step Chromium rounds FCP to.
NOT_MEASURED = {"16.7 ms", "35 ms", "4 ms"}


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n{heading}\n")
    end = text.find("\n## ", start + len(heading) + 2)
    return text[start : end if end != -1 else None]


@pytest.fixture(scope="module")
def result():
    return chart.load()


# -- the documents ------------------------------------------------------------------------


@pytest.mark.unit
def test_the_charts_are_drawn_from_the_named_result(result):
    for name, svg in chart.charts(result).items():
        path = chart.IMAGES / f"bench-fastapi-{name}.svg"
        assert path.read_text(encoding="utf-8") == svg, f"{path.name} is stale: run make bench-fastapi-charts"


@pytest.mark.unit
def test_the_named_result_is_the_newest_measurement():
    newest = max(
        (ROOT / "bench" / "results").glob("*-fastapi.json"),
        key=lambda p: json.loads(p.read_text())["environment"]["date"],
    )
    assert chart.RESULT == newest, f"chart.RESULT names {chart.RESULT.name}, the newest measurement is {newest.name}"


@pytest.mark.unit
def test_the_feed_row_reads_the_stream_fan_out_the_broadcast_guides_quote(result):
    """The comparison's Broadcast numbers are the feed measurement's, at the board's connection count."""
    from bench.compare_fastapi import stream_fanout

    assert chart.FEED_RESULT == stream_fanout.RESULT
    assert chart.load(chart.FEED_RESULT)["method"]["connections"] == result["method"]["fanout_connections"]


@pytest.mark.unit
def test_the_result_counts_the_lines_the_implementations_have_now(result):
    """The line counts are part of the result; changing an implementation means measuring again."""
    assert result["loc"] == loc.count()


@pytest.mark.unit
def test_the_readme_quotes_the_result_and_nothing_else(result):
    numbers = _section(README.read_text(encoding="utf-8"), "## 숫자")
    facts = chart.facts(result)
    for key, value in facts.items():
        assert value in numbers, f"README's 숫자 section does not say {key} = {value}"
    # Every measured-looking number in the section is one of them
    quoted = set(NUMBER.findall(numbers))
    assert quoted <= set(facts.values()), f"README quotes numbers the result does not: {quoted - set(facts.values())}"


@pytest.mark.unit
def test_the_readme_and_the_performance_guide_show_every_chart(result):
    readme = _section(README.read_text(encoding="utf-8"), "## 숫자")
    guide = PERFORMANCE.read_text(encoding="utf-8")
    for name in chart.charts(result):
        file = f"docs/images/bench-fastapi-{name}.svg"
        # README links absolute: PyPI has no repository (hatch_build pins main to the tag, the site serves its own copy)
        assert f"]({RAW}{file})" in readme or name == "loc", f"README does not show {file}"
        assert f"](images/bench-fastapi-{name}.svg)" in guide, f"PERFORMANCE.md does not show {file}"


@pytest.mark.unit
def test_the_performance_guide_quotes_only_the_result(result):
    """The prose around the table reads its numbers off the table, so a new result shows a stale sentence."""
    guide = PERFORMANCE.read_text(encoding="utf-8")
    section = guide[guide.index("#### FastAPI와 비교") : guide.index("#### wireview 단독")]
    known = set(NUMBER.findall(chart.table(result))) | set(chart.facts(result).values()) | NOT_MEASURED
    assert set(NUMBER.findall(section)) <= known, set(NUMBER.findall(section)) - known


@pytest.mark.unit
def test_the_performance_guide_has_the_results_table(result):
    assert chart.table(result) in PERFORMANCE.read_text(encoding="utf-8"), (
        "docs/PERFORMANCE.md's table differs: paste python -m bench.compare_fastapi.chart --table"
    )


@pytest.mark.unit
def test_a_chart_reads_in_either_color_scheme_and_names_its_series(result):
    svg = chart.charts(result)["latency"]
    assert "@media (prefers-color-scheme:dark)" in svg
    assert '<rect class="bg"' in svg  # its own surface, whatever the page behind it
    for label in chart.SERIES_LABELS.values():
        assert f">{label}</text>" in svg
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", svg), "an image the site serves loads nothing else"


# -- the harness ------------------------------------------------------------------------


@pytest.mark.unit
def test_lines_of_code_skip_blank_lines_comments_and_docstrings(tmp_path):
    python = tmp_path / "a.py"
    python.write_text(
        '"""Module."""\n\n# comment\nimport os\n\n\ndef f():\n    """Doc\n    string."""\n    return os  # trailing\n'
    )
    assert loc.lines_of_code(python) == 3
    template = tmp_path / "a.html"
    template.write_text("{# note #}\n<p>\n  <!-- a\n  b -->\n  {{ x }}\n</p>\n\n")
    assert loc.lines_of_code(template) == 3
    script = tmp_path / "a.jsx"
    script.write_text("// note\nconst a = 1;\n/* block\n */\n<p>{/* jsx */}</p>\n")
    assert loc.lines_of_code(script) == 2


@pytest.mark.unit
def test_the_timing_wrapper_times_a_message_from_receive_to_the_next_send():
    async def app(scope, receive, send):
        await send({"type": "websocket.accept"})
        message = await receive()
        await asyncio.sleep(0.01)
        await send({"type": "websocket.send", "text": message["text"]})
        await send({"type": "websocket.send", "text": "unasked"})  # no message waits for this one

    timed = Timed(app)
    inbox = [{"type": "websocket.receive", "text": json.dumps({"type": "insert"})}]
    sent = []

    async def receive():
        return inbox.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(timed({"type": "websocket"}, receive, send))
    assert len(sent) == 3
    [(text, ms)] = timed.records
    assert kind(text) == "insert" and ms >= 10


@pytest.mark.unit
def test_the_timed_actions_are_named_for_both_protocols():
    assert kind('{"type": "increment"}') == "increment"
    assert kind('{"command": "user_event", "payload": {"id": "board", "command": "announce"}}') == "announce"
    assert kind('{"command": "join", "payload": {"name": "Board"}}') == "join"


@pytest.mark.unit
def test_the_feed_stays_at_its_size_newest_first():
    store = Store()
    item = store.insert()
    assert len(store.items) == FEED_SIZE and store.items[0] == item
    assert store.items[-1]["id"] == 1  # item 0 fell off the end


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_wireview_side_renders_the_feed_and_answers_a_click(monkeypatch):
    with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "bench.compare_fastapi.wv.board"]):
        from bench.compare_fastapi.wv.board import live
        from wireview import mount

        monkeypatch.setattr(live, "store", Store())
        board = await mount(live.Board, id="board")
        html = board.render()
        assert html.count("<li") == FEED_SIZE and 'id="count">0<' in html
        await board.call("insert")
        await board.call("increment")
        html = board.render()
        assert 'id="count">1<' in html and html.index("item 50") < html.index("item 49")
