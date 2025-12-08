import typing as t

from django import template
from django.core.signing import Signer
from django.template.base import Node, Parser, Token
from django.utils.html import format_html

from .. import settings
from ..component import Component
from ..event_transpiler import transpile
from ..repository import ComponentRepository

register = template.Library()


@register.inclusion_tag("wireview_header.html")
def wireview_header():
    return {"BOOST_PAGES": settings.BOOST_PAGES}


@register.simple_tag(takes_context=True)
def tag_header(context):
    component: Component = context["this"]
    repo: ComponentRepository = context["wireview_repository"]
    return format_html(
        ('id="{id}" ' 'data-name="{name}" ' 'data-state="{state}" ' 'data-is-live="{is_live}" ' "wireview-component"),
        id=component.id,
        name=component._name,
        is_live=str(repo.is_live).lower(),
        state=Signer().sign(component.model_dump_json(exclude=component._exclude_fields)),
    )


@register.simple_tag(takes_context=True)
def component(context, _name, **kwargs):
    if (repo := context.get("wireview_repository")) is None:
        qs = (request := context.get("request")) and request.META["QUERY_STRING"] or ""
        repo = ComponentRepository(
            is_live=False,
            user=context.get("user"),
            params=ComponentRepository.extract_params(qs),
        )
        context["wireview_repository"] = repo

    component = repo.build(_name, state=kwargs)
    return component._render(repo) or ""


@register.simple_tag(takes_context=True)
def on(context, _event_and_modifiers, _command, **kwargs: t.Any):
    """
    Bind an event handler to an element.

    Supports both string commands (server event handlers) and JS command
    builder objects (client-side commands).

    Examples:
        {% on "click" "increment" %}
        {% on "click" "save" item_id=item.id %}
        {% on "click" JS().toggle("#modal") %}
        {% on "click" JS().push("save").hide() %}
    """
    from ..js import JS

    component: Component | None = context.get("this")
    assert component, "Can't find a component in this context"

    # Validate handler for string commands (not JS objects)
    if isinstance(_command, str):
        handler = getattr(component, _command, None)
        assert handler, f"Missing handler: {component._name}.{_command}"
        assert callable(handler), f"Not callable: {component._name}.{_command}"
    elif isinstance(_command, JS):
        # Validate push events in JS commands reference valid handlers
        for cmd in _command._commands:
            if cmd.get("cmd") == "push":
                event_name = cmd.get("event")
                if event_name:
                    handler = getattr(component, event_name, None)
                    assert handler, f"Missing handler: {component._name}.{event_name}"
                    assert callable(handler), f"Not callable: {component._name}.{event_name}"

    event, code = transpile(_event_and_modifiers, _command, kwargs)
    return format_html('{event}="{code}"', event=event, code=code)


@register.filter(name="str")
def to_string(value):
    return str(value)


@register.filter
def concat(value, arg):
    return f"{value}{arg}"


# Shortcuts and helpers


@register.tag()
def cond(parser: Parser, token: Token):
    """Prints some text conditionally

        ```html
        {% cond {'works': True, 'does not work': 1 == 2} %}
        ```
    Will output 'works'.
    """
    dict_expression = token.contents[len("cond ") :]
    return CondNode(dict_expression)


@register.tag(name="class")
def class_cond(parser: Parser, token: Token):
    """Prints classes conditionally

    ```html
    <div {% class {'btn': True, 'loading': loading, 'falsy': 0} %}></div>
    ```

    If `loading` is `True` will print:

    ```html
    <div class="btn loading"></div>
    ```
    """
    dict_expression = token.contents[len("class ") :]
    return ClassNode(dict_expression)


class CondNode(Node):
    def __init__(self, dict_expression):
        self.dict_expression = dict_expression

    def render(self, context):
        variables: dict[str, t.Any] = context.flatten()  # type: ignore
        terms = eval(self.dict_expression, variables)
        return " ".join(term for term, ok in terms.items() if ok)


class ClassNode(CondNode):
    def render(self, *args, **kwargs):
        text = super().render(*args, **kwargs)
        return f'class="{text}"'


# Upload template tags


@register.simple_tag(takes_context=True)
def upload_input(context, name: str, **attrs):
    """
    Render a file input for uploads.

    Args:
        name: Upload field name (matches allow_upload name)
        **attrs: Additional HTML attributes (class, id, etc.)

    Example:
        {% upload_input "images" class="hidden" id="image-input" %}

    Note: The component must call allow_upload(name, ...) in joined()
    for this to work properly.
    """
    component: Component | None = context.get("this")
    if not component:
        return ""

    registry = getattr(component, "_upload_registry", None)
    if not registry or name not in registry.configs:
        return ""

    config = registry.configs[name]

    # Build attributes
    attrs_parts = []
    for key, value in attrs.items():
        attrs_parts.append(f'{key}="{value}"')
    attrs_str = " ".join(attrs_parts)

    accept = ",".join(config.accept) if config.accept else ""
    multiple = "multiple" if config.max_entries > 1 else ""

    return format_html(
        '<input type="file" wire-upload="{name}" accept="{accept}" {multiple} {attrs} '
        "onchange=\"wireview.addFiles(this, '{name}', this.files)\">",
        name=name,
        accept=accept,
        multiple=multiple,
        attrs=attrs_str,
    )


@register.simple_tag(takes_context=True)
def upload_drop_zone(context, name: str):
    """
    Return attribute for making an element a drop zone.

    Args:
        name: Upload field name (matches allow_upload name)

    Example:
        <div {% upload_drop_zone "images" %} class="drop-area">
            Drop files here
        </div>

    The drop zone will have 'wireview-drag-over' class added when
    a file is dragged over it.
    """
    return format_html('wire-upload-drop="{name}"', name=name)


@register.simple_tag(takes_context=True)
def upload_button(context, name: str, **attrs):
    """
    Render a button that triggers file selection.

    Args:
        name: Upload field name (matches allow_upload name)
        **attrs: Additional HTML attributes

    Example:
        {% upload_button "images" class="btn btn-primary" %}
            Select Images
        {% endupload_button %}
    """
    component: Component | None = context.get("this")
    if not component:
        return ""

    # Build attributes
    attrs_parts = []
    for key, value in attrs.items():
        attrs_parts.append(f'{key}="{value}"')
    attrs_str = " ".join(attrs_parts)

    return format_html(
        '<button type="button" {attrs} onclick="wireview.selectFiles(this, \'{name}\')">',
        name=name,
        attrs=attrs_str,
    )
