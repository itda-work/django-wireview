"""Template tags for tests/test_shared_render.py: templates and bodies another tag renders (#208).

Loaded through the engine's ``libraries`` option (``{% load sh_tags %}``), so no
app of the test project carries it. Each tag is a shape a library may take that
the scope check must read through.
"""

from django import template
from django.template import Template
from django.template.library import InclusionNode

register = template.Library()


@register.inclusion_tag("sh/card.html", takes_context=True)
def sh_card(context):
    """Hands the viewer to its template, as an inclusion tag for a page often does."""
    return {"user": context.get("user")}


@register.inclusion_tag("sh/request_card.html", takes_context=True)
def sh_request_card(context):
    """Hands the request to its template: there, ``request`` is never the component's field."""
    return {"request": context.get("request")}


@register.inclusion_tag(Template("<i>safe</i>"), takes_context=True)
def sh_safe_card(context):
    """A template made from a string, as Django's docs show: its Origin names no file."""
    return {}


@register.inclusion_tag(Template("<b>{{ user.username }}</b>"), takes_context=True)
def sh_viewer_card(context):
    """Another template made from a string, with the same Origin name as ``sh_safe_card``'s."""
    return {"user": context.get("user")}


class _SubInclusionNode(InclusionNode):
    pass


@register.tag
def sh_sub_card(parser, token):
    """An inclusion tag whose node is a subclass of Django's."""

    def card(context):
        return {"user": context.get("user")}

    return _SubInclusionNode(card, True, [], {}, "sh/card.html")


class _WrapNode(template.Node):
    """A block tag that keeps its body under a name of its own, without ``child_nodelists``."""

    def __init__(self, body):
        self.body = body

    def render(self, context):
        return f"<div>{self.body.render(context)}</div>"


@register.tag
def sh_wrap(parser, token):
    body = parser.parse(("endsh_wrap",))
    parser.delete_first_token()
    return _WrapNode(body)
