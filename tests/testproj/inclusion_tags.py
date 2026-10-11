"""Template tags for tests/test_shared_render.py: templates and bodies another tag renders (#208).

Loaded through the engine's ``libraries`` option (``{% load sh_tags %}``), so no
app of the test project carries it. Each tag is a shape a library may take that
the scope check must read through.
"""

from django import template
from django.template import Template
from django.template.base import Parser
from django.template.defaulttags import WithNode
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


class _Slot:
    """Not a node, a list or a dict: a body kept in an object of the tag's own."""

    def __init__(self, nodelist):
        self.nodelist = nodelist


class _SlotsNode(template.Node):
    def __init__(self, slots):
        self.slots = slots

    def render(self, context):
        return "".join(slot.nodelist.render(context) for slot in self.slots.values())


@register.tag
def sh_slot(parser, token):
    body = parser.parse(("endsh_slot",))
    parser.delete_first_token()
    return _SlotsNode({"main": _Slot(body)})


class _BranchesNode(template.Node):
    """A body four containers down: list, tuple, list, node list."""

    def __init__(self, branches):
        self.branches = branches

    def render(self, context):
        return "".join(body.render(context) for _key, bodies in self.branches for body in bodies)


@register.tag
def sh_deep(parser, token):
    body = parser.parse(("endsh_deep",))
    parser.delete_first_token()
    return _BranchesNode([("x", [body])])


class _Value:
    """An argument wrapped in a value object, as django-classytags does."""

    def __init__(self, expression):
        self.expression = expression

    def resolve(self, context):
        return self.expression.resolve(context)


class _ShowNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return str(self.kwargs["value"].resolve(context))


@register.tag
def sh_show(parser, token):
    _tag, argument = token.split_contents()
    return _ShowNode({"value": _Value(parser.compile_filter(argument))})


class _VariableNode(template.Node):
    """Makes its ``Variable`` itself, as Django's docs show: past the parser's ``compile_filter``."""

    def __init__(self, variable):
        self.variable = template.Variable(variable)

    def render(self, context):
        return str(self.variable.resolve(context))


@register.tag
def sh_variable(parser, token):
    _tag, argument = token.split_contents()
    return _VariableNode(argument)


class _MacroNode(template.Node):
    """A macro's body: parsed where it is written, rendered where it is used (django-macros' shape)."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return ""


class _UseNode(template.Node):
    def __init__(self, body):
        self.body = body

    def render(self, context):
        return self.body.render(context)


@register.tag
def sh_macro(parser, token):
    """``{% sh_macro m %}...{% endsh_macro %}``: keeps the body on the parser for ``{% sh_use m %}``."""
    _tag, name = token.split_contents()
    nodelist = parser.parse(("endsh_macro",))
    parser.delete_first_token()
    parser.extra_data.setdefault("sh-macros", {})[name] = nodelist
    return _MacroNode(nodelist)


@register.tag
def sh_use(parser, token):
    """Renders a body parsed elsewhere in the file: inside a partial, one parsed outside it."""
    _tag, name = token.split_contents()
    return _UseNode(parser.extra_data["sh-macros"][name])


class _Ref:
    def __init__(self, body):
        self.body = body


class _UseObjNode(template.Node):
    """Keeps the body it renders in an object of its own: only the node's module tells it from Django's."""

    def __init__(self, body):
        self.ref = _Ref(body)

    def render(self, context):
        return self.ref.body.render(context)


@register.tag
def sh_use_obj(parser, token):
    _tag, name = token.split_contents()
    return _UseObjNode(parser.extra_data["sh-macros"][name])


@register.tag
def sh_use_with(parser, token):
    """Renders a body parsed elsewhere through a node of Django's: only what the node holds tells it."""
    _tag, name = token.split_contents()
    return WithNode(None, None, parser.extra_data["sh-macros"][name], {})


def _raw(parser, end):
    """The tokens up to ``{% end %}``, taken as they are: no node is made of them."""
    tokens = []
    while (token := parser.next_token()).contents != end:
        tokens.append(token)
    return tokens


class _LaterNode(template.Node):
    """Keeps its body as text and compiles it at render."""

    def __init__(self, text):
        self.text = text

    def render(self, context):
        return context.template.engine.from_string(self.text).render(context)


@register.tag
def sh_later(parser, token):
    shapes = {"VAR": "{{ %s }}", "BLOCK": "{%% %s %%}", "TEXT": "%s", "COMMENT": "{# %s #}"}
    return _LaterNode("".join(shapes[each.token_type.name] % each.contents for each in _raw(parser, "endsh_later")))


@register.tag
def sh_fresh(parser, token):
    """Parses its body with a parser of its own."""
    inner = Parser(_raw(parser, "endsh_fresh"), parser.libraries, None, parser.origin)
    inner.tags = dict(parser.tags)
    inner.filters = dict(parser.filters)
    return _WrapNode(inner.parse())


class _Alias:
    def __init__(self, nodelist):
        self.nodelist = nodelist


@register.tag
def sh_alias(parser, token):
    """Keeps its body in ``extra_data`` under a name, as a partial library does: a partial's namesake."""
    _tag, name = token.split_contents()
    nodelist = parser.parse(("endsh_alias",))
    parser.delete_first_token()
    parser.extra_data.setdefault("sh-aliases", {})[name] = _Alias(nodelist)
    return _MacroNode(nodelist)
