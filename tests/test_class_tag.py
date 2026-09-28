"""``{% class %}`` and ``{% cond %}`` read dotted names the way the rest of a template does (#113).

Their expression is evaluated as Python, so a dot was an attribute lookup: inside
a loop, ``forloop.counter0`` (a dict) failed, and so did ``item.title`` on a row
from ``.values()``. examples/search's dropdown could not render once it had results.
"""

import pytest
from django.template import Context, Template

pytestmark = pytest.mark.unit


def render(source: str, **context) -> str:
    return Template("{% load wireview %}" + source).render(Context(context))


def test_forloop_reads_inside_a_class_list():
    out = render(
        "{% for x in rows %}<i {% class {'on': forloop.counter0 == picked} %}></i>{% endfor %}", rows="ab", picked=1
    )
    assert out == '<i class=""></i><i class="on"></i>'


def test_a_dict_row_reads_with_dots():
    assert render("<i {% class {'done': row.done} %}></i>", row={"done": True}) == '<i class="done"></i>'


def test_nested_dicts_read_with_dots_too():
    assert render("{% cond {'x': a.b.c} %}", a={"b": {"c": 1}}) == "x"


def test_attributes_still_read_as_attributes():
    class Row:
        done = True

    assert render("<i {% class {'done': row.done} %}></i>", row=Row()) == '<i class="done"></i>'
