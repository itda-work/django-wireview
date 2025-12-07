"""Template engine for automatic dynamic marker injection.

This module provides mechanisms to automatically wrap Django template
variable outputs with markers for efficient diffing.

The approach is to traverse the template nodelist and wrap VariableNode
outputs with HTML comment markers that identify dynamic regions.
"""

from __future__ import annotations

import typing as t

from django.template import Context, Template
from django.template.base import Node, NodeList, VariableNode

from .core.rendered import inject_marker

# Node types that should NOT be wrapped with markers
# (they produce attributes or structural elements)
SKIP_VARIABLE_NAMES = frozenset(
    {
        "this",
        "wireview_repository",
        "forloop",
        "block",
    }
)


class MarkerContext:
    """Tracks marker indices during template rendering."""

    __slots__ = ("_counter",)

    def __init__(self) -> None:
        self._counter = 0

    def next_index(self) -> int:
        """Get the next marker index."""
        idx = self._counter
        self._counter += 1
        return idx

    def reset(self) -> None:
        """Reset the counter for a new render."""
        self._counter = 0

    @property
    def count(self) -> int:
        """Get the current count of markers."""
        return self._counter


class MarkedVariableNode(Node):
    """
    A wrapper around VariableNode that adds markers to its output.

    This node wraps the original VariableNode and injects markers
    around its rendered output for diff tracking.
    """

    def __init__(self, original_node: VariableNode, marker_context: MarkerContext) -> None:
        self.original_node = original_node
        self.marker_context = marker_context
        # Copy attributes from original for compatibility
        self.token = original_node.token
        self.filter_expression = original_node.filter_expression

    def render(self, context: Context) -> str:
        """Render the variable with markers."""
        output = self.original_node.render(context)

        # Skip empty output
        if not output:
            return output

        # Get unique index for this dynamic part
        idx = self.marker_context.next_index()

        # Wrap with markers
        return inject_marker(output, idx)

    def __repr__(self) -> str:
        return f"<MarkedVariableNode: {self.filter_expression!r}>"


class TemplateMarker:
    """
    Instruments a Django template to add dynamic markers.

    This class traverses a template's nodelist and wraps VariableNodes
    with MarkedVariableNode to inject tracking markers.
    """

    def __init__(self) -> None:
        self.marker_context = MarkerContext()
        self._processed_templates: set[int] = set()

    def prepare_template(self, template: Template) -> Template:
        """
        Prepare a template for marked rendering.

        Traverses the nodelist and wraps variable nodes with markers.
        The template is modified in-place for efficiency.

        Args:
            template: Django Template instance

        Returns:
            The same template (modified in-place)
        """
        template_id = id(template)

        # Skip if already processed
        if template_id in self._processed_templates:
            return template

        self._wrap_nodelist(template.nodelist)
        self._processed_templates.add(template_id)

        return template

    def _wrap_nodelist(self, nodelist: NodeList) -> None:
        """Recursively wrap VariableNodes in a nodelist."""
        for i, node in enumerate(nodelist):
            if isinstance(node, VariableNode):
                # Check if this variable should be wrapped
                if self._should_wrap_variable(node):
                    nodelist[i] = MarkedVariableNode(node, self.marker_context)

            # Recursively process child nodelists
            for attr in ("nodelist", "nodelist_true", "nodelist_false"):
                child_nodelist = getattr(node, attr, None)
                if child_nodelist is not None:
                    self._wrap_nodelist(child_nodelist)

            # Handle for loops specially
            if hasattr(node, "nodelist_loop"):
                self._wrap_nodelist(node.nodelist_loop)  # type: ignore[attr-defined]
            if hasattr(node, "nodelist_empty"):
                self._wrap_nodelist(node.nodelist_empty)  # type: ignore[attr-defined]

    def _should_wrap_variable(self, node: VariableNode) -> bool:
        """
        Determine if a VariableNode should be wrapped with markers.

        Skip certain variables that are used for internal purposes
        or would produce invalid HTML if wrapped.
        """
        var_name = str(node.filter_expression.var)

        # Skip internal variables
        if var_name in SKIP_VARIABLE_NAMES:
            return False

        # Skip variables that start with underscore
        if var_name.startswith("_"):
            return False

        return True

    def reset(self) -> None:
        """Reset the marker context for a new render."""
        self.marker_context.reset()

    def render_marked(self, template: Template, context: dict[str, t.Any]) -> str:
        """
        Render a template with dynamic markers.

        Args:
            template: Django Template instance
            context: Template context dictionary

        Returns:
            Rendered HTML with dynamic markers
        """
        self.reset()
        prepared = self.prepare_template(template)
        return prepared.render(Context(context))


# Global template marker instance
_template_marker: TemplateMarker | None = None


def get_template_marker() -> TemplateMarker:
    """Get or create the global template marker instance."""
    global _template_marker
    if _template_marker is None:
        _template_marker = TemplateMarker()
    return _template_marker


def render_with_markers(template: Template, context: dict[str, t.Any]) -> str:
    """
    Render a template with automatic dynamic markers.

    This is the main entry point for marked rendering. It:
    1. Prepares the template (wraps variable nodes)
    2. Resets the marker counter
    3. Renders with markers

    Args:
        template: Django Template instance
        context: Template context dictionary

    Returns:
        Rendered HTML with dynamic markers
    """
    marker = get_template_marker()
    return marker.render_marked(template, context)


def clear_template_cache() -> None:
    """Clear the template marker cache (useful for testing)."""
    global _template_marker
    _template_marker = None
