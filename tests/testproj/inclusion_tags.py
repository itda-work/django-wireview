"""An inclusion tag for tests/test_shared_render.py: a template another tag renders (#208).

Loaded through the engine's ``libraries`` option (``{% load sh_tags %}``), so no
app of the test project carries it.
"""

from django import template

register = template.Library()


@register.inclusion_tag("sh/card.html", takes_context=True)
def sh_card(context):
    """Hands the viewer to its template, as an inclusion tag for a page often does."""
    return {"user": context.get("user")}
