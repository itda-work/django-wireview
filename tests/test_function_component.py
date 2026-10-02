"""Tests for function components (GAP-003)."""

import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template, TemplateSyntaxError
from django.test import override_settings

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.state import sign_state, state_of, unsign_state
from wireview.function_components import (
    FunctionComponent,
    _registry,
    function_component,
    get_function_component,
    list_function_components,
)
from wireview.repository import ComponentRepository

# Test fixtures - function components


@function_component
def button(text: str, variant: str = "primary"):
    """Simple button component."""
    return f'<button class="btn btn-{variant}">{text}</button>'


@function_component
def icon(name: str, size: int = 24):
    """Icon component with type coercion."""
    return f'<svg class="icon icon-{name}" width="{size}" height="{size}"></svg>'


@function_component(name="custom_name")
def my_alert(message: str, type: str = "info"):
    """Alert with custom registration name."""
    return f'<div class="alert alert-{type}">{message}</div>'


@function_component
def greeting(name: str):
    """Component with required argument only."""
    return f"<span>Hello, {name}!</span>"


class TestFunctionComponentDecorator:
    """Test the @function_component decorator."""

    def test_simple_registration(self):
        """Test that decorator registers component."""
        assert "button" in _registry
        assert isinstance(_registry["button"], FunctionComponent)

    def test_custom_name_registration(self):
        """Test registration with custom name."""
        assert "custom_name" in _registry
        assert _registry["custom_name"].func.__name__ == "my_alert"

    def test_fqn_registration(self):
        """Test FQN (fully qualified name) registration."""
        # Should be registered with module prefix
        fqn = f"{button.func.__module__}.button"
        assert fqn in _registry

    def test_metadata_preservation(self):
        """Test that function metadata is preserved."""
        assert button.__doc__ == "Simple button component."
        assert button.__name__ == "button"


class TestFunctionComponentRendering:
    """Test rendering function components."""

    def test_simple_render(self):
        """Test basic rendering."""
        result = button(text="Click me")
        assert result == '<button class="btn btn-primary">Click me</button>'

    def test_render_with_kwargs(self):
        """Test rendering with custom kwargs."""
        result = button(text="Delete", variant="danger")
        assert result == '<button class="btn btn-danger">Delete</button>'

    def test_render_with_type_coercion(self):
        """Test that string numbers are coerced to int."""
        fc = get_function_component("icon")
        result = fc.render({"name": "check", "size": "32"})
        assert 'width="32"' in result
        assert 'height="32"' in result

    def test_render_with_default_values(self):
        """Test that default values are used."""
        result = icon(name="star")
        assert 'width="24"' in result  # default size

    def test_required_argument_missing(self):
        """Test error when required argument is missing."""
        with pytest.raises(TypeError) as exc_info:
            greeting()  # 'name' is required

        assert "missing required argument" in str(exc_info.value)
        assert "'name'" in str(exc_info.value)

    def test_unexpected_argument(self):
        """Test error when unexpected argument is passed."""
        fc = get_function_component("button")
        with pytest.raises(TypeError) as exc_info:
            fc.render({"text": "Hi", "unknown_arg": "value"})

        assert "unexpected arguments" in str(exc_info.value)
        assert "unknown_arg" in str(exc_info.value)


class TestFunctionComponentLookup:
    """Test component lookup functions."""

    def test_get_by_name(self):
        """Test lookup by simple name."""
        fc = get_function_component("button")
        assert fc.name == "button"

    def test_get_by_custom_name(self):
        """Test lookup by custom name."""
        fc = get_function_component("custom_name")
        assert fc.func.__name__ == "my_alert"

    def test_get_not_found(self):
        """Test error when component not found."""
        with pytest.raises(TemplateSyntaxError) as exc_info:
            get_function_component("nonexistent")

        assert "not found" in str(exc_info.value)

    def test_list_components(self):
        """Test listing all registered components."""
        components = list_function_components()
        assert "button" in components
        assert "icon" in components
        assert "custom_name" in components


class TestFuncTemplateTag:
    """Test {% func %} template tag."""

    def test_simple_func_tag(self):
        """Test basic func tag usage."""
        template = Template('{% load wireview %}{% func "button" text="Click" %}')
        result = template.render(Context({}))
        assert '<button class="btn btn-primary">Click</button>' in result

    def test_func_tag_with_multiple_args(self):
        """Test func tag with multiple arguments."""
        template = Template('{% load wireview %}{% func "button" text="Delete" variant="danger" %}')
        result = template.render(Context({}))
        assert '<button class="btn btn-danger">Delete</button>' in result

    def test_func_tag_with_context_variable(self):
        """Test func tag with context variable."""
        template = Template('{% load wireview %}{% func "button" text=btn_text %}')
        result = template.render(Context({"btn_text": "Submit"}))
        assert "Submit" in result

    def test_func_tag_component_not_found(self):
        """Test error when component not found."""
        template = Template('{% load wireview %}{% func "nonexistent" %}')
        with pytest.raises(TemplateSyntaxError):
            template.render(Context({}))


class TestFuncBlockTemplateTag:
    """Test {% func_block %}...{% endfunc %} template tag."""

    def test_block_tag_with_default_slot(self):
        """Test func_block with default slot content."""

        # First, create a template-based function component for testing
        @function_component(template="test_func_card.html")
        def test_card(title: str = ""):
            return {"title": title}

        template = Template(
            """{% load wireview %}
{% func_block "test_card" title="Hello" %}
    <p>Default content</p>
{% endfunc %}"""
        )
        result = template.render(Context({}))
        # The result depends on the template, but should not raise
        assert result is not None

    def test_block_tag_syntax_error_no_name(self):
        """Test that missing component name raises error."""
        with pytest.raises(TemplateSyntaxError):
            Template("{% load wireview %}{% func_block %}{% endfunc %}")


class TestTypeCoercion:
    """Test type coercion for function component arguments."""

    def test_string_to_int(self):
        """Test string to int coercion."""
        fc = get_function_component("icon")
        result = fc.render({"name": "test", "size": "48"})
        assert 'width="48"' in result

    def test_string_to_bool_true_values(self):
        """Test string to bool coercion for truthy values."""

        @function_component
        def checkbox(checked: bool = False):
            return f'<input type="checkbox" {"checked" if checked else ""}>'

        fc = get_function_component("checkbox")

        # True values
        assert "checked" in fc.render({"checked": "true"})
        assert "checked" in fc.render({"checked": "1"})
        assert "checked" in fc.render({"checked": "yes"})
        assert "checked" in fc.render({"checked": True})

    def test_string_to_bool_false_values(self):
        """Test string to bool coercion for falsy values."""

        @function_component
        def toggle(enabled: bool = True):
            return f'<div class="{"enabled" if enabled else "disabled"}"></div>'

        fc = get_function_component("toggle")

        # False values
        assert "disabled" in fc.render({"enabled": "false"})
        assert "disabled" in fc.render({"enabled": "0"})
        assert "disabled" in fc.render({"enabled": "no"})
        assert "disabled" in fc.render({"enabled": ""})
        assert "disabled" in fc.render({"enabled": False})


class TestValidation:
    """Test argument validation."""

    def test_validate_args_required(self):
        """Test validation of required arguments."""
        fc = get_function_component("greeting")

        with pytest.raises(TypeError) as exc_info:
            fc.validate_args({})

        assert "missing required argument" in str(exc_info.value)

    def test_validate_args_with_defaults(self):
        """Test validation includes default values."""
        fc = get_function_component("button")

        validated = fc.validate_args({"text": "Test"})

        assert validated["text"] == "Test"
        assert validated["variant"] == "primary"  # default

    def test_validate_args_unexpected(self):
        """Test validation rejects unexpected arguments."""
        fc = get_function_component("button")

        with pytest.raises(TypeError) as exc_info:
            fc.validate_args({"text": "Test", "extra": "bad"})

        assert "unexpected arguments" in str(exc_info.value)


@pytest.mark.unit
class TestFunctionComponentUnit:
    """Unit tests for FunctionComponent class."""

    def test_dataclass_fields(self):
        """Test FunctionComponent has expected fields."""
        fc = get_function_component("button")

        assert hasattr(fc, "func")
        assert hasattr(fc, "name")
        assert hasattr(fc, "template")
        assert hasattr(fc, "slots")

    def test_signature_extraction(self):
        """Test parameter info extraction."""
        fc = get_function_component("button")

        assert "text" in fc._param_info
        assert fc._param_info["text"]["required"] is True

        assert "variant" in fc._param_info
        assert fc._param_info["variant"]["required"] is False
        assert fc._param_info["variant"]["default"] == "primary"

    def test_repr(self):
        """Test FunctionComponent repr."""
        fc = get_function_component("button")
        repr_str = repr(fc)

        assert "FunctionComponent" in repr_str
        assert "button" in repr_str


# A {% component %} in a function component's template is the page's


FC_TEMPLATES = {
    "fc/host.html": "{% load wireview %}<div {% tag_header %}><b>{{ n }}</b>{% func 'fc_counter_card' %}</div>",
    "fc/blockhost.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ n }}</b>{% func_block 'fc_counter_card' %}{% endfunc %}</div>"
    ),
    "fc/card.html": "{% load wireview %}<section>{% component 'FcCounter' id='counter' %}</section>",
    "fc/counter.html": "{% load wireview %}<p {% tag_header %}>C{{ count }}</p>",
}


@function_component(template="fc/card.html")
def fc_counter_card():
    return {}


class FcHost(Component):
    class Meta:
        template_name = "fc/host.html"

    n: int = 0

    async def bump(self):
        self.n += 1


class FcBlockHost(FcHost):
    class Meta:
        template_name = "fc/blockhost.html"


class FcCounter(Component):
    class Meta:
        template_name = "fc/counter.html"

    count: int = 0

    async def inc(self):
        self.count += 1


class _Outbound:
    def __init__(self) -> None:
        self.renders: list[dict] = []

    async def send_command(self, command: str, payload: dict) -> None:
        if command == "render":
            self.renders.append(payload)

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


@pytest.fixture
def fc_templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", FC_TEMPLATES)]},
            }
        ]
    ):
        yield


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db
@pytest.mark.usefixtures("fc_templates")
@pytest.mark.parametrize("host_name", ["FcHost", "FcBlockHost"])
async def test_a_live_render_draws_the_component_the_page_joined(host_name):
    """The host draws its function component again: the counter stays as the page changed it.

    Drawn in a repository of its own, each render of the host drew a fresh
    counter from the template's arguments -- ``C0``, its state signed back to
    0 -- over the one the page had joined and changed.
    """
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    consumer.outbound = _Outbound()  # type: ignore[assignment]
    host = await consumer.repo.join(host_name, {"id": "host"})
    await consumer.send_render(host)
    counter = consumer.repo.get("counter")
    assert counter is not None, "the host's pass built the counter in the page's repository"
    await consumer.command_join(FcCounter._fqn, sign_state(counter))
    assert consumer.repo.get("counter") is counter, "the join took up the instance the pass built"
    await consumer.command_user_event("counter", "inc", {}, {})

    await consumer.command_user_event("host", "bump", {}, {})

    drawn = host.wire._last_rendered.to_html()  # type: ignore[union-attr]
    assert "<b>1</b>" in drawn, "the control: the host rendered again"
    assert ">C1</p>" in drawn, drawn
    token = re.search(r'id="counter" data-name="[^"]+" data-state="([^"]+)"', drawn)
    assert token is not None, drawn
    assert unsign_state(token.group(1), FcCounter._fqn) == state_of(counter)
