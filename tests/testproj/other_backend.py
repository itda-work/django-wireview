"""A template backend that is not Django's, shaped as ``django.template.backends.jinja2`` (#210).

A component's template renders with Django's engine only. tests/test_component_templates.py
and the W022 rows of tests/test_checks.py put this engine ahead of a DjangoTemplates one:
the wrapper has ``template`` and ``backend`` as Django's own wrappers do, and the
backend has ``env`` -- no ``engine``. jinja2 is not a dependency, so its templates
are ``str.format`` strings over the context.

``StrictTemplates`` finds Django's syntax and cannot compile it, as Jinja2 does
with ``{% load %}``: Django's loader raises that error and asks no other engine.
"""

from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.backends.base import BaseEngine

#: The templates it has, by name
TEMPLATES = {
    "other/count.html": '<div id="{id}">{n}</div>',
    "other/fn.html": "<p>fn {n}</p>",
}


class _Inner:
    def __init__(self, source: str) -> None:
        self.source = source

    def render(self, context: dict) -> str:
        return self.source.format_map(context)


class _Env:
    def get_template(self, name: str) -> _Inner:
        if name not in TEMPLATES:
            raise TemplateDoesNotExist(name)
        return _Inner(TEMPLATES[name])


class OtherTemplates(BaseEngine):
    app_dirname = "other"

    def __init__(self, params: dict) -> None:
        params = params.copy()
        params.pop("OPTIONS")
        super().__init__(params)
        self.env = _Env()

    def get_template(self, template_name: str) -> "Template":
        return Template(self.env.get_template(template_name), self)


class Template:
    def __init__(self, template: _Inner, backend: OtherTemplates) -> None:
        self.template = template
        self.backend = backend

    def render(self, context: dict | None = None, request: object = None) -> str:
        return self.template.render(dict(context or {}))


class StrictTemplates(OtherTemplates):
    """Finds ``STRICT``'s names and fails to compile them, as Jinja2 does on Django's tags."""

    STRICT = ("dj/count.html", "dj/fn.html")

    def get_template(self, template_name: str) -> "Template":
        if template_name in self.STRICT:
            raise TemplateSyntaxError("Encountered unknown tag 'load'.")
        return super().get_template(template_name)
