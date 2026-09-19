"""Content-matched comprehension diffs (GAP-030, #69, docs/design/keyed-comprehension.md).

A client that speaks protocol version 2 may receive ``{"k": [segment, ...]}``
for a loop whose items moved: ``[start, length]`` is a run of the previous
items and ``{"d": [...]}`` a new one. Items are matched by their dynamics, so
templates need no keys. The positional form stays whenever it is enough, and
a client that names no version never sees the new form at all.

``tests/test_diff_roundtrip.py`` checks that the client rebuilds every render
from these diffs; this file pins the shapes and the rules that choose them.
"""

import json
import random
import re
from pathlib import Path

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.template import Template

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import (
    MOVES_SINCE,
    PROTOCOL_VERSION,
    ComponentRef,
    Comprehension,
    Rendered,
    protocol_version,
)
from wireview.core.state import sign_state

ITEM_STATIC = ["<li>", "</li>"]


def items(*names: str) -> Comprehension:
    return Comprehension(static=ITEM_STATIC, dynamics=[[n] for n in names])


def diff(new: Comprehension, old: Comprehension, vsn: int = PROTOCOL_VERSION):
    return new.diff(old, moves=vsn >= MOVES_SINCE)


def letters(n: int) -> list[str]:
    return [f"i{k}" for k in range(n)]


def apply(old: Comprehension, payload: dict) -> list[list]:
    """What a client holds after a ``k`` payload: runs of the old items and new ones."""
    result: list[list] = []
    for segment in payload["k"]:
        if isinstance(segment, list):
            start, length = segment
            result.extend(old.dynamics[start : start + length])
        else:
            result.append(segment["d"])
    return result


@pytest.mark.unit
class TestShapes:
    def test_an_insert_at_the_front_sends_only_the_new_item(self):
        old = items(*letters(50))

        payload = diff(items("new", *letters(50)), old)

        assert payload == {"k": [{"d": ["new"]}, [0, 50]]}

    def test_an_insert_in_the_middle_keeps_both_halves_as_runs(self):
        names = letters(50)

        payload = diff(items(*names[:25], "new", *names[25:]), items(*names))

        assert payload == {"k": [[0, 25], {"d": ["new"]}, [25, 25]]}

    def test_removing_the_first_item_sends_one_run(self):
        names = letters(50)

        assert diff(items(*names[1:]), items(*names)) == {"k": [[1, 49]]}

    def test_moving_the_last_item_to_the_front(self):
        names = letters(50)

        assert diff(items(names[-1], *names[:-1]), items(*names)) == {"k": [[49, 1], [0, 49]]}

    def test_a_moved_item_that_also_changed_is_sent_whole(self):
        names = letters(50)

        payload = diff(items("i49 edited", *names[:-1]), items(*names))

        assert payload == {"k": [{"d": ["i49 edited"]}, [0, 49]]}

    def test_every_segment_rebuilds_the_new_list(self):
        names = letters(20)
        new = items(*reversed(names))

        payload = diff(new, items(*names))

        assert apply(items(*names), payload) == new.dynamics

    def test_equal_items_are_matched_earliest_first_and_no_position_is_used_twice(self):
        old = items("a", "a", "b", "a")
        new = items("b", "a", "a", "a", "a")

        payload = diff(new, old)

        assert payload is not None
        used = [p for s in payload.get("k", []) if isinstance(s, list) for p in range(s[0], s[0] + s[1])]
        assert len(used) == len(set(used)), payload
        if "k" in payload:
            assert apply(old, payload) == new.dynamics


@pytest.mark.unit
class TestTheSmallerFormWins:
    """Positional is kept where it suffices, byte for byte; otherwise the fewer bytes win."""

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            pytest.param(letters(50), [*letters(49), "edited"], id="one item edited"),
            pytest.param(letters(50), letters(51), id="append"),
            pytest.param(letters(50), letters(40), id="truncate"),
            pytest.param(letters(50), letters(50), id="no change"),
            pytest.param([], letters(3), id="filled from empty"),
            pytest.param(letters(3), [], id="emptied"),
        ],
    )
    def test_matches_what_an_old_client_gets(self, before, after):
        old, new = items(*before), items(*after)

        assert diff(new, old, vsn=PROTOCOL_VERSION) == diff(new, old, vsn=0)

    def test_swapping_two_middle_items_stays_positional_when_that_is_smaller(self):
        """The review's counterexample to choosing by item count: here the runs cost more."""
        names = ["a"] * 500 + ["b", "c"] + ["a"] * 500
        swapped = ["a"] * 500 + ["c", "b"] + ["a"] * 500

        payload = diff(items(*swapped), items(*names))

        assert payload == {"u": {"500": ["c"], "501": ["b"]}, "n": 1002}

    @pytest.mark.parametrize("seed", range(200))
    def test_the_chosen_form_is_never_larger_than_the_positional_one(self, seed):
        rng = random.Random(seed)
        old = [rng.choice("abcdefgh") * rng.randint(1, 6) for _ in range(rng.randint(1, 30))]
        new = list(old)
        for _ in range(rng.randint(1, 4)):
            op = rng.choice(["insert", "remove", "move", "edit", "shuffle"])
            if op == "insert":
                new.insert(rng.randint(0, len(new)), rng.choice("xyz"))
            elif op == "remove" and new:
                new.pop(rng.randrange(len(new)))
            elif op == "move" and new:
                new.insert(rng.randint(0, len(new) - 1), new.pop(rng.randrange(len(new))))
            elif op == "edit" and new:
                new[rng.randrange(len(new))] = rng.choice("xyz")
            elif op == "shuffle":
                rng.shuffle(new)
        if not new:
            return

        chosen = diff(items(*new), items(*old))
        positional = diff(items(*new), items(*old), vsn=0)

        assert len(json.dumps(chosen)) <= len(json.dumps(positional))
        if chosen is not None and "k" in chosen:
            assert apply(items(*old), chosen) == items(*new).dynamics

    @pytest.mark.parametrize(
        "positional",
        [
            {"u": {}, "n": 0},
            {"u": {"0": ["a"]}, "n": 1},
            {"u": {"500": ["c"], "501": ["b"]}, "n": 1002},
            {"u": {"3": ["한글", {"r": ["<b>", "</b>"], "d": ["x"]}], "12": [{"c": "child-1"}]}, "n": 13},
        ],
    )
    def test_the_size_check_counts_what_json_dumps_writes(self, positional):
        from wireview.core.rendered import _longer_than

        exact = len(json.dumps(positional))

        assert not _longer_than(positional, exact)
        assert _longer_than(positional, exact - 1)

    def test_the_old_protocol_gets_the_positional_form_for_a_move(self):
        names = letters(5)

        payload = diff(items(names[-1], *names[:-1]), items(*names), vsn=0)

        assert payload is not None and "k" not in payload
        assert payload["n"] == 5


@pytest.mark.unit
class TestNesting:
    def test_items_holding_blocks_lists_and_references_are_matched_whole(self):
        def item(n: int) -> list:
            return [
                Rendered(static=["<b>", "</b>"], dynamic=[f"b{n}"]),
                Comprehension(static=["<i>", "</i>"], dynamics=[[f"x{n}"], [f"y{n}"]]),
                ComponentRef(f"child-{n}"),
            ]

        static = ["<li>", "", "", "</li>"]
        old = Comprehension(static=static, dynamics=[item(n) for n in range(10)])
        new = Comprehension(static=static, dynamics=[item(9), *(item(n) for n in range(9))])

        assert new.diff(old, moves=True) == {"k": [[9, 1], [0, 9]]}

    def test_a_block_that_differs_only_in_kind_is_not_a_match(self):
        """A block and a list with the same parts render differently: they must not pair."""
        block = Rendered(static=["<b>", "</b>"], dynamic=["x"])
        loop = Comprehension(static=["<b>", "</b>"], dynamics=[["x"]])
        old = Comprehension(static=["<li>", "</li>"], dynamics=[[block], [loop], [loop], [loop]])
        new = Comprehension(static=["<li>", "</li>"], dynamics=[[loop], [loop], [loop], [loop]])

        payload = new.diff(old, moves=True)

        assert payload is not None
        if "k" in payload:
            assert apply(old, payload) == new.dynamics

    def test_a_list_inside_a_block_is_matched_under_a_block_partial(self):
        names = letters(30)

        def page(loop_names: list[str]) -> Rendered:
            block = Rendered(static=["<div>", "", "</div>"], dynamic=["title", items(*loop_names)])
            return Rendered(static=["<main>", "</main>"], dynamic=[block])

        result = page([names[-1], *names[:-1]]).get_diff(page(names), vsn=PROTOCOL_VERSION)

        assert result is not None
        assert result.to_payload() == {"0": {"p": {"1": {"k": [[29, 1], [0, 29]]}}}}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (b"", 0),
        (b"vsn=2", 2),
        ("vsn=2", 2),
        (b"vsn=1", 1),
        (b"other=1&vsn=2", 2),
        (b"vsn=", 0),
        (b"vsn=two", 0),
        (b"vsn=-1", 0),
        (b"vsn=2.0", 0),
        (b"vsn=%EF%BC%92", 0),  # a full-width digit: isdigit() says yes, int() would agree
        (b"vsn=2&vsn=2", 0),
    ],
)
def test_the_protocol_version_is_read_conservatively(query, expected):
    assert protocol_version(query) == expected


@pytest.mark.unit
def test_the_client_and_server_speak_the_same_version():
    source = (Path(__file__).parent.parent / "wireview/static/wireview/rendered.mjs").read_text()
    match = re.search(r"export const PROTOCOL_VERSION = (\d+);", source)

    assert match, "rendered.mjs must export PROTOCOL_VERSION"
    assert int(match.group(1)) == PROTOCOL_VERSION


# --- through the consumer: the version comes from the socket URL ---------------

_template: Template | None = None


class MovesProbe(Component):
    _template_name = "moves_probe.html"

    names: list[str] = []

    async def rotate(self):
        self.names.insert(0, self.names.pop())

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template(
                "{% load wireview %}<ul {% tag_header %}>{% for n in names %}<li>{{ n }}</li>{% endfor %}</ul>"
            )
        return _template


def _has_moves(value) -> bool:
    if isinstance(value, dict):
        return "k" in value or any(_has_moves(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_moves(v) for v in value)
    return False


async def _rotate_over_socket(path: str) -> dict:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), path)
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    try:
        probe = MovesProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="moves-1", names=letters(20))
        await communicator.send_json_to(
            {"command": "join", "payload": {"name": "MovesProbe", "state": sign_state(probe), "children": {}}}
        )
        first = await communicator.receive_json_from(timeout=5)
        assert first["command"] == "render" and "s" in first["payload"]["diff"]
        await communicator.send_json_to(
            {
                "command": "user_event",
                "payload": {"id": "moves-1", "command": "rotate", "implicit_args": {}, "explicit_args": {}},
            }
        )
        for _ in range(5):
            message = await communicator.receive_json_from(timeout=5)
            if message["command"] == "render":
                return message["payload"]["diff"]
        raise AssertionError("no render after the event")
    finally:
        await communicator.disconnect()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_client_that_names_the_version_gets_moves():
    diff = await _rotate_over_socket(f"/__wireview__?vsn={PROTOCOL_VERSION}")

    assert _has_moves(diff), json.dumps(diff)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/__wireview__", "/__wireview__?vsn=1", "/__wireview__?vsn=nonsense"])
async def test_a_client_that_does_not_never_sees_them(path):
    diff = await _rotate_over_socket(path)

    assert not _has_moves(diff), json.dumps(diff)
