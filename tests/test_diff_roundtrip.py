"""Delta round-trip: server diffs applied by the real client code rebuild every render.

Each sequence renders one template through the marker engine while a seeded
random walk mutates the context (inserting, removing, moving and editing loop
items, toggling conditionals inside and outside items, emptying and refilling
lists, pointing items at LiveComponents and changing those children). Every
render's diff goes through JSON to ``tests/js/roundtrip.mjs``, which applies
it with ``rendered.mjs`` exactly as the browser does: children first, then the
parent built with a resolver for component references. The HTML the client
ends up with must equal the HTML the server rendered, after every step, not
just the last. Some steps diff against a snapshot restored through
``to_dict()``/``from_dict()``, the form a session takes outside the process,
and some start over with a full render, as after a reconnect.

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
from django.utils.safestring import mark_safe

from wireview.core.rendered import Rendered, component_ref_placeholder, strip_markers
from wireview.template_engine import TemplateMarker

pytestmark = pytest.mark.integration

DRIVER = Path(__file__).parent / "js" / "roundtrip.mjs"

TEMPLATE_SOURCE = (
    "<section><h1>{{ title }}</h1>"
    "{% if banner %}<p class=banner>{{ banner }} · {{ title }}</p>{% endif %}"
    # A block whose partial update carries a comprehension update.
    "{% if show_globals %}<div>{{ banner }}{% for g in globals %}<i>{{ g }}</i>{% endfor %}</div>{% endif %}"
    "<ul>"
    "{% for item in items %}"
    '<li class="{% if item.done %}done{% endif %}">{{ item.name }} × {{ item.qty }}'
    "{% if item.tags %}<span>{% for tag in item.tags %}<b>{{ tag }}</b>{% endfor %}</span>{% endif %}"
    # A LiveComponent in a live render leaves only a reference in the parent.
    "{{ item.child }}"
    "</li>"
    "{% endfor %}"
    "</ul>"
    "<ol>{% for n in notes %}<li>{{ n }}</li>{% empty %}<li>none</li>{% endfor %}</ol>"
    "</section>"
)

SEQUENCES = 40
STEPS = 30
CHILDREN = ("child-1", "child-2", "child-3")


def _item(rng: random.Random) -> dict[str, t.Any]:
    # A small value space on purpose: duplicate items and values that repeat
    # across positions are where a matching diff could pair the wrong items.
    return {
        "name": rng.choice("abcde"),
        "qty": rng.randint(0, 3),
        "done": rng.random() < 0.3,
        "tags": [rng.choice("xyz") for _ in range(rng.choice([0, 0, 1, 2]))],
        "child": rng.choice([None, None, *CHILDREN]),
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
            "headline",
            "notes",
            "globals",
            "show_globals",
            "child_html",
            "child_assign",
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
    elif op == "headline":
        # One event that changes two slots of the same block.
        state["title"] = rng.choice(["T", "U", "V"])
        state["banner"] = rng.choice(["hello", "bye", "hi"])
    elif op == "notes":
        state["notes"] = [rng.choice("pq") for _ in range(rng.randint(0, 3))]
    elif op == "globals":
        state["globals"] = [rng.choice("gh") for _ in range(rng.randint(0, 3))]
    elif op == "show_globals":
        state["show_globals"] = not state["show_globals"]
    elif op == "child_html":
        # The child re-renders on its own; the parent's render does not change.
        state["children"][rng.choice(CHILDREN)] = f"<em>{rng.choice('mno')}</em>"
    elif op == "child_assign" and items:
        rng.choice(items)["child"] = rng.choice([None, *CHILDREN])


def _context(state: dict[str, t.Any]) -> dict[str, t.Any]:
    """The template context: each item's child becomes the reference a live render leaves."""
    context = copy.deepcopy(state)
    for item in context["items"]:
        item["child"] = mark_safe(component_ref_placeholder(item["child"])) if item["child"] else ""
    return context


def _resolve(html: str, children: dict[str, str]) -> str:
    """What the browser shows: every reference replaced by that child's current HTML."""
    for child_id, child_html in children.items():
        html = html.replace(component_ref_placeholder(child_id), child_html)
    return html


def _sequence(seed: int) -> tuple[list[dict[str, t.Any]], list[str]]:
    """Steps (diff plus the children's HTML) and the HTML the browser should show, one per render."""
    rng = random.Random(seed)
    marker = TemplateMarker()
    template = Template(TEMPLATE_SOURCE)
    state: dict[str, t.Any] = {
        "title": "T",
        "banner": "",
        "items": [_item(rng) for _ in range(rng.randint(0, 6))],
        "notes": [],
        "globals": [],
        "show_globals": False,
        "children": {child_id: f"<em>{child_id}</em>" for child_id in CHILDREN},
    }
    steps: list[dict[str, t.Any]] = []
    expected: list[str] = []
    previous: Rendered | None = None
    for step in range(STEPS):
        # Several mutations per render: one event often changes more than one thing,
        # and a block partial only carries two slots when both moved at once.
        for _ in range(rng.choice([1, 1, 2, 3]) if step else 0):
            _mutate(state, rng)
        marked = marker.render_marked(template, _context(state))
        rendered = Rendered.from_marked_html(marked)
        html = strip_markers(marked)
        assert rendered.to_html() == html, f"seed {seed} step {step}: parse lost content"
        if previous is not None and rng.random() < 0.2:
            restored = Rendered.from_dict(json.loads(json.dumps(previous.to_dict())))
            # A lossy restore still round-trips (the next diff just resends more), so
            # check the restore itself: it must be the same structure.
            assert restored == previous, f"seed {seed} step {step}: snapshot restore changed the render"
            previous = restored
        elif rng.random() < 0.05:
            previous = None  # a reconnect: the server has no snapshot and sends a full render
        diff = rendered.get_diff(previous)
        # Through JSON, as the consumer sends it: tuples, non-str keys and the like
        # must not survive into what the client sees.
        payload = json.loads(json.dumps(diff.to_payload())) if diff is not None else None
        steps.append({"diff": payload, "children": dict(state["children"])})
        expected.append(_resolve(html, state["children"]))
        previous = rendered
    return steps, expected


def _replay(sequences: list[list[dict[str, t.Any]]]) -> list[list[str]]:
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

    replayed = _replay([steps for steps, _expected in runs])

    for seed, (_steps, expected), got in zip(seeds, runs, replayed, strict=True):
        for step, (want, have) in enumerate(zip(expected, got, strict=True)):
            assert have == want, f"seed {seed} step {step}: client HTML diverged from the render"


def _kinds(value: t.Any, inside_block: bool = False) -> set[str]:
    """The diff forms present anywhere in one partial value."""
    if not isinstance(value, dict):
        return {"string"}
    if isinstance(value.get("c"), str):
        return {"ref"}
    found: set[str] = set()
    nested: list[t.Any] = []
    if "s" in value:
        found.add("comprehension")
        nested = [v for item in value["d"] for v in item]
    elif "r" in value:
        found.add("block")
        nested = list(value["d"])
    elif "u" in value:
        found.add("block>u" if inside_block else "u")
        nested = [v for item in value["u"].values() for v in item]
    elif "p" in value:
        found.add("p2" if len(value["p"]) > 1 else "p")
        found.update(k for v in value["p"].values() for k in _kinds(v, inside_block=True))
    for v in nested:
        found.update(_kinds(v))
    return found


def test_the_walk_exercises_every_diff_form():
    """Guard the test itself: a walk that never produced a form proves nothing about it."""
    kinds: set[str] = set()
    for seed in range(SEQUENCES):
        steps, expected = _sequence(seed)
        for before, after, step in zip(expected, expected[1:], steps[1:]):
            diff = step["diff"]
            if diff is None:
                kinds.add("none" if before == after else "children only")
            elif "s" in diff:
                kinds.add("full")
            else:
                for value in diff.values():
                    kinds.update(_kinds(value))

    wanted = {"none", "children only", "full", "string", "ref", "comprehension", "block", "u", "p", "p2", "block>u"}
    assert wanted <= kinds, wanted - kinds


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

    def steps(*diffs):
        return [{"diff": diff, "children": {}} for diff in diffs]

    (good, bad) = _replay([steps(full.to_payload(), partial.to_payload()), steps(full.to_payload(), broken)])

    assert good[-1] == after.to_html()
    assert bad[-1] != after.to_html()
