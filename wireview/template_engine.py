"""Template engine for automatic dynamic marker injection.

This module provides mechanisms to automatically wrap Django template
variable outputs with markers for efficient diffing.

The approach is to traverse the template nodelist and wrap VariableNode
outputs with HTML comment markers that identify dynamic regions.
"""

from __future__ import annotations

import typing as t
from contextlib import contextmanager

from django.http import HttpRequest
from django.template import Context, Template
from django.template.base import FilterExpression, Node, NodeList, Variable, VariableNode
from django.template.smartif import TokenBase

from .core import render_reads
from .core.rendered import inject_marker


class BackendTemplate(t.Protocol):
    """Protocol for Django template backend wrappers (e.g., from loader.get_template())."""

    template: Template

    def render(
        self,
        context: dict[str, t.Any] | None = ...,
        request: HttpRequest | None = ...,
    ) -> str: ...


# Node types that should NOT be wrapped with markers
# (they produce attributes or structural elements)
SKIP_VARIABLE_NAMES = frozenset(
    {
        "this",
        "wireview_repository",
        "forloop",
        "block",
        "slots",  # Slot container for component content composition
    }
)


class MarkerContext:
    """Tracks marker indices and the enclosing comprehensions during a render."""

    __slots__ = ("_counter", "_comprehensions", "reads")

    def __init__(self) -> None:
        self._counter = 0
        self._comprehensions: list[int] = []
        #: The reads this render tracks (#111), read once per render rather than
        #: once per node: every dynamic node of every render asks
        self.reads: render_reads.RenderReads | None = None

    def next_index(self) -> int:
        """Get the next marker index."""
        idx = self._counter
        self._counter += 1
        return idx

    def skip(self, count: int) -> int:
        """Take ``count`` indices at once, for output drawn earlier; the first of them."""
        idx = self._counter
        self._counter += count
        return idx

    def reset(self) -> None:
        """Reset the counter for a new render."""
        self._counter = 0
        self._comprehensions.clear()

    def push_comprehension(self, index: int) -> None:
        self._comprehensions.append(index)

    def pop_comprehension(self) -> None:
        self._comprehensions.pop()

    @property
    def current_comprehension(self) -> int | None:
        return self._comprehensions[-1] if self._comprehensions else None

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
        """Render the variable with markers.

        Empty output is marked too: skipping the marker would merge two
        static parts and shift every following index, turning a value that
        toggles between "" and text into a full render.
        """
        reads = self.marker_context.reads
        slot = reads.open() if reads else None
        try:
            output = self.original_node.render(context)
        finally:
            # The index follows the output, as the parser numbers it
            index = self.marker_context.next_index()
            if reads and slot:
                reads.close(slot, index)
        return inject_marker(output, index)

    def __repr__(self) -> str:
        return f"<MarkedVariableNode: {self.filter_expression!r}>"


class ComprehensionNode(Node):
    """Wraps a ``{% for %}`` node so its whole output is one comprehension slot."""

    def __init__(self, for_node: Node, marker_context: MarkerContext) -> None:
        self.for_node = for_node
        self.marker_context = marker_context
        self.token = getattr(for_node, "token", None)

    def render(self, context: Context) -> str:
        index = self.marker_context.next_index()
        self.marker_context.push_comprehension(index)
        reads = self.marker_context.reads
        slot = reads.open(index) if reads else None
        try:
            output = self.for_node.render(context)
        finally:
            if reads and slot:
                reads.close(slot)
            self.marker_context.pop_comprehension()
        return f"<!--$C{index}-->{output}<!--/$C{index}-->"

    def __repr__(self) -> str:
        return f"<ComprehensionNode: {self.for_node!r}>"


def referenced_names(node: Node) -> frozenset[str]:
    """Every name ``node`` and the nodes inside it can resolve, in every branch.

    ``this.count`` counts as ``count``. Found statically, so a branch that did
    not render is included: a block whose condition reads a stale temporary
    assign may hold something else, which a render that took the other branch
    never read (#111). Only template structures are walked -- a node's
    ``origin`` leads to the loader and every template it holds.
    """
    cached = getattr(node, "_wireview_names", None)
    if cached is not None:
        return cached
    names: set[str] = set()
    seen: set[int] = set()

    def walk(value: t.Any) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, Variable):
            lookups = list(value.lookups or ())
            if lookups:
                root = lookups[0]
                names.add(lookups[1] if root == "this" and len(lookups) > 1 else root)
        elif isinstance(value, FilterExpression):
            walk(value.var)
            for _func, args in value.filters:
                for _lookup, arg in args:
                    walk(arg)
        elif isinstance(value, (Node, TokenBase)) or type(value).__name__ == "TemplateLiteral":
            for name, attr in vars(value).items():
                if name not in ("origin", "token"):
                    walk(attr)
        elif isinstance(value, (list, tuple, NodeList)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(node)
    frozen = frozenset(names)
    node._wireview_names = frozen  # type: ignore[attr-defined]
    return frozen


class ConditionalNode(Node):
    """Wraps ``{% if %}`` so each branch is a nested block with its own statics."""

    def __init__(self, if_node: Node, marker_context: MarkerContext) -> None:
        self.if_node = if_node
        self.marker_context = marker_context
        self.token = getattr(if_node, "token", None)

    def render(self, context: Context) -> str:
        index = self.marker_context.next_index()
        reads = self.marker_context.reads
        slot = reads.open(index) if reads else None
        try:
            output = self.if_node.render(context)
        finally:
            if reads and slot:
                # Kept whole, a block would keep whatever else its branches show
                if slot.stale and not slot.other and referenced_names(self.if_node) & reads.other:
                    slot.other = True
                reads.close(slot)
        return f"<!--$B{index}-->{output}<!--/$B{index}-->"

    def __repr__(self) -> str:
        return f"<ConditionalNode: {self.if_node!r}>"


class ComprehensionItemNode(Node):
    """Renders one loop iteration and marks it as an item of the enclosing comprehension."""

    def __init__(self, nodelist: NodeList, marker_context: MarkerContext) -> None:
        self.nodelist = nodelist
        self.marker_context = marker_context

    def render(self, context: Context) -> str:
        output = self.nodelist.render(context)
        index = self.marker_context.current_comprehension
        if index is None:
            return output
        return f"<!--$I{index}-->{output}<!--/$I{index}-->"

    def __repr__(self) -> str:
        return "<ComprehensionItemNode>"


class TemplateMarker:
    """
    Instruments a Django template to add dynamic markers.

    This class traverses a template's nodelist and wraps VariableNodes
    with MarkedVariableNode to inject tracking markers.
    """

    def __init__(self) -> None:
        self.marker_context = MarkerContext()
        self._depth = 0
        # The depth whose template reports the names it resolves: the component
        # that renders, or one drawn in its pass that tracks its own (tracking())
        self._tracked_depth = 1

    def prepare_template(self, template: Template | BackendTemplate) -> Template | BackendTemplate:
        """
        Prepare a template for marked rendering.

        Traverses the nodelist and wraps variable nodes with markers.
        The template is modified in-place for efficiency.

        Args:
            template: Django Template instance (or backend wrapper)

        Returns:
            The same template (modified in-place)
        """
        # Handle backend wrapper templates (from loader.get_template())
        # which have the actual template in .template attribute
        # Get the inner template (backend wrappers have .template attribute)
        inner_template = t.cast(Template, getattr(template, "template", template))

        # A template prepared by this marker carries a stamp. The stamp must live on
        # the object, not in a set of id()s: with DEBUG on (or any uncached loader)
        # every render compiles a fresh Template, a freed one's id gets reused, and
        # an id-keyed set would skip the new object, leaving it without markers and
        # turning its next diff into a full render at random.
        if getattr(inner_template, "_wireview_marker", None) is self:
            return template

        # Access nodelist from Django's base Template
        nodelist = getattr(inner_template, "nodelist", None)
        if nodelist is not None:
            self._wrap_nodelist(nodelist)
        inner_template._wireview_marker = self  # type: ignore[attr-defined]

        return template

    def _wrap_nodelist(self, nodelist: NodeList) -> None:
        """Recursively wrap VariableNodes in a nodelist."""
        for i, node in enumerate(nodelist):
            if isinstance(node, VariableNode):
                # Check if this variable should be wrapped
                if self._should_wrap_variable(node):
                    nodelist[i] = MarkedVariableNode(node, self.marker_context)

            # {% if %}: wrap every branch, then the node itself as a block. IfNode's
            # ``nodelist`` property builds a throwaway list, so go via the branches.
            conditions = getattr(node, "conditions_nodelists", None)
            if conditions is not None and not isinstance(node, ConditionalNode):
                for _condition, branch in conditions:
                    self._wrap_nodelist(branch)
                nodelist[i] = ConditionalNode(node, self.marker_context)
                continue

            # Recursively process child nodelists
            for attr in ("nodelist", "nodelist_true", "nodelist_false"):
                child_nodelist = getattr(node, attr, None)
                if child_nodelist is not None:
                    self._wrap_nodelist(child_nodelist)

            # {% for %}: mark the loop as a comprehension and each iteration as an item
            if hasattr(node, "nodelist_loop") and not isinstance(node, ComprehensionNode):
                loop_nodelist: NodeList = node.nodelist_loop  # type: ignore[attr-defined]
                self._wrap_nodelist(loop_nodelist)
                if not (len(loop_nodelist) == 1 and isinstance(loop_nodelist[0], ComprehensionItemNode)):
                    node.nodelist_loop = NodeList([ComprehensionItemNode(loop_nodelist, self.marker_context)])  # type: ignore[attr-defined]
                if hasattr(node, "nodelist_empty"):
                    self._wrap_nodelist(node.nodelist_empty)  # type: ignore[attr-defined]
                nodelist[i] = ComprehensionNode(node, self.marker_context)

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

    def next_index(self) -> int:
        """The index the next template rendered takes first: 0 unless a render is under way."""
        return self.marker_context.count if self._depth else 0

    @contextmanager
    def tracking(self, reads: render_reads.RenderReads) -> t.Iterator[None]:
        """Track ``reads`` for the template rendered next, inside the render under way.

        A component drawn in another component's pass has temporary assigns of
        its own; its parts are numbered in that pass, and what they read is its
        own business (#111). The render around it tracks again afterwards.
        """
        saved = self.marker_context.reads, self._tracked_depth
        self.marker_context.reads, self._tracked_depth = reads, self._depth + 1
        try:
            with reads:
                yield
        finally:
            self.marker_context.reads, self._tracked_depth = saved

    def render_marked(self, template: Template | BackendTemplate, context: dict[str, t.Any]) -> str:
        """
        Render a template with dynamic markers.

        Args:
            template: Django Template instance (or backend wrapper)
            context: Template context dictionary

        Returns:
            Rendered HTML with dynamic markers
        """
        # A nested component render (``{% component %}`` inside a template) must
        # keep counting: resetting mid-render would duplicate indexes and drop
        # the enclosing comprehension.
        if self._depth == 0:
            self.reset()
            self.marker_context.reads = render_reads.active()
        prepared = self.prepare_template(template)

        self._depth += 1
        try:
            # Backend wrappers (from loader.get_template()) accept a dict,
            # raw django.template.base.Template needs a Context.
            reads = self.marker_context.reads if self._depth == self._tracked_depth else None
            if reads is not None:
                # The names the template resolves are reported (#111). A backend
                # wrapper builds a plain Context itself, so build this one as it would.
                inner = t.cast(Template, getattr(prepared, "template", prepared))
                autoescape = getattr(getattr(prepared, "backend", None), "engine", inner.engine).autoescape
                return inner.render(render_reads.TrackingContext(context, autoescape=autoescape))
            if hasattr(prepared, "template"):
                return prepared.render(context)  # type: ignore[arg-type]
            return prepared.render(Context(context))  # type: ignore[arg-type]
        finally:
            self._depth -= 1


# Global template marker instance
_template_marker: TemplateMarker | None = None


def get_template_marker() -> TemplateMarker:
    """Get or create the global template marker instance."""
    global _template_marker
    if _template_marker is None:
        _template_marker = TemplateMarker()
    return _template_marker


def render_with_markers(template: Template | BackendTemplate, context: dict[str, t.Any]) -> str:
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
