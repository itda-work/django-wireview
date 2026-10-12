"""Template tags for tests/test_querystring.py: one that reads more of the request than its query, one
that reads the query when there is a request (#210).

Loaded through the engine's ``libraries`` option (``{% load qs_tags %}``), so no
app of the test project carries it.
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_path(context):
    """Reads ``context.request.path``, which a component's template does not have."""
    return context.request.path


@register.simple_tag(takes_context=True)
def qs_get(context):
    """A third-party tag that reads the query when there is a request, as ``getattr(context, "request", None)``."""
    request = getattr(context, "request", None)
    if request is None:
        return "no-request"
    return request.GET.urlencode()
