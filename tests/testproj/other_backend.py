"""A template backend that is not Django's, shaped as ``django.template.backends.jinja2`` (#210).

tests/test_querystring.py renders components and function components with it:
a template of another backend is the backend's to render. The wrapper has
``template`` and ``backend`` as Django's own wrappers do, and the backend has
``env`` -- no ``engine``. jinja2 is not a dependency, so its templates are
``str.format`` strings over the context.
"""

from django.template import TemplateDoesNotExist
from django.template.backends.base import BaseEngine

#: The templates it has, by name
TEMPLATES = {
    "other/count.html": '<div id="{id}">{n}</div>',
    "other/fn.html": "<p>fn {n}</p>",
    "other/kept.html": '<div id="{id}">{n} {rows}</div>',
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
