"""Delta round-trip: server diffs applied by the real client code rebuild every render.

Each sequence renders one template through the marker engine while a seeded
random walk mutates the context (inserting, removing, moving and editing loop
items, toggling conditionals inside and outside items, emptying and refilling
lists). Every render's diff goes through JSON to ``tests/js/roundtrip.mjs``,
which applies it with ``rendered.mjs`` exactly as the browser does. The HTML
the client ends up with must equal the HTML the server rendered, after every
step, not just the last.

The tests elsewhere pin the payload *shape* for hand-picked cases. This one
pins the property that makes any shape correct, so a new diff form (GAP-030,
#69) only has to pass it rather than re-derive its own cases.
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import subprocess
import typing as t
from pathlib import Path

import pytest
from django.template import Template

from wireview.core.rendered import Rendered, strip_markers
from wireview.template_engine import TemplateMarker

pytestmark = pytest.mark.integration

DRIVER = Path(__file__).parent / "js" / "roundtrip.mjs"

TEMPLATE_SOURCE = (
    "<section><h1>{{ title }}</h1>"
    "{% if banner %}<p class=banner>{{ banner }} · {{ title }}</p>{% endif %}"
    "<ul>"
    "{% for item in items %}"
    '<li class="{% if item.done %}done{% endif %}">{{ item.name }} × {{ item.qty }}'
    "{% if item.tags %}<span>{% for tag in item.tags %}<b>{{ tag }}</b>{% endfor %}</span>{% endif %}"
    "</li>"
    "{% endfor %}"
    "</ul>"
    "<ol>{% for n in notes %}<li>{{ n }}</li>{% empty %}<li>none</li>{% endfor %}</ol>"
    "</section>"
)

SEQUENCES = 40
STEPS = 30


def _item(rng: random.Random) -> dict[str, t.Any]:
    # A small value space on purpose: duplicate items and values that repeat
    # across positions are where a matching diff could pair the wrong items.
    return {
        "name": rng.choice("abcde"),
        "qty": rng.randint(0, 3),
        "done": rng.random() < 0.3,
        "tags": [rng.choice("xyz") for _ in range(rng.choice([0, 0, 1, 2]))],
    }


def _mutate(state: dict[str, t.Any], rng: random.Random) -> None:
    items: list[dict[str, t.Any]] = state["items"]
    op = rng.choice(
        [
            "insert_front",
            "insert_middle",
            "append",
            "remove_first",
            "remove_random",
            "move_last_to_first",
            "move_random",
            "reverse",
            "shuffle",
            "duplicate",
            "edit",
            "toggle_done",
            "retag",
            "clear",
            "refill",
            "title",
            "banner",
            "notes",
            "nothing",
        ]
    )
    if op == "insert_front":
        items.insert(0, _item(rng))
    elif op == "insert_middle":
        items.insert(len(items) // 2, _item(rng))
    elif op == "append":
        items.append(_item(rng))
    elif op == "remove_first" and items:
        items.pop(0)
    elif op == "remove_random" and items:
        items.pop(rng.randrange(len(items)))
    elif op == "move_last_to_first" and items:
        items.insert(0, items.pop())
    elif op == "move_random" and items:
        items.insert(rng.randrange(len(items)), items.pop(rng.randrange(len(items))))
    elif op == "reverse":
        items.reverse()
    elif op == "shuffle":
        rng.shuffle(items)
    elif op == "duplicate" and items:
        items.insert(rng.randrange(len(items) + 1), copy.deepcopy(rng.choice(items)))
    elif op == "edit" and items:
        rng.choice(items)["name"] = rng.choice("abcdef")
    elif op == "toggle_done" and items:
        target = rng.choice(items)
        target["done"] = not target["done"]
    elif op == "retag" and items:
        rng.choice(items)["tags"] = [rng.choice("xyz") for _ in range(rng.randint(0, 3))]
    elif op == "clear":
        items.clear()
    elif op == "refill":
        items[:] = [_item(rng) for _ in range(rng.randint(1, 8))]
    elif op == "title":
        state["title"] = rng.choice(["T", "U", ""])
    elif op == "banner":
        state["banner"] = rng.choice(["", "hello", "bye"])
    elif op == "notes":
        state["notes"] = [rng.choice("pq") for _ in range(rng.randint(0, 3))]


def _sequence(seed: int) -> tuple[list[t.Any], list[str]]:
    """Diffs and the HTML the server rendered, one per step of a random walk."""
    rng = random.Random(seed)
    marker = TemplateMarker()
    template = Template(TEMPLATE_SOURCE)
    state: dict[str, t.Any] = {
        "title": "T",
        "banner": "",
        "items": [_item(rng) for _ in range(rng.randint(0, 6))],
        "notes": [],
    }
    diffs: list[t.Any] = []
    expected: list[str] = []
    previous: Rendered | None = None
    for step in range(STEPS):
        # Several mutations per render: one event often changes more than one thing,
        # and a block partial only carries two slots when both moved at once.
        for _ in range(rng.choice([1, 1, 2, 3]) if step else 0):
            _mutate(state, rng)
        marked = marker.render_marked(template, copy.deepcopy(state))
        rendered = Rendered.from_marked_html(marked)
        html = strip_markers(marked)
        assert rendered.to_html() == html, f"seed {seed} step {step}: parse lost content"
        diff = rendered.get_diff(previous)
        # Through JSON, as the consumer sends it: tuples, non-str keys and the like
        # must not survive into what the client sees.
        diffs.append(json.loads(json.dumps(diff.to_payload())) if diff is not None else None)
        expected.append(html)
        previous = rendered
    return diffs, expected


def _replay(sequences: list[list[t.Any]]) -> list[list[str]]:
    node = shutil.which("node")
    # Not a skip: this is the only check that server and client agree on the diff,
    # and node is already required to build the client bundle.
    assert node, "node is required: tests/js/roundtrip.mjs applies the diffs with rendered.mjs"
    result = subprocess.run(
        [node, str(DRIVER)],
        input=json.dumps(sequences),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_client_rebuilds_every_render_from_the_diffs():
    seeds = range(SEQUENCES)
    runs = [_sequence(seed) for seed in seeds]

    replayed = _replay([diffs for diffs, _expected in runs])

    for seed, (_diffs, expected), got in zip(seeds, runs, replayed, strict=True):
        for step, (want, have) in enumerate(zip(expected, got, strict=True)):
            assert have == want, f"seed {seed} step {step}: client HTML diverged from the render"


def test_the_walk_exercises_partial_diffs():
    """Guard the test itself: a walk that only ever sent full renders would prove nothing."""
    kinds: set[str] = set()
    for seed in range(SEQUENCES):
        diffs, _expected = _sequence(seed)
        for diff in diffs[1:]:
            if diff is None:
                kinds.add("none")
            elif "s" in diff:
                kinds.add("full")
            else:
                for value in diff.values():
                    if isinstance(value, dict):
                        kinds.update(key for key in ("u", "p", "r", "s") if key in value)
                        if len(value.get("p", ())) > 1:
                            kinds.add("p2")
                    else:
                        kinds.add("string")

    assert {"none", "u", "p", "p2", "r", "s", "string"} <= kinds, kinds


def test_the_driver_catches_a_client_that_drops_an_update():
    """The comparison can fail: a diff that loses one item update rebuilds the wrong HTML."""
    before = Rendered.from_marked_html(
        "<ul><!--$C0--><!--$I0--><li><!--$1-->a<!--/$1--></li><!--/$I0--><!--/$C0--></ul>"
    )
    after = Rendered.from_marked_html(
        "<ul><!--$C0--><!--$I0--><li><!--$1-->b<!--/$1--></li><!--/$I0--><!--/$C0--></ul>"
    )
    full = before.get_diff(None)
    partial = after.get_diff(before)
    assert full is not None and partial is not None
    broken = copy.deepcopy(partial.to_payload())  # to_payload() hands out its own dict
    broken["0"]["u"] = {}

    (good, bad) = _replay([[full.to_payload(), partial.to_payload()], [full.to_payload(), broken]])

    assert good[-1] == after.to_html()
    assert bad[-1] != after.to_html()
