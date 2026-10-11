"""The shape django-template-partials gives partials on Django 5.2, for tests/test_shared_render.py (#208).

Django 6 merged that package: there, ``"file#name"`` is the engine's and its
partial a ``PartialTemplate``. On 5.2 the package's loader answers the name
with a ``TemplateProxy`` -- the file's Origin, the partial's name, and no
relation to Django's class -- and its ``{% partialdef %}`` keeps the partials
in the parser's ``extra_data`` under a key of its own. This is that shape,
without the package: the scope check must tell a partial by its shape.
"""

from django import template
from django.template.loaders.locmem import Loader as LocmemLoader

register = template.Library()


class TemplateProxy:
    """What the package's loader returns for ``"file#name"``."""

    def __init__(self, nodelist, origin, name):
        self.nodelist = nodelist
        self.origin = origin
        self.name = name

    def render(self, context):
        return self.nodelist.render(context)


class Loader(LocmemLoader):
    """A locmem loader that answers ``"file#name"`` with the file's partial, as the package's does."""

    def get_template(self, template_name, skip=None):
        name, _, partial = template_name.partition("#")
        found = super().get_template(name, skip=skip)
        if not partial:
            return found
        try:
            return found.extra_data["template-partials"][partial]
        except KeyError:
            raise template.TemplateDoesNotExist(template_name) from None


class DefineNode(template.Node):
    def __init__(self, partial_name, inline, nodelist):
        self.partial_name = partial_name
        self.inline = inline
        self.nodelist = nodelist

    def render(self, context):
        return self.nodelist.render(context) if self.inline else ""


class RenderPartialNode(template.Node):
    def __init__(self, name, partials):
        self.name = name
        self.partials = partials

    def render(self, context):
        return self.partials[self.name].render(context)


@register.tag
def partialdef(parser, token):
    bits = token.split_contents()
    name = bits[1]
    nodelist = parser.parse(("endpartialdef", f"endpartialdef {name}"))
    parser.delete_first_token()
    partials = parser.extra_data.setdefault("template-partials", {})
    partials[name] = TemplateProxy(nodelist, parser.origin, name)
    return DefineNode(name, bits[2:] == ["inline"], nodelist)


@register.tag
def partial(parser, token):
    """Keeps the file's partials, not the name's attribute: the check reads the name off the token."""
    _tag, name = token.split_contents()
    return RenderPartialNode(name, parser.extra_data.setdefault("template-partials", {}))
