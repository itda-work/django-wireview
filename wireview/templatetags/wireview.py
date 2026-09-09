import asyncio
import typing as t
from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import async_to_sync
from django import template
from django.template.base import Node, NodeList, Parser, TextNode, Token, token_kwargs
from django.template.context import Context
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from .. import settings
from ..component import Component
from ..core.live_session import REQUEST_ATTR as LIVE_SESSION_REQUEST_ATTR
from ..core.live_session import declaration_allows, get_live_session
from ..core.rendered import inject_marker
from ..core.state import sign_state
from ..event_transpiler import transpile
from ..function_component import get_function_component
from ..repository import ComponentRepository
from ..slots import Slot, SlotContainer

register = template.Library()


@register.inclusion_tag("wireview_header.html", takes_context=True)
def wireview_header(context):
    """Load the client bundle and publish the page's navigation facts.

    The ``live_session`` name goes in a meta tag because the client has to know
    it before it decides whether a boosted navigation may morph the body: the
    boundary is what forces a full page load, and a full load is what gives the
    next page a fresh handshake under the current cookies (#58).
    """
    request = context.get("request")
    return {
        "BOOST_PAGES": settings.BOOST_PAGES,
        "LIVE_SESSION": getattr(request, LIVE_SESSION_REQUEST_ATTR, "") if request is not None else "",
    }


def _signed_state(component: Component, repo: ComponentRepository) -> str:
    """Return the signed component state for the ``data-state`` attribute.

    The client re-sends this value on (re)connect, so it must always reflect
    the latest state. In live (WebSocket) renders the value is wrapped in a
    dynamic marker so the diff engine treats it as a dynamic part. Without
    the marker the freshly signed state lands in the static parts, the
    fingerprint changes on every render, and every event ships a full render
    instead of a partial diff. HTTP renders are left untouched because a
    marker inside an attribute would corrupt the value used for the initial
    join.
    """
    signed = sign_state(component)
    if not repo.is_live:
        return signed
    from ..template_engine import get_template_marker

    index = get_template_marker().marker_context.next_index()
    return mark_safe(inject_marker(escape(signed), index))


@register.simple_tag(takes_context=True)
def tag_header(context):
    component: Component = context["this"]
    repo: ComponentRepository = context["wireview_repository"]
    return format_html(
        ('id="{id}" data-name="{name}" data-state="{state}" data-is-live="{is_live}" wireview-component'),
        id=component.id,
        name=component._name,
        is_live=str(repo.is_live).lower(),
        state=_signed_state(component, repo),
    )


def _mount_for_http(component: Component, repo: ComponentRepository) -> bool:
    """Run a component's mount-time boundary during a dead (HTTP) render.

    The hooks are an authorization boundary, so they have to cover the first
    HTML too: skipping them here would ship the protected page once and only
    stop the WebSocket join that follows. Components without hooks pay nothing
    because the common case returns on the first line.

    Getting an async callback out of a synchronous template pass depends on
    which thread that pass runs on:

    - A sync view, or an async view whose template pass is already wrapped in
      ``sync_to_async``, renders on a plain worker thread. ``async_to_sync``
      is the right bridge there.
    - An async view that calls ``render()`` directly renders on the event-loop
      thread itself, where ``async_to_sync`` refuses to run. Rather than fail
      the page, the hooks get a loop of their own on a helper thread. A hook
      that touches the ORM there opens its own connection, which is the same
      trade Django's own sync/async bridges make.

    A halt freezes the component and answers ``False``, and the caller renders
    nothing for it. Freezing rather than skipping the render outright is what
    keeps a redirect working: ``WireviewMeta.render`` still turns the URL a hook
    queued into a ``<meta http-equiv="refresh">``, and emits nothing else -- no
    template output, no ``data-state``.

    Returns:
        Whether the component may be rendered.
    """
    if not declaration_allows(type(component), repo.live_session):
        # Checked before the async question and on the live path too: a nested
        # {% component %} renders inline during its parent's pass, where there is
        # no seam to await a hook in, but this much needs no awaiting.
        component.wire.freeze()
        repo.remove(component.id)
        return False
    if repo.is_live:
        return True
    if not type(component)._on_mount and repo.live_session is None:
        return True

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        mounted = async_to_sync(component._mount)(repo.params, repo.session)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            mounted = pool.submit(asyncio.run, component._mount(repo.params, repo.session)).result()

    if not mounted:
        component.wire.freeze()
        repo.remove(component.id)
    return mounted


def _build_and_render_component(
    context: Context,
    component_name: str,
    kwargs: dict[str, t.Any],
    slots: SlotContainer | None = None,
) -> str:
    """Helper function to build and render a component."""
    if (repo := context.get("wireview_repository")) is None:
        request = context.get("request")
        qs = request and request.META["QUERY_STRING"] or ""
        repo = ComponentRepository(
            is_live=False,
            user=context.get("user"),
            params=ComponentRepository.extract_params(qs),
            session=getattr(request, "session", None),
            # The view decorator put the name here. Reading it off the request
            # rather than off a setting is what makes the boundary a property of
            # the page rather than of the project (#58).
            live_session=get_live_session(getattr(request, LIVE_SESSION_REQUEST_ATTR, "")) if request else None,
        )
        context["wireview_repository"] = repo

    component_instance = repo.build(component_name, state=kwargs)
    if not _mount_for_http(component_instance, repo):
        # Frozen: whatever comes back is a redirect meta or nothing at all.
        return component_instance._render(repo) or ""

    # Use slot-aware rendering if slots are provided
    if slots is not None:
        return component_instance._render_with_slots(repo, slots) or ""
    return component_instance._render(repo) or ""


@register.simple_tag(takes_context=True)
def component(context, _name, **kwargs):
    """
    Simple tag for rendering a component without slots.

    Usage:
        {% component 'Counter' count=10 %}
    """
    return _build_and_render_component(context, _name, kwargs)


# Slot-based component rendering


@register.tag("component_block")
def do_component_block(parser: Parser, token: Token):
    """
    Block tag for rendering a component with slots.

    Usage:
        {% component_block "Card" title="Hello" %}
            {% fill header %}
                <h1>{{ title }}</h1>
            {% endfill %}

            Default content goes here

            {% fill footer %}
                <button>Save</button>
            {% endfill %}
        {% endcomponent %}

    The content outside {% fill %} tags becomes the default slot.
    """
    bits = token.split_contents()
    tag_name = bits[0]

    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"'{tag_name}' tag requires a component name. "
            f'Usage: {{% {tag_name} "ComponentName" attr=value %}}...{{% endcomponent %}}'
        )

    component_name = bits[1]
    # Remove quotes if present
    if len(component_name) >= 2 and component_name[0] in ('"', "'") and component_name[-1] == component_name[0]:
        component_name = component_name[1:-1]

    # Parse remaining bits as kwargs
    remaining_bits = bits[2:]
    kwargs = token_kwargs(remaining_bits, parser)

    # Parse until {% endcomponent %}
    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()  # consume {% endcomponent %}

    return ComponentBlockNode(component_name, kwargs, nodelist)


class ComponentBlockNode(Node):
    """Node for {% component_block %}...{% endcomponent %} block tag."""

    def __init__(
        self,
        component_name: str,
        kwargs: dict[str, t.Any],
        nodelist: NodeList,
    ):
        self.component_name = component_name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        # Resolve kwargs
        resolved_kwargs = {}
        for key, value in self.kwargs.items():
            resolved_kwargs[key] = value.resolve(context)

        # Extract slots from nodelist
        slot_container = self._extract_slots(context)

        # Validate required slots
        self._validate_required_slots(slot_container)

        return _build_and_render_component(
            context,
            self.component_name,
            resolved_kwargs,
            slot_container,
        )

    def _extract_slots(self, context: Context) -> SlotContainer:
        return _extract_slots(self.nodelist, context)

    def _validate_required_slots(self, slots: SlotContainer) -> None:
        _validate_required_slots(self.component_name, slots)


def _extract_slots(nodelist: NodeList, context: Context) -> SlotContainer:
    """Turn the body of a block tag into a SlotContainer.

    Slot content handling depends on whether let: bindings are used:
    - Without let: Pre-render in parent context to capture parent variables
    - With let: Keep nodelist for render-time binding from component
    """
    container = SlotContainer()
    default_nodes = NodeList()

    for node in nodelist:
        if isinstance(node, FillNode):
            if node.let_vars:
                # Has let: bindings - keep nodelist for render-time binding
                # The variables will be provided by render_slot's extra_context
                slot = Slot(
                    name=node.slot_name,
                    nodelist=node.nodelist,
                    let_vars=node.let_vars,
                )
            else:
                # No let: bindings - pre-render to capture parent context
                rendered_content = node.nodelist.render(context)
                slot = Slot(
                    name=node.slot_name,
                    nodelist=NodeList([TextNode(rendered_content)]),
                    let_vars=[],
                )
            container.add(slot)
        else:
            # Default slot content
            default_nodes.append(node)

    # Only set default if there's actual content
    # Filter out whitespace-only TextNodes
    has_content = False
    for node in default_nodes:
        if hasattr(node, "s"):  # TextNode
            if node.s.strip():  # type: ignore[attr-defined]
                has_content = True
                break
        else:
            has_content = True
            break

    if has_content:
        # Pre-render default slot content in parent context
        rendered_default = default_nodes.render(context)
        container.set_default(NodeList([TextNode(rendered_default)]))

    return container


def _validate_required_slots(component_name: str, slots: SlotContainer) -> None:
    """Raise when a slot the component declares as required is missing."""
    if component_name not in Component._all:
        return

    component_cls = Component._all[component_name]
    slot_defs = getattr(component_cls, "_slots", {})

    for slot_name, slot_config in slot_defs.items():
        if slot_config.get("required") and not slots.has(slot_name):
            doc = slot_config.get("doc", "")
            doc_msg = f" ({doc})" if doc else ""
            raise template.TemplateSyntaxError(
                f"Component '{component_name}' requires slot '{slot_name}'{doc_msg}. "
                f"Add: {{% fill {slot_name} %}}...{{% endfill %}}"
            )


@register.tag("fill")
def do_fill(parser: Parser, token: Token):
    """
    Define slot content to fill in a parent component.

    Usage:
        {% fill header %}
            <h1>Title</h1>
        {% endfill %}

        {% fill item let:item let:index %}
            <li>{{ index }}. {{ item.name }}</li>
        {% endfill %}

    The let: syntax binds variables from the component's render_slot call.
    """
    bits = token.split_contents()
    tag_name = bits[0]

    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"'{tag_name}' tag requires a slot name. Usage: {{% {tag_name} slotname %}}...{{% endfill %}}"
        )

    slot_name = bits[1]
    # Remove quotes if present (support both quoted and unquoted)
    if len(slot_name) >= 2 and slot_name[0] in ('"', "'") and slot_name[-1] == slot_name[0]:
        slot_name = slot_name[1:-1]

    # Parse let:var syntax
    let_vars: list[str] = []
    for bit in bits[2:]:
        if bit.startswith("let:"):
            var_name = bit[4:]  # Remove "let:"
            if not var_name:
                raise template.TemplateSyntaxError(
                    f"'{tag_name}' let: syntax requires a variable name. Usage: {{% {tag_name} slotname let:varname %}}"
                )
            let_vars.append(var_name)
        else:
            raise template.TemplateSyntaxError(
                f"'{tag_name}' only accepts 'let:varname' after slot name, got '{bit}'. "
                f"Use let:varname to bind variables from the component. "
                f"Example: {{% {tag_name} item let:item let:index %}}"
            )

    nodelist = parser.parse(("endfill",))
    parser.delete_first_token()

    return FillNode(slot_name, nodelist, let_vars)


class FillNode(Node):
    """
    Node for {% fill %}...{% endfill %} tag.

    This node is only used for extraction by ComponentBlockNode.
    If rendered directly (outside a component block), it returns empty string.
    """

    def __init__(self, slot_name: str, nodelist: NodeList, let_vars: list[str]):
        self.slot_name = slot_name
        self.nodelist = nodelist
        self.let_vars = let_vars

    def render(self, context: Context) -> str:
        # FillNode outside a component block is a no-op
        return ""


@register.simple_tag(takes_context=True)
def render_slot(context, name: str = "", **extra_context):
    """
    Render a slot in the component template.

    Usage:
        {% render_slot %}                    {# Render default slot #}
        {% render_slot "header" %}           {# Render named slot #}
        {% render_slot "item" item=obj %}    {# With extra context for let: binding #}

    Args:
        name: Slot name (empty string for default slot)
        **extra_context: Variables to pass to slot (for let: binding)
    """
    slots: SlotContainer | None = context.get("slots")
    if not slots:
        return ""

    return slots.render_slot(context, name, extra_context)


@register.simple_tag(takes_context=True)
def on(context, _event_and_modifiers, _command, myself: bool = False, **kwargs: t.Any):
    """
    Bind an event handler to an element.

    Supports both string commands (server event handlers) and JS command
    builder objects (client-side commands).

    Args:
        _event_and_modifiers: Event name with optional modifiers (e.g., "click.prevent")
        _command: Handler name (string) or JS command builder
        myself: If True, target the current LiveComponent instead of parent.
                Required for events inside LiveComponent templates.

    Examples:
        {% on "click" "increment" %}
        {% on "click" "save" item_id=item.id %}
        {% on "click" JS().toggle("#modal") %}
        {% on "click" JS().push("save").hide() %}

        {# LiveComponent event targeting itself #}
        {% on "click" "increment" myself=True %}
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

    # Add target ID for LiveComponent @myself targeting
    if myself:
        kwargs["_target"] = component.id

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


@register.simple_tag
def upload_preview(entry, **attrs):
    """
    Render an image preview for an upload entry.

    This creates an img element that will display a preview of the selected
    image file before it's uploaded. The preview is generated client-side
    using a blob URL.

    Args:
        entry: UploadEntry object from uploads.{name}
        **attrs: Additional HTML attributes for the img element

    Example:
        {% for entry in this.uploads.images %}
            {% upload_preview entry class="w-32 h-32 object-cover" %}
        {% endfor %}

    Note: Preview only works for image files. Non-image files will show
    an empty img element or the alt text if provided.
    """
    # Build attributes
    attrs_parts = []
    for key, value in attrs.items():
        # Convert underscores to hyphens for HTML attributes
        html_key = key.replace("_", "-")
        attrs_parts.append(f'{html_key}="{value}"')
    attrs_str = " ".join(attrs_parts)

    # Get entry ref - support both UploadEntry objects and dicts
    ref = getattr(entry, "ref", None) or entry.get("ref", "") if isinstance(entry, dict) else ""
    upload_name = getattr(entry, "upload_name", None) or entry.get("upload_name", "") if isinstance(entry, dict) else ""

    return format_html(
        '<img wire-preview="{upload_name}:{ref}" {attrs} />',
        upload_name=upload_name,
        ref=ref,
        attrs=mark_safe(attrs_str),
    )


# Function component template tags


@register.simple_tag(takes_context=True)
def func(context, _name: str, **kwargs: t.Any):
    """
    Render a stateless function component.

    Function components are simpler than full components - they don't have
    WebSocket state and are purely for rendering reusable UI elements.

    Usage:
        {% func "button" text="Click me" variant="primary" %}
        {% func "icon" name="check" size=24 %}

    The function component must be registered with @function_component decorator.
    """
    fc = get_function_component(_name)
    return fc.render(kwargs)


@register.tag("func_block")
def do_func_block(parser: Parser, token: Token):
    """
    Block tag for rendering a function component with slots.

    Usage:
        {% func_block "card" title="Hello" %}
            {% fill header %}
                <h1>Custom Header</h1>
            {% endfill %}

            Default content here

            {% fill footer %}
                <button>Save</button>
            {% endfill %}
        {% endfunc %}
    """
    bits = token.split_contents()
    tag_name = bits[0]

    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"'{tag_name}' tag requires a component name. "
            f'Usage: {{% {tag_name} "name" attr=value %}}...{{% endfunc %}}'
        )

    component_name = bits[1]
    # Remove quotes if present
    if len(component_name) >= 2 and component_name[0] in ('"', "'") and component_name[-1] == component_name[0]:
        component_name = component_name[1:-1]

    # Parse remaining bits as kwargs
    remaining_bits = bits[2:]
    kwargs = token_kwargs(remaining_bits, parser)

    # Parse until {% endfunc %}
    nodelist = parser.parse(("endfunc",))
    parser.delete_first_token()  # consume {% endfunc %}

    return FuncBlockNode(component_name, kwargs, nodelist)


class FuncBlockNode(Node):
    """Node for {% func_block %}...{% endfunc %} block tag."""

    def __init__(
        self,
        component_name: str,
        kwargs: dict[str, t.Any],
        nodelist: NodeList,
    ):
        self.component_name = component_name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        # Get the function component
        fc = get_function_component(self.component_name)

        # Resolve kwargs
        resolved_kwargs = {}
        for key, value in self.kwargs.items():
            resolved_kwargs[key] = value.resolve(context)

        # Extract slots from nodelist
        slot_container = self._extract_slots(context)

        # Validate required slots
        self._validate_required_slots(fc, slot_container)

        # Render the component
        return fc.render(resolved_kwargs, slots=slot_container, context=context)

    def _extract_slots(self, context: Context) -> SlotContainer:
        """Extract slot definitions from the nodelist."""
        container = SlotContainer()
        default_nodes = NodeList()

        for node in self.nodelist:
            if isinstance(node, FillNode):
                if node.let_vars:
                    # Has let: bindings - keep nodelist for render-time binding
                    slot = Slot(
                        name=node.slot_name,
                        nodelist=node.nodelist,
                        let_vars=node.let_vars,
                    )
                else:
                    # No let: bindings - pre-render to capture parent context
                    rendered_content = node.nodelist.render(context)
                    slot = Slot(
                        name=node.slot_name,
                        nodelist=NodeList([TextNode(rendered_content)]),
                        let_vars=[],
                    )
                container.add(slot)
            else:
                # Default slot content
                default_nodes.append(node)

        # Only set default if there's actual content
        has_content = False
        for node in default_nodes:
            if hasattr(node, "s"):  # TextNode
                if node.s.strip():  # type: ignore[attr-defined]
                    has_content = True
                    break
            else:
                has_content = True
                break

        if has_content:
            rendered_default = default_nodes.render(context)
            container.set_default(NodeList([TextNode(rendered_default)]))

        return container

    def _validate_required_slots(
        self,
        fc: t.Any,
        slots: SlotContainer,
    ) -> None:
        """Validate that required slots are provided."""
        slot_defs = fc.slots

        for slot_name, slot_config in slot_defs.items():
            if slot_config.get("required") and not slots.has(slot_name):
                doc = slot_config.get("doc", "")
                doc_msg = f" ({doc})" if doc else ""
                raise template.TemplateSyntaxError(
                    f"Function component '{self.component_name}' "
                    f"requires slot '{slot_name}'{doc_msg}. "
                    f"Add: {{% fill {slot_name} %}}...{{% endfill %}}"
                )


# LiveComponent template tags


def _render_live_component(
    context: Context,
    name: str,
    kwargs: dict[str, t.Any],
    slots: SlotContainer | None = None,
) -> str:
    """Shared body of ``{% live_component %}`` and ``{% live_component_block %}``."""
    # Get parent component from context
    parent: Component | None = context.get("this")
    if parent is None:
        raise template.TemplateSyntaxError(
            "{% live_component %} must be used within a Component template. No parent component found in context."
        )

    # ID is required
    if "id" not in kwargs:
        raise template.TemplateSyntaxError(
            "{{% live_component %}} requires an 'id' attribute. "
            'Usage: {{% live_component "{name}" id="unique-id" %}}'.format(name=name)
        )

    # Get or create repository
    repo: ComponentRepository | None = context.get("wireview_repository")
    if repo is None:
        raise template.TemplateSyntaxError(
            "{% live_component %} requires a wireview_repository in context. "
            "This usually means it's not being rendered within a wireview component."
        )

    # Build (or look up) the LiveComponent and record that this render names it
    live_comp = repo.build_live_component(
        name=name,
        state=kwargs,
        parent_id=parent.id,
        slots=slots,
    )

    if not repo.is_live:
        # HTTP render: a dead render of the child, inline, like any nested component.
        # A halt here matters more than elsewhere: the child renders *into* the
        # parent's output, so refusing it after the fact would leave its HTML in
        # a response already on the wire (docs/design/live-session.md §3-5).
        if not _mount_for_http(live_comp, repo):
            return live_comp._render(repo) or ""
        if slots is not None:
            return live_comp._render_with_slots(repo, slots) or ""
        return live_comp._render(repo) or ""

    # Live render: the parent only names the child. The consumer runs the child's
    # joined()/update() after this template pass and ships the child's own diff in
    # the same render message (docs/design/live-component-ownership.md §3).
    from ..core.rendered import component_ref_marker
    from ..template_engine import get_template_marker

    index = get_template_marker().marker_context.next_index()
    return mark_safe(component_ref_marker(live_comp.id, index))


@register.tag("live_component_block")
def do_live_component_block(parser: Parser, token: Token):
    """
    Block form of ``{% live_component %}`` that passes slots to the child.

    Usage:
        {% live_component_block "Modal" id="m1" title="Hello" %}
            {% fill header %}<h1>{{ heading }}</h1>{% endfill %}
            Body content becomes the default slot
        {% endlive_component %}

    Fills without ``let:`` render in the parent's context during the parent's
    pass; the child receives their text. Fills with ``let:`` render when the
    child renders, bound to the values ``{% render_slot %}`` passes.
    """
    bits = token.split_contents()
    tag_name = bits[0]

    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            f"'{tag_name}' tag requires a component name. "
            f'Usage: {{% {tag_name} "ComponentName" id="..." %}}...{{% endlive_component %}}'
        )

    component_name = bits[1]
    if len(component_name) >= 2 and component_name[0] in ('"', "'") and component_name[-1] == component_name[0]:
        component_name = component_name[1:-1]

    kwargs = token_kwargs(bits[2:], parser)
    nodelist = parser.parse(("endlive_component",))
    parser.delete_first_token()

    return LiveComponentBlockNode(component_name, kwargs, nodelist)


class LiveComponentBlockNode(Node):
    """Node for {% live_component_block %}...{% endlive_component %}."""

    def __init__(self, component_name: str, kwargs: dict[str, t.Any], nodelist: NodeList):
        self.component_name = component_name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        resolved_kwargs = {key: value.resolve(context) for key, value in self.kwargs.items()}
        slots = _extract_slots(self.nodelist, context)
        _validate_required_slots(self.component_name, slots)
        return _render_live_component(context, self.component_name, resolved_kwargs, slots)


@register.simple_tag(takes_context=True)
def live_component(context, _name: str, **kwargs: t.Any):
    """
    Render a LiveComponent within a parent Component.

    LiveComponents are stateful nested components that maintain their own
    state while sharing the parent's WebSocket connection.

    Usage:
        {% live_component "Counter" id="counter-1" count=10 %}

    Requirements:
        - Must be used within a parent Component's template
        - 'id' attribute is required and must be unique

    The LiveComponent events use `myself=True` in {% on %} tags:
        <button {% on "click" "increment" myself=True %}>+1</button>

    To pass slots, use {% live_component_block %}.
    """
    return _render_live_component(context, _name, kwargs)


@register.simple_tag(takes_context=True)
def live_tag_header(context):
    """
    Generate the tag header for a LiveComponent.

    Similar to {% tag_header %} but includes LiveComponent-specific attributes.

    Usage (in LiveComponent template):
        <div {% live_tag_header %}>
            ...
        </div>
    """
    from ..live_component import LiveComponent as LC

    component: Component = context["this"]
    repo: ComponentRepository = context["wireview_repository"]

    # Check if this is actually a LiveComponent
    parent_id = ""
    if isinstance(component, LC):
        parent_id = component._parent_id or ""

    return format_html(
        (
            'id="{id}" data-name="{name}" data-state="{state}" '
            'data-is-live="{is_live}" data-parent="{parent_id}" '
            "wireview-component wireview-live"
        ),
        id=component.id,
        name=component._name,
        is_live=str(repo.is_live).lower(),
        state=_signed_state(component, repo),
        parent_id=parent_id,
    )
