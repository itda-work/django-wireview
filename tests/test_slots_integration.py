"""Integration tests for the slots system.

Tests for rendering components with slots using template tags.
"""

import pytest
from django.template import Context, Template, TemplateSyntaxError

# Import components to register them
from testproj.slots.components import Alert, Card, List, Modal  # noqa: F401


@pytest.mark.django_db
class TestComponentBlockTag:
    """Tests for the {% component_block %} template tag."""

    def test_component_block_basic(self):
        """Test basic component block rendering."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            <p>Card content</p>
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "card-body" in result
        assert "Card content" in result

    def test_component_block_with_attrs(self):
        """Test component block with attributes."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" title="My Card" variant="primary" %}
            <p>Content</p>
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # Note: "primary" may be wrapped with markers like <!--$0-->primary<!--/$0-->
        assert "primary" in result
        assert "My Card" in result

    def test_component_block_with_fill(self):
        """Test component block with fill tags."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}
                <h1>Custom Header</h1>
            {% endfill %}
            <p>Body content</p>
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "card-header" in result
        assert "Custom Header" in result
        assert "Body content" in result

    def test_component_block_with_multiple_fills(self):
        """Test component block with multiple fill tags."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}
                <h1>Header</h1>
            {% endfill %}
            <p>Body</p>
            {% fill footer %}
                <button>Save</button>
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "card-header" in result
        assert "card-footer" in result
        assert "Header" in result
        assert "Body" in result
        assert "Save" in result


@pytest.mark.django_db
class TestFillTag:
    """Tests for the {% fill %} template tag."""

    def test_fill_accesses_parent_context(self):
        """Test that fill content can access parent template context."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}
                <h1>{{ page_title }}</h1>
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({"page_title": "Welcome"}))

        assert "Welcome" in result

    def test_fill_accesses_component_attrs(self):
        """Test that fill content can access component attributes."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" title="Card Title" %}
            {% fill header %}
                <h1>{{ title }}</h1>
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Card Title" in result

    def test_fill_with_let_binding(self):
        """Test fill tag with let: variable binding."""
        template_str = """
        {% load wireview %}
        {% component_block "List" items=items %}
            {% fill item let:item let:index %}
                <span>{{ index }}. {{ item.name }}</span>
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        items = [{"name": "Apple"}, {"name": "Banana"}, {"name": "Cherry"}]
        result = template.render(Context({"items": items}))

        assert "1. Apple" in result
        assert "2. Banana" in result
        assert "3. Cherry" in result


@pytest.mark.django_db
class TestRenderSlotTag:
    """Tests for the {% render_slot %} template tag."""

    def test_render_slot_default(self):
        """Test rendering default slot."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            Default content here
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Default content here" in result

    def test_render_slot_fallback(self):
        """Test fallback when slot is not provided."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" title="Fallback Title" %}
            Body only
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # When no header fill is provided, Card uses title as fallback
        assert "Fallback Title" in result

    def test_render_slot_with_extra_context(self):
        """Test render_slot with extra context for let binding."""
        template_str = """
        {% load wireview %}
        {% component_block "List" items=items %}
            {% fill item let:item %}
                <strong>{{ item.name }}</strong>
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        items = [{"name": "Test Item"}]
        result = template.render(Context({"items": items}))

        assert "Test Item" in result


@pytest.mark.django_db
class TestSlotConditionals:
    """Tests for slot conditional rendering."""

    def test_slots_truthiness_in_template(self):
        """Test {% if slots.header %} works correctly."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}Header Content{% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # Header is provided, so card-header should be rendered
        assert "card-header" in result

    def test_slots_falsy_when_empty(self):
        """Test that empty slots are falsy."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            Body only
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # No header fill, but title is also empty, so no card-header
        # (unless Card template has title fallback)
        assert "Body only" in result


@pytest.mark.django_db
class TestSlotValidation:
    """Tests for slot validation."""

    def test_required_slot_raises_error(self):
        """Test that missing required slot raises error."""
        template_str = """
        {% load wireview %}
        {% component_block "Modal" %}
            Body content only
        {% endcomponent %}
        """
        template = Template(template_str)

        # Modal requires 'title' slot
        with pytest.raises(TemplateSyntaxError) as exc_info:
            template.render(Context({}))

        assert "requires slot 'title'" in str(exc_info.value)

    def test_required_slot_provided(self):
        """Test that providing required slot works."""
        template_str = """
        {% load wireview %}
        {% component_block "Modal" %}
            {% fill title %}Modal Title{% endfill %}
            Body content
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Modal Title" in result


@pytest.mark.django_db
class TestNestedComponents:
    """Tests for nested components with slots."""

    def test_nested_component_with_slots(self):
        """Test nesting components with slots."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}
                <h1>Outer Card</h1>
            {% endfill %}
            {% component_block "Alert" message="Inner alert" %}
                {% fill icon %}
                    <span>!</span>
                {% endfill %}
            {% endcomponent %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Outer Card" in result
        assert "Inner alert" in result


@pytest.mark.django_db
class TestBackwardsCompatibility:
    """Tests for backwards compatibility with simple component tag."""

    def test_simple_component_still_works(self):
        """Test that {% component %} simple tag still works."""
        template_str = """
        {% load wireview %}
        {% component "Card" title="Simple Card" %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Simple Card" in result
        assert "card" in result.lower()

    def test_simple_and_block_coexist(self):
        """Test that simple and block tags can coexist."""
        template_str = """
        {% load wireview %}
        {% component "Alert" message="Simple alert" %}
        {% component_block "Card" %}
            {% fill header %}Block header{% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Simple alert" in result
        assert "Block header" in result


@pytest.mark.django_db
class TestEdgeCases:
    """Edge case tests for slots system."""

    def test_empty_fill_tag(self):
        """Test that empty fill tags render nothing."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}{% endfill %}
            Body content
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Body content" in result
        # Empty header slot should still create the header element
        assert "card-header" in result

    def test_whitespace_only_fill(self):
        """Test that whitespace-only fills are handled correctly."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}

            {% endfill %}
            Real content
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Real content" in result

    def test_multiple_fills_same_name(self):
        """Test behavior when multiple fills have the same name."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}First{% endfill %}
            {% fill header %}Second{% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # Both should be rendered (slots are additive)
        assert "First" in result
        assert "Second" in result

    def test_deeply_nested_components(self):
        """Test deeply nested component slots."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" title="Level 1" %}
            {% fill header %}L1 Header{% endfill %}
            {% component_block "Card" title="Level 2" %}
                {% fill header %}L2 Header{% endfill %}
                {% component_block "Card" title="Level 3" %}
                    {% fill header %}L3 Header{% endfill %}
                    Innermost content
                {% endcomponent %}
            {% endcomponent %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "L1 Header" in result
        assert "L2 Header" in result
        assert "L3 Header" in result
        assert "Innermost content" in result

    def test_slot_with_template_tags(self):
        """Test slots containing Django template tags."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}
                {% if True %}Conditional Header{% endif %}
            {% endfill %}
            {% for i in "123" %}Item {{ i }}{% endfor %}
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Conditional Header" in result
        assert "Item 1" in result
        assert "Item 2" in result
        assert "Item 3" in result

    def test_slot_with_special_characters(self):
        """Test slots with special HTML characters."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            {% fill header %}<script>alert('xss')</script>{% endfill %}
            &lt;escaped&gt;
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        # Content should be preserved (Django handles escaping at variable level)
        assert "<script>" in result  # fill content is raw HTML
        assert "&lt;escaped&gt;" in result

    def test_let_binding_with_missing_variable(self):
        """Test let: binding when component doesn't provide the variable."""
        template_str = """
        {% load wireview %}
        {% component_block "List" items=items %}
            {% fill item let:item let:missing_var %}
                Item: {{ item.name }}, Missing: {{ missing_var|default:"N/A" }}
            {% endfill %}
        {% endcomponent %}
        """
        template = Template(template_str)
        items = [{"name": "Test"}]
        result = template.render(Context({"items": items}))

        assert "Item: Test" in result
        assert "Missing: N/A" in result

    def test_component_block_without_any_slots(self):
        """Test component_block with only default content, no fills."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" %}
            Just default content, no fills at all.
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "Just default content" in result
        # Should not have header since no header slot and no title
        assert "card-header" not in result

    def test_slot_accessing_component_property(self):
        """Test that slot can use component properties via context."""
        template_str = """
        {% load wireview %}
        {% component_block "Card" variant="primary" %}
            {% fill footer %}
                Variant is available in component, not here.
            {% endfill %}
            Default content
        {% endcomponent %}
        """
        template = Template(template_str)
        result = template.render(Context({}))

        assert "card-" in result  # variant applied to card class
        assert "Default content" in result
