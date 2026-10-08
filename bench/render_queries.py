"""What render-part SQL attribution costs a live render (#182, docs/design/render-part-queries.md §4).

One mode per process, so a mode's imports and its wrapper are its own:

- ``base``: the tree under ``--tree`` (the commit before the feature, ``git archive`` of it) --
  no boundary, no wrapper
- ``off``: this tree, ``DEBUG_RENDER_QUERIES`` off, the wrapper installed (``wireview.testing``
  puts it on every connection) and reading its ``ContextVar`` per statement
- ``on``: this tree, the setting on. The ``wireview.queries`` logger is above ``WARNING`` so the
  collecting and the repeat check are measured, not the log's I/O

Each scenario is ``mount()`` then ``render_diff()``: 30 warm-up renders, then 15 rounds of 40,
and the value is the median of the rounds' per-render time. Every template has a 100-row loop.

    uv run python -m bench.render_queries --mode on
    uv run python -m bench.render_queries --compare BASE_TREE   # base, off, on alternately, three times each
    uv run python -m bench.render_queries --paired --times 5    # off and on in turns within each process

Results go to stdout as JSON (``--compare`` writes ``bench/results/render-queries-<sha>.json``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATES = {
    "bq/none.html": ("{% load wireview %}<div {% tag_header %}>{% for i in rows %}<i>{{ i }}</i>{% endfor %}</div>"),
    "bq/template.html": (
        "{% load wireview %}<div {% tag_header %}>{% for i in rows %}<i>{{ i }}</i>{% endfor %}"
        "{% for c in choices %}<b>{{ c.question.text }}</b>{% endfor %}</div>"
    ),
    "bq/sync.html": (
        "{% load wireview %}<div {% tag_header %}>{% for i in rows %}<i>{{ i }}</i>{% endfor %}{{ total }}</div>"
    ),
    "bq/async.html": (
        "{% load wireview %}<div {% tag_header %}>{% for i in rows %}<i>{{ i }}</i>{% endfor %}{{ first_title }}</div>"
    ),
}
SCENARIOS = {
    "no queries": "BqNone",
    "template SQL x7": "BqTemplate",
    "sync property SQL x1": "BqSync",
    "async property SQL x1": "BqAsync",
}


def _setup(mode: str, tree: str | None, database: str) -> None:
    if mode == "base":
        assert tree, "--tree names the base checkout"
        sys.path.insert(0, tree)
    sys.path.insert(1 if mode == "base" else 0, str(ROOT / "tests"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "testproj.settings"
    from django.conf import settings

    settings.DEBUG = False
    settings.DATABASES["default"]["NAME"] = database
    settings.DATABASES["default"]["ATOMIC_REQUESTS"] = False
    settings.WIREVIEW = {
        **getattr(settings, "WIREVIEW", {}),
        "DEBUG_SYNC_TRANSITIONS": False,
        "DEBUG_RENDER_QUERIES": mode == "on",
    }
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [
                    ("django.template.loaders.cached.Loader", [("django.template.loaders.locmem.Loader", TEMPLATES)])
                ]
            },
        }
    ]
    import logging

    import django

    django.setup()
    logging.getLogger("wireview.queries").setLevel(logging.ERROR)
    logging.getLogger("wireview").setLevel(logging.ERROR)


def _components() -> dict[str, type]:
    from examples.quiz.models import Choice, Quiz
    from wireview import Component

    rows = list(range(100))

    class _Rows:
        @property
        def rows(self) -> list[int]:
            return rows

    class BqNone(_Rows, Component):
        class Meta:
            template_name = "bq/none.html"

    class BqTemplate(_Rows, Component):
        class Meta:
            template_name = "bq/template.html"

        @property
        def choices(self):
            return Choice.objects.all()

    class BqSync(_Rows, Component):
        class Meta:
            template_name = "bq/sync.html"

        @property
        def total(self) -> int:
            return Choice.objects.count()

    class BqAsync(_Rows, Component):
        class Meta:
            template_name = "bq/async.html"

        @property
        async def first_title(self) -> str:
            quiz = await Quiz.objects.afirst()
            return quiz.title if quiz else ""

    return {cls.__name__: cls for cls in (BqNone, BqTemplate, BqSync, BqAsync)}


def _seed() -> None:
    from django.core.management import call_command

    from examples.quiz.models import Choice, Question, Quiz

    call_command("migrate", verbosity=0)
    quiz = Quiz.objects.create(title="Q")
    for n in range(6):
        Choice.objects.create(question=Question.objects.create(quiz=quiz, text=f"q{n}"), text=f"c{n}")


async def _measure(cls: type, warmup: int, rounds: int, per_round: int) -> float:
    from wireview.testing import mount

    view = await mount(cls, id="b")
    for _ in range(warmup):
        await view.render_diff()
    wall, cpu = [], []
    for _ in range(rounds):
        start, start_cpu = time.perf_counter(), time.process_time()
        for _ in range(per_round):
            await view.render_diff()
        wall.append((time.perf_counter() - start) / per_round)
        cpu.append((time.process_time() - start_cpu) / per_round)
    return {"wall": statistics.median(wall) * 1e6, "cpu": statistics.median(cpu) * 1e6}


async def _measure_paired(cls: type, warmup: int, rounds: int, per_round: int) -> dict[str, float]:
    """Off and on in turns, round by round, on one instance: a load that comes and goes hits both."""
    from django.conf import settings

    from wireview.testing import mount

    off = {**settings.WIREVIEW, "DEBUG_RENDER_QUERIES": False}
    on = {**settings.WIREVIEW, "DEBUG_RENDER_QUERIES": True}
    view = await mount(cls, id="b")
    for _ in range(warmup):
        await view.render_diff()
    timings: dict[str, list[float]] = {"off": [], "on": []}
    for _ in range(rounds):
        for name, values in (("off", off), ("on", on)):
            settings.WIREVIEW = values
            start = time.perf_counter()
            for _ in range(per_round):
                await view.render_diff()
            timings[name].append((time.perf_counter() - start) / per_round)
    differences = [b - a for a, b in zip(timings["off"], timings["on"], strict=True)]
    return {
        "off": statistics.median(timings["off"]) * 1e6,
        "on": statistics.median(timings["on"]) * 1e6,
        "on - off": statistics.median(differences) * 1e6,
    }


def run(mode: str, tree: str | None) -> dict[str, dict[str, float]]:
    with tempfile.TemporaryDirectory() as scratch:
        _setup(mode, tree, str(Path(scratch) / "bench.sqlite3"))
        _seed()
        components = _components()
        if mode != "base":
            import wireview.testing  # noqa: F401 -- puts the wrapper on, as in a test process
            from wireview.debug import render_queries

            assert render_queries.installed(), "wireview.testing puts the wrapper on"
            if mode != "paired":
                assert render_queries.enabled() is (mode == "on")

        measure = _measure_paired if mode == "paired" else _measure

        async def all_scenarios() -> dict[str, dict[str, float]]:
            return {name: await measure(components[cls], 30, 15, 40) for name, cls in SCENARIOS.items()}

        return asyncio.run(all_scenarios())


def paired(times: int) -> dict[str, object]:
    runs = []
    for _ in range(times):
        done = subprocess.run(
            [sys.executable, "-m", "bench.render_queries", "--mode", "paired"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        )
        runs.append(json.loads(done.stdout))
    summary = {
        scenario: {
            key: [round(min(r[scenario][key] for r in runs)), round(max(r[scenario][key] for r in runs))]
            for key in ("off", "on", "on - off")
        }
        for scenario in SCENARIOS
    }
    return {
        "unit": "µs per render, wall clock. Each run: off and on in turns, 15 rounds x 40 each; 'on - off' is "
        "the median of the rounds' differences. The summary is the range over the runs",
        "runs": runs,
        "summary": summary,
    }


def compare(tree: str, times: int) -> dict[str, object]:
    runs: dict[str, list[dict[str, dict[str, float]]]] = {"base": [], "off": [], "on": []}
    for _ in range(times):
        for mode in runs:
            done = subprocess.run(
                [sys.executable, "-m", "bench.render_queries", "--mode", mode, "--tree", tree],
                capture_output=True,
                text=True,
                cwd=ROOT,
                check=True,
            )
            runs[mode].append(json.loads(done.stdout))
    summary: dict[str, dict[str, dict[str, object]]] = {}
    for clock in ("wall", "cpu"):
        for scenario in SCENARIOS:
            values = {mode: [r[scenario][clock] for r in results] for mode, results in runs.items()}
            summary.setdefault(clock, {})[scenario] = {
                **{mode: round(statistics.median(v)) for mode, v in values.items()},
                **{f"{mode} range": [round(min(v)), round(max(v))] for mode, v in values.items()},
                "on - base, per pair": [
                    round(on - base) for base, on in zip(values["base"], values["on"], strict=True)
                ],
                "off - base, per pair": [
                    round(off - base) for base, off in zip(values["base"], values["off"], strict=True)
                ],
            }
    return {
        "unit": "µs per render: each run the median of 15 rounds x 40, the summary the median of the runs",
        "clocks": {"wall": "time.perf_counter", "cpu": "time.process_time (every thread of the process)"},
        "runs": runs,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["base", "off", "on", "paired"])
    parser.add_argument("--tree", help="the base tree (a git archive of the commit before the feature)")
    parser.add_argument("--compare", metavar="BASE_TREE", help="run base, off and on alternately")
    parser.add_argument("--paired", action="store_true", help="off and on in turns in one process, --times runs")
    parser.add_argument("--times", type=int, default=3)
    parser.add_argument("--out", help="where --compare writes its JSON")
    args = parser.parse_args()
    if args.compare or args.paired:
        result = compare(args.compare, args.times) if args.compare else paired(args.times)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(text + "\n")
        print(text)
    else:
        print(json.dumps(run(args.mode, args.tree)))


if __name__ == "__main__":
    main()
