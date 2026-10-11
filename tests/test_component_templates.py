"""A component's template renders with Django's template engine only (#210).

wireview's tags (``{% tag_header %}``, ``{% on %}``, ``{% component %}``) and a
live render's markers are Django's, so a component's template found by another
engine -- Jinja2 ahead of DjangoTemplates in ``TEMPLATES`` -- never rendered
live. Getting one now raises ``ImproperlyConfigured`` that names the class, the
template and the engine, on the first render: a component's, a stream or
Broadcast item's (all through ``Component._get_template``), a function
component's. ``manage.py check`` says it first (``wireview.W022``,
tests/test_checks.py). A page's template may be any engine's.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from wireview import Component, function_component
from wireview import testing as wireview_testing

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

#: Found by the other engine first, by Django's too: the order in TEMPLATES decides
DJANGO = {
    "other/count.html": "<div>never drawn</div>",
    "dj/count.html": "{% load wireview %}<div {% tag_header %}>{{ n }}</div>",
    "dj/fn.html": "<p>fn {{ n }}</p>",
}

WHY = (
    r"template 'other/{name}' was found by the 'other_backend' template engine "
    r"\(testproj\.other_backend\.OtherTemplates\), not by a DjangoTemplates one: wireview renders a "
    r"component's template with Django's template engine only"
)


class OtCount(Component):
    class Meta:
        template_name = "other/count.html"

    n: int = 0


class DjCount(Component):
    class Meta:
        template_name = "dj/count.html"

    n: int = 0


@function_component(template="other/fn.html")
def ot_fn(n: int):
    return {"n": n}


@function_component(template="dj/fn.html")
def dj_fn(n: int):
    return {"n": n}


@pytest.fixture(autouse=True)
def _engines():
    with override_settings(
        TEMPLATES=[
            {"BACKEND": "testproj.other_backend.OtherTemplates", "DIRS": [], "OPTIONS": {}},
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", DJANGO)]},
            },
        ]
    ):
        yield


@pytest.mark.asyncio
async def test_a_component_whose_template_another_engine_finds_is_told_on_its_first_render():
    mounted = await wireview_testing.mount(OtCount, id="o")
    with pytest.raises(ImproperlyConfigured, match="OtCount's " + WHY.format(name="count.html")):
        mounted.render()
    with pytest.raises(ImproperlyConfigured, match="OtCount's " + WHY.format(name="count.html")):
        await mounted.render_diff()


def test_an_items_template_another_engine_finds_is_told_too():
    """A stream or Broadcast item's template comes through the same door."""
    with pytest.raises(ImproperlyConfigured, match="DjCount's " + WHY.format(name="count.html")):
        DjCount._get_template("other/count.html")


def test_a_function_component_whose_template_another_engine_finds_is_told():
    with pytest.raises(ImproperlyConfigured, match="Function component 'ot_fn''s " + WHY.format(name="fn.html")):
        ot_fn(n=2)


@pytest.mark.asyncio
async def test_django_templates_render_beside_another_engine():
    """Only the engine of a component's template matters: the page's may be any engine's."""
    assert dj_fn(n=2) == "<p>fn 2</p>"
    mounted = await wireview_testing.mount(DjCount, id="d")
    assert mounted.render().endswith(">0</div>")
