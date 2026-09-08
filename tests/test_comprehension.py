"""Comprehensions: `{% for %}` loops as one dynamic slot with per-item dynamics."""

import json

import pytest

from wireview.core.rendered import Comprehension, Rendered, strip_markers

LOOP = "<ul><!--$C1-->{items}<!--/$C1--></ul>"


def item(idx: int, name: str, qty: str) -> str:
    return f"<!--$I1--><li><!--$2-->{name}<!--/$2--> x <!--$3-->{qty}<!--/$3--></li><!--/$I1-->"


def page(*items: tuple[str, str], title: str = "T") -> str:
    body = "".join(item(i, n, q) for i, (n, q) in enumerate(items))
    return f"<h1><!--$0-->{title}<!--/$0--></h1>" + LOOP.format(items=body)


@pytest.mark.unit
def test_loop_parses_into_one_comprehension_slot():
    rendered = Rendered.from_marked_html(page(("a", "1"), ("b", "2")))

    assert len(rendered.dynamic) == 2
    comp = rendered.dynamic[1]
    assert isinstance(comp, Comprehension)
    assert comp.static == ["<li>", " x ", "</li>"]
    assert comp.dynamics == [["a", "1"], ["b", "2"]]
    assert rendered.to_html() == strip_markers(page(("a", "1"), ("b", "2")))


@pytest.mark.unit
def test_changing_one_item_sends_only_that_item():
    before = Rendered.from_marked_html(page(("a", "1"), ("b", "2")))
    after = Rendered.from_marked_html(page(("a", "1"), ("b", "3")))

    diff = after.get_diff(before)

    assert diff is not None and not diff.is_full
    assert diff.to_payload() == {"1": {"u": {"1": ["b", "3"]}, "n": 2}}


@pytest.mark.unit
def test_appending_an_item_keeps_the_parent_fingerprint():
    before = Rendered.from_marked_html(page(("a", "1")))
    after = Rendered.from_marked_html(page(("a", "1"), ("b", "2")))

    assert after.fingerprint == before.fingerprint
    diff = after.get_diff(before)
    assert diff is not None and diff.to_payload() == {"1": {"u": {"1": ["b", "2"]}, "n": 2}}


@pytest.mark.unit
def test_removing_items_only_sends_the_new_length():
    before = Rendered.from_marked_html(page(("a", "1"), ("b", "2"), ("c", "3")))
    after = Rendered.from_marked_html(page(("a", "1")))

    diff = after.get_diff(before)

    assert diff is not None and diff.to_payload() == {"1": {"u": {}, "n": 1}}


@pytest.mark.unit
def test_empty_loop_then_items_sends_the_item_template_once():
    empty = Rendered.from_marked_html(page())
    assert empty.dynamic[1] == Comprehension()

    filled = Rendered.from_marked_html(page(("a", "1")))
    diff = filled.get_diff(empty)

    assert diff is not None and not diff.is_full
    assert diff.to_payload() == {"1": {"s": ["<li>", " x ", "</li>"], "d": [["a", "1"]]}}


@pytest.mark.unit
def test_full_render_payload_nests_the_comprehension():
    rendered = Rendered.from_marked_html(page(("a", "1")))

    payload = rendered.get_diff(None).to_payload()

    assert payload["s"] == ["<h1>", "</h1><ul>", "</ul>"]
    assert payload["d"] == ["T", {"s": ["<li>", " x ", "</li>"], "d": [["a", "1"]]}]
    assert json.dumps(payload)  # JSON-serializable


@pytest.mark.unit
def test_non_uniform_items_fall_back_to_one_string():
    html = (
        "<ul><!--$C1--><!--$I1--><li>plain</li><!--/$I1-->"
        "<!--$I1--><li><b><!--$2-->x<!--/$2--></b></li><!--/$I1--><!--/$C1--></ul>"
    )

    rendered = Rendered.from_marked_html(html)

    assert rendered.dynamic == ["<li>plain</li><li><b>x</b></li>"]


@pytest.mark.unit
def test_empty_clause_is_plain_text():
    html = "<ul><!--$C1--><li>nothing yet</li><!--/$C1--></ul>"

    rendered = Rendered.from_marked_html(html)

    assert rendered.dynamic == ["<li>nothing yet</li>"]


@pytest.mark.unit
def test_nested_loops_round_trip():
    inner = "<!--$C5--><!--$I5--><i><!--$6-->q<!--/$6--></i><!--/$I5--><!--/$C5-->"
    html = f"<ul><!--$C1--><!--$I1--><li><!--$2-->a<!--/$2-->{inner}</li><!--/$I1--><!--/$C1--></ul>"

    rendered = Rendered.from_marked_html(html)
    comp = rendered.dynamic[0]

    assert isinstance(comp, Comprehension)
    assert isinstance(comp.dynamics[0][1], Comprehension)
    assert rendered.to_html() == "<ul><li>a<i>q</i></li></ul>"
    restored = Rendered.from_dict(rendered.to_dict())
    assert restored == rendered
    assert restored.get_diff(rendered) is None


@pytest.mark.unit
def test_flat_markers_still_parse_as_before():
    rendered = Rendered.from_marked_html("<p><!--$0-->a<!--/$0--> <!--$1--><!--/$1--></p>")

    assert rendered.static == ["<p>", " ", "</p>"]
    assert rendered.dynamic == ["a", ""]


@pytest.mark.unit
def test_strip_markers_removes_every_kind():
    html = "<ul><!--$C1--><!--$I1--><li><!--$2-->a<!--/$2--></li><!--/$I1--><!--/$C1--></ul>"
    assert strip_markers(html) == "<ul><li>a</li></ul>"


@pytest.mark.unit
def test_if_block_is_a_nested_render():
    html = "<p><!--$B0--><b><!--$1-->Y<!--/$1--></b><!--/$B0--></p>"

    rendered = Rendered.from_marked_html(html)

    assert rendered.static == ["<p>", "</p>"]
    block = rendered.dynamic[0]
    assert isinstance(block, Rendered)
    assert block.static == ["<b>", "</b>"] and block.dynamic == ["Y"]
    assert rendered.to_html() == "<p><b>Y</b></p>"
    assert rendered.get_diff(None).to_payload()["d"] == [{"r": ["<b>", "</b>"], "d": ["Y"]}]


@pytest.mark.unit
def test_switching_if_branch_is_a_partial_diff():
    on = Rendered.from_marked_html("<p><!--$B0--><b><!--$1-->Y<!--/$1--></b><!--/$B0--></p>")
    off = Rendered.from_marked_html("<p><!--$B0-->no<!--/$B0--></p>")

    diff = off.get_diff(on)

    assert diff is not None and not diff.is_full
    assert diff.to_payload() == {"0": {"r": ["no"], "d": []}}


@pytest.mark.unit
def test_change_inside_if_block_sends_nested_partial():
    a = Rendered.from_marked_html("<p><!--$B0--><b><!--$1-->Y<!--/$1--></b><!--/$B0--></p>")
    b = Rendered.from_marked_html("<p><!--$B0--><b><!--$1-->Z<!--/$1--></b><!--/$B0--></p>")

    diff = b.get_diff(a)

    assert diff is not None and diff.to_payload() == {"0": {"p": {"0": "Z"}}}


@pytest.mark.unit
def test_loop_items_with_conditionals_stay_uniform():
    def li(done: bool, name: str) -> str:
        cls = "<!--$B2-->done<!--/$B2-->" if done else "<!--$B2--><!--/$B2-->"
        return f'<!--$I1--><li class="{cls}"><!--$3-->{name}<!--/$3--></li><!--/$I1-->'

    html = f"<ul><!--$C1-->{li(True, 'a')}{li(False, 'b')}<!--/$C1--></ul>"
    rendered = Rendered.from_marked_html(html)

    comp = rendered.dynamic[0]
    assert isinstance(comp, Comprehension)
    assert comp.static == ['<li class="', '">', "</li>"]
    assert comp.dynamics[0] == [Rendered(static=["done"], dynamic=[]), "a"]
    assert comp.dynamics[1] == [Rendered(static=[""], dynamic=[]), "b"]
    assert rendered.to_html() == '<ul><li class="done">a</li><li class="">b</li></ul>'
    assert Rendered.from_dict(rendered.to_dict()) == rendered
