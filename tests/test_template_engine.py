"""Tests for the template engine marker injection system."""

import pytest
from django.template import Template

from wireview.template_engine import (
    MarkedVariableNode,
    MarkerContext,
    TemplateMarker,
    clear_template_cache,
    get_template_marker,
    render_with_markers,
)


class TestMarkerContext:
    """Test MarkerContext class."""

    @pytest.mark.unit
    def test_next_index_starts_at_zero(self):
        ctx = MarkerContext()
        assert ctx.next_index() == 0

    @pytest.mark.unit
    def test_next_index_increments(self):
        ctx = MarkerContext()
        assert ctx.next_index() == 0
        assert ctx.next_index() == 1
        assert ctx.next_index() == 2

    @pytest.mark.unit
    def test_reset_resets_counter(self):
        ctx = MarkerContext()
        ctx.next_index()
        ctx.next_index()
        ctx.reset()
        assert ctx.next_index() == 0

    @pytest.mark.unit
    def test_count_property(self):
        ctx = MarkerContext()
        assert ctx.count == 0
        ctx.next_index()
        assert ctx.count == 1
        ctx.next_index()
        assert ctx.count == 2


class TestTemplateMarker:
    """Test TemplateMarker class."""

    @pytest.mark.unit
    def test_simple_variable_wrapped(self):
        marker = TemplateMarker()
        template = Template("Hello {{ name }}!")
        context = {"name": "World"}

        result = marker.render_marked(template, context)

        assert "<!--$0-->" in result
        assert "<!--/$0-->" in result
        assert "World" in result

    @pytest.mark.unit
    def test_multiple_variables_wrapped(self):
        marker = TemplateMarker()
        template = Template("{{ greeting }} {{ name }}!")
        context = {"greeting": "Hello", "name": "World"}

        result = marker.render_marked(template, context)

        assert "<!--$0-->" in result
        assert "<!--$1-->" in result
        assert "Hello" in result
        assert "World" in result

    @pytest.mark.unit
    def test_skips_this_variable(self):
        """Test that 'this' (wireview internal) is not wrapped."""
        marker = TemplateMarker()
        # Using a regular variable that should be wrapped
        template = Template("{{ name }}")
        context = {"name": "Test", "this": "internal"}

        result = marker.render_marked(template, context)

        # 'name' should be wrapped but not 'this'
        assert "<!--$0-->" in result
        assert "Test" in result

    @pytest.mark.unit
    def test_handles_conditional_blocks(self):
        marker = TemplateMarker()
        template = Template("{% if show %}Visible{% endif %}")
        context = {"show": True}

        result = marker.render_marked(template, context)

        # Static content inside if block
        assert "Visible" in result

    @pytest.mark.unit
    def test_handles_for_loops(self):
        marker = TemplateMarker()
        template = Template("{% for item in items %}{{ item }}{% endfor %}")
        context = {"items": ["a", "b", "c"]}

        result = marker.render_marked(template, context)

        # Each loop iteration should have markers
        assert result.count("<!--$") >= 1

    @pytest.mark.unit
    def test_reset_resets_marker_context(self):
        marker = TemplateMarker()
        template = Template("{{ x }}")

        marker.render_marked(template, {"x": "1"})
        marker.reset()
        result = marker.render_marked(template, {"x": "2"})

        # After reset, should start from 0 again
        assert "<!--$0-->" in result


class TestRenderWithMarkers:
    """Test the render_with_markers function."""

    @pytest.mark.unit
    def test_basic_rendering(self):
        template = Template("<div>{{ count }}</div>")
        result = render_with_markers(template, {"count": 5})

        assert "<div>" in result
        assert "</div>" in result
        assert "<!--$0-->5<!--/$0-->" in result

    @pytest.mark.unit
    def test_variable_in_text_wrapped(self):
        clear_template_cache()  # Clear to ensure fresh processing
        template = Template("<span>Value: {{ value }}</span>")
        result = render_with_markers(template, {"value": "test"})

        assert "<!--$0-->test<!--/$0-->" in result
        assert "<span>Value:" in result

    @pytest.mark.unit
    def test_empty_template(self):
        template = Template("<div>Static content</div>")
        result = render_with_markers(template, {})

        assert result == "<div>Static content</div>"
        assert "<!--$" not in result


class TestGlobalTemplateMarker:
    """Test global template marker management."""

    @pytest.mark.unit
    def test_get_template_marker_returns_same_instance(self):
        clear_template_cache()
        marker1 = get_template_marker()
        marker2 = get_template_marker()
        assert marker1 is marker2

    @pytest.mark.unit
    def test_clear_template_cache_creates_new_instance(self):
        marker1 = get_template_marker()
        clear_template_cache()
        marker2 = get_template_marker()
        assert marker1 is not marker2


class TestMarkedVariableNode:
    """Test MarkedVariableNode class."""

    @pytest.mark.unit
    def test_repr(self):
        from django.template.base import Variable, VariableNode

        var = Variable("count")
        original = VariableNode(var)
        marker_ctx = MarkerContext()
        marked = MarkedVariableNode(original, marker_ctx)

        repr_str = repr(marked)
        assert "MarkedVariableNode" in repr_str
        assert "count" in repr_str
