"""A template tag for tests/test_querystring.py: one that reads more of the request than its query (#210).

Loaded through the engine's ``libraries`` option (``{% load qs_tags %}``), so no
app of the test project carries it.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_path(context):
    """Reads ``context.request.path``, which a component's template does not have."""
    return context.request.path
