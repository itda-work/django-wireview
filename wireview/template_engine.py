"""Template engine for automatic dynamic marker injection.

This module provides mechanisms to automatically wrap Django template
variable outputs with markers for efficient diffing.

The approach is to traverse the template nodelist and wrap VariableNode
outputs with HTML comment markers that identify dynamic regions.
"""

from __future__ import annotations

import itertools
import typing as t
from contextlib import contextmanager
from html import escape as escape_html

from django.conf import settings
from django.http import HttpRequest
from django.template import Context, Template
from django.template.base import FilterExpression, Node, NodeList, Variable, VariableNode, render_value_in_context
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader_tags import IncludeNode
from django.template.smartif import TokenBase
from django.utils.safestring import SafeString

from .core import render_reads


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

    __slots__ = ("_counter", "_comprehensions", "reads", "frames", "recording")

    def __init__(self) -> None:
        self._counter = 0
        self._comprehensions: list[int] = []
        #: The reads this render tracks (#111), read once per render rather than
        #: once per node: every dynamic node of every render asks
        self.reads: render_reads.RenderReads | None = None
        #: What each open part being recorded drew from elsewhere (``drew()``)
        self.frames: list[set[tuple[t.Any, ...]]] = []
        #: How many renders under way record what their parts draw (``WireviewMeta.drawing``).
        #: While none does, a part need not look its owner up to learn that it does not
        self.recording = 0

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
        self.frames.clear()

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
        #: Django's own node, whose render this one does itself (``render_value``)
        self._plain = type(original_node) is VariableNode

    def render(self, context: Context) -> str:
        """Render the variable with markers.

        Empty output is marked too: skipping the marker would merge two
        static parts and shift every following index, turning a value that
        toggles between "" and text into a full render.
        """
        # Every dynamic value of every render comes through here (#176): what
        # inject_marker() and next_index() do is spelled out in place
        marker_context = self.marker_context
        reads = marker_context.reads
        slot = reads.open() if reads is not None else None
        try:
            if self._plain:
                try:
                    output = render_value(self.filter_expression.resolve(context), context)
                except UnicodeDecodeError:
                    output = ""  # as VariableNode.render
            else:
                output = self.original_node.render(context)  # a subclass renders its own way
        finally:
            # The index follows the output, as the parser numbers it
            index = marker_context._counter
            marker_context._counter = index + 1
            if slot is not None:
                reads.close(slot, index)  # type: ignore[union-attr]
        return f"<!--${index}-->{output}<!--/${index}-->"

    def __repr__(self) -> str:
        return f"<MarkedVariableNode: {self.filter_expression!r}>"


def render_value(value: t.Any, context: Context) -> str:
    """``render_value_in_context(value, context)``, a plain ``str`` or ``int`` printed without the detour.

    Django prints an int as ``str()`` unless it groups thousands -- but it reaches
    that shortcut only after ``get_language()`` and three ``get_format()`` calls
    (``number_format``), about 2 µs an int and half of a numeric list's render
    (#176). Grouping needs ``USE_THOUSAND_SEPARATOR`` with localization on, so
    without it the text is ``str(value)``, as Django's own shortcut makes it.
    A plain ``str`` passes ``template_localtime()`` and ``localize()`` unchanged
    and is escaped as ``django.utils.html.escape`` escapes it. Only the exact
    types: not ``bool``, not ``SafeString``, nor any subclass, whose ``__str__``
    or ``__html__`` may differ.
    """
    kind = type(value)
    if kind is str:
        return SafeString(escape_html(value)) if context.autoescape else value
    if kind is int and (context.use_l10n is False or not settings.USE_THOUSAND_SEPARATOR):
        return str(value)
    return render_value_in_context(value, context)


def drew(item: tuple[t.Any, ...]) -> None:
    """The render drew ``item`` from elsewhere: ``("c", id, moved)`` a nested component, ``("s", name, key)`` a slot.

    Every part open around it records it (``_PartNode``).
    """
    for frame in get_template_marker().marker_context.frames:
        frame.add(item)


def _wire(component: t.Any) -> t.Any:
    """``component.wire``, unseen by the reads a render tracks: ``wire`` is a field, and no part reads it."""
    try:
        return object.__getattribute__(component, "wire")
    except AttributeError:
        return None


def _moved_at(component: t.Any) -> int:
    """When ``component``'s own render last showed something new (``WireviewMeta.moved``): a record holds one per row.

    Not its signed state: what its render shows moves outside it too (a
    temporary assign, an excluded field, a property).
    """
    return _wire(component).moved


def drew_component(component: t.Any) -> None:
    drew(("c", component.id, _moved_at(component)))


class _PartNode(Node):
    """A block or a loop: a part a reset temporary assign can keep whole (#111).

    Kept, a part shows what the component's last render of its own drew there,
    and that may hold what the component's names do not decide: another
    component's drawing, which its own renders move, or a slot's fill.
    So each render of its own records, per part, what the part drew from
    elsewhere (``WireviewMeta.drawn``), and a kept part is drawn again only when
    one of those has moved since: the component's own render showed something
    new, or the fill is another. A part kept as it was takes its record along.
    """

    #: Which part of its template this is: the same across compilations of it
    key: tuple[t.Any, ...] = ()
    marker_context: MarkerContext

    def _record(self, context: Context) -> tuple[t.Any, set[tuple[t.Any, ...]] | None]:
        """The owner's meta and a frame for what this part draws, while the owner records its render.

        ``(None, None)`` when no render under way records: ``_moved()`` looks the owner up itself.
        """
        if not self.marker_context.recording:
            return None, None
        wire = _wire(context.get("this"))
        if getattr(wire, "drawing", None) is None:
            return wire, None
        frame: set[tuple[t.Any, ...]] = set()
        self.marker_context.frames.append(frame)
        return wire, frame

    def _settle_record(self, wire: t.Any, frame: set[tuple[t.Any, ...]] | None, kept: bool) -> None:
        if frame is None:
            return
        frames = self.marker_context.frames
        frames.pop()
        if kept:
            carried = wire.drawn.get(self.key, frozenset())
            frame |= carried
            for outer in frames:
                outer |= carried
        # A part in a loop's items settles once per item: merged in place, not copied
        wire.drawing.setdefault(self.key, set()).update(frame)

    def _moved(self, wire: t.Any, context: Context) -> bool:
        """Whether something this part drew from elsewhere last time is another now."""
        if wire is None:
            wire = _wire(context.get("this"))
        record = wire.drawn.get(self.key) if wire is not None else None
        if not record:
            return False
        repo = context.get("wireview_repository")
        slots = context.get("slots")
        for kind, name, drawn in record:
            if kind == "c":
                component = repo.components.get(name) if repo is not None else None
                if component is not None and _moved_at(component) != drawn:
                    return True
            elif (slots.drawn_key(name) if slots else None) != drawn:
                return True
        return False


class ComprehensionNode(_PartNode):
    """Wraps a ``{% for %}`` node so its whole output is one comprehension slot."""

    def __init__(self, for_node: Node, marker_context: MarkerContext) -> None:
        self.for_node = for_node
        self.marker_context = marker_context
        self.token = getattr(for_node, "token", None)

    def render(self, context: Context) -> str:
        marker_context = self.marker_context
        index = marker_context.next_index()
        if marker_context.reads is None and not marker_context.recording:
            # Nothing to track or record: the loop and its items, marked (#176)
            marker_context._comprehensions.append(index)
            try:
                output = self.for_node.render(context)
            finally:
                marker_context._comprehensions.pop()
            return f"<!--$C{index}-->{output}<!--/$C{index}-->"
        self.marker_context.push_comprehension(index)
        reads = self.marker_context.reads
        slot = reads.open(index) if reads else None
        wire, frame = self._record(context)
        kept = False
        try:
            output = self.for_node.render(context)
        finally:
            if reads and slot:
                # Items keep what they drew, unless what they drew from elsewhere moved on
                if slot.stale and not slot.other and self._moved(wire, context):
                    slot.other = True
                kept = slot.stale and not slot.other
                reads.close(slot)
            self._settle_record(wire, frame, kept)
            self.marker_context.pop_comprehension()
        return f"<!--$C{index}-->{output}<!--/$C{index}-->"

    def __repr__(self) -> str:
        return f"<ComprehensionNode: {self.for_node!r}>"


#: What ``referenced_names`` reports for an ``{% include %}`` whose template it
#: cannot know: one named at render time, or one it cannot find
DRAWS_UNKNOWN = "<unknown>"


def referenced_names(node: Node) -> frozenset[str]:
    """Every name ``node`` and the nodes inside it can resolve, in every branch.

    ``this.count`` counts as ``count``. Found statically, so a branch that did
    not render is included: a block whose condition reads a stale temporary
    assign may hold something else, which a render that took the other branch
    never read (#111). An ``{% include %}`` of a named template counts that
    template's names, and one it cannot know adds ``DRAWS_UNKNOWN``. Only
    template structures are walked -- a node's ``origin`` leads to the loader
    and every template it holds.
    """
    cached = getattr(node, "_wireview_names", None)
    if cached is not None:
        return cached
    names: set[str] = set()
    seen: set[int] = set()
    included: set[str] = set()

    def include(value: IncludeNode) -> None:
        # The parser already made a relative name absolute
        name = value.template.var
        if not isinstance(name, str):
            names.add(DRAWS_UNKNOWN)  # a template chosen at render time
            return
        if name in included:
            return
        included.add(name)
        try:
            found = value.origin.loader.engine.get_template(name)  # type: ignore[union-attr]
        except (AttributeError, TemplateDoesNotExist):
            names.add(DRAWS_UNKNOWN)
            return
        walk(found.nodelist)

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
            if isinstance(value, IncludeNode):
                include(value)
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


class _BlockNode(_PartNode):
    """Wraps a node so its output is a nested block with its own statics."""

    def __init__(self, inner: Node, marker_context: MarkerContext) -> None:
        self.inner = inner
        self.marker_context = marker_context
        self.token = getattr(inner, "token", None)

    def render(self, context: Context) -> str:
        marker_context = self.marker_context
        index = marker_context.next_index()
        reads = marker_context.reads
        if reads is None and not marker_context.recording:
            # Nothing to track or record: the block, marked (#176)
            return f"<!--$B{index}-->{self.inner.render(context)}<!--/$B{index}-->"
        slot = reads.open(index) if reads else None
        wire, frame = self._record(context)
        kept = False
        try:
            output = self.inner.render(context)
        finally:
            if reads and slot:
                # Kept whole, a block would keep whatever else it can show: another
                # name in a branch that did not render, a template it cannot see
                # into, or what it drew from elsewhere that moved on since
                if slot.stale and not slot.other:
                    names = referenced_names(self.inner)
                    slot.other = bool(names & reads.other) or DRAWS_UNKNOWN in names or self._moved(wire, context)
                kept = slot.stale and not slot.other
                reads.close(slot)
            self._settle_record(wire, frame, kept)
        return f"<!--$B{index}-->{output}<!--/$B{index}-->"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self.inner!r}>"


class ConditionalNode(_BlockNode):
    """Wraps ``{% if %}`` so each branch is a nested block with its own statics."""


class IncludedNode(_BlockNode):
    """Wraps ``{% include %}`` so what the included template draws is a part of its own.

    The included template is not given markers -- it may be shared with pages
    that are not components -- so its output was static: any change to it was
    a full render, and one that read a reset temporary assign could not keep
    what it drew (#111).
    """


class ComprehensionItemNode(Node):
    """Renders one loop iteration and marks it as an item of the enclosing comprehension."""

    def __init__(self, nodelist: NodeList, marker_context: MarkerContext) -> None:
        self.nodelist = nodelist
        self.marker_context = marker_context

    def render(self, context: Context) -> str:
        output = self.nodelist.render(context)
        comprehensions = self.marker_context._comprehensions
        if not comprehensions:
            return output
        index = comprehensions[-1]
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
            # A part is known by its place in the template's source, which a
            # template compiled again (an uncached loader) keeps
            self._wrap_nodelist(nodelist, (hash(getattr(inner_template, "source", "")), itertools.count()))
        inner_template._wireview_marker = self  # type: ignore[attr-defined]

        return template

    def _wrap_nodelist(self, nodelist: NodeList, parts: tuple[int, itertools.count]) -> None:
        """Recursively wrap VariableNodes in a nodelist; ``parts`` numbers the template's parts."""

        def part(node: _PartNode) -> _PartNode:
            node.key = (parts[0], next(parts[1]))
            return node

        for i, node in enumerate(nodelist):
            if isinstance(node, VariableNode):
                # Check if this variable should be wrapped
                if self._should_wrap_variable(node):
                    nodelist[i] = MarkedVariableNode(node, self.marker_context)
            elif isinstance(node, IncludeNode):
                nodelist[i] = part(IncludedNode(node, self.marker_context))
                continue

            # {% if %}: wrap every branch, then the node itself as a block. IfNode's
            # ``nodelist`` property builds a throwaway list, so go via the branches.
            conditions = getattr(node, "conditions_nodelists", None)
            if conditions is not None and not isinstance(node, ConditionalNode):
                for _condition, branch in conditions:
                    self._wrap_nodelist(branch, parts)
                nodelist[i] = part(ConditionalNode(node, self.marker_context))
                continue

            # Recursively process child nodelists
            for attr in ("nodelist", "nodelist_true", "nodelist_false"):
                child_nodelist = getattr(node, attr, None)
                if child_nodelist is not None:
                    self._wrap_nodelist(child_nodelist, parts)

            # {% for %}: mark the loop as a comprehension and each iteration as an item
            if hasattr(node, "nodelist_loop") and not isinstance(node, ComprehensionNode):
                loop_nodelist: NodeList = node.nodelist_loop  # type: ignore[attr-defined]
                self._wrap_nodelist(loop_nodelist, parts)
                if not (len(loop_nodelist) == 1 and isinstance(loop_nodelist[0], ComprehensionItemNode)):
                    node.nodelist_loop = NodeList([ComprehensionItemNode(loop_nodelist, self.marker_context)])  # type: ignore[attr-defined]
                if hasattr(node, "nodelist_empty"):
                    self._wrap_nodelist(node.nodelist_empty, parts)  # type: ignore[attr-defined]
                nodelist[i] = part(ComprehensionNode(node, self.marker_context))

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
