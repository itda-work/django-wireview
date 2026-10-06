import asyncio
import logging
import re
import typing as t
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata

from asgiref.sync import async_to_sync, sync_to_async
from django import template
from django.template.base import Node, NodeList, Parser, TextNode, Token, token_kwargs
from django.template.context import Context
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import mark_safe

from .. import settings
from ..core.component import Component
from ..core.live_session import REQUEST_ATTR as LIVE_SESSION_REQUEST_ATTR
from ..core.live_session import declaration_allows, get_live_session
from ..core.rendered import inject_marker, marked_component_refs, nested_component_html
from ..core.shared_render import STATE_SLOT
from ..core.state import sign_state, signable_json
from ..event_transpiler import binding
from ..features.hooks import hook_files
from ..function_components import DRAWER as FUNCTION_DRAWER
from ..function_components import PAGE as FUNCTION_PAGE
from ..function_components import get_function_component
from ..live_component import LiveComponent
from ..repository import ComponentRepository
from ..slots import Slot, SlotContainer
from ..template_engine import drew, drew_component

register = template.Library()

log = logging.getLogger("wireview")


def _bundle_version() -> str:
    try:
        return metadata.version("django-wireview")
    except metadata.PackageNotFoundError:
        return ""


#: The bundle's cache key. It was a fixed ``?v=2``, so after an upgrade a browser
#: could keep the old bundle under the same URL until its cache expired.
BUNDLE_VERSION = _bundle_version()


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
        "BUNDLE_VERSION": BUNDLE_VERSION,
        "LIVE_SESSION": getattr(request, LIVE_SESSION_REQUEST_ATTR, "") if request is not None else "",
        # Every page carries every app's hooks, not the ones this page uses: a
        # boosted move replaces the body, so a script in the destination's head
        # never runs (docs/design/colocated-hooks.md §2). Settled at startup and
        # independent of the request, unlike the boundary name above.
        "HOOK_FILES": hook_files() if settings.COLLECT_HOOKS else (),
        # Django's CSP middleware (6.0+) puts a per-request nonce here; the
        # header's <style> needs it under a style-src without 'unsafe-inline'
        # and the scripts carry it for policies built on nonces (#90). Reading
        # it makes the middleware include it in the header. Absent on older
        # Django or without the middleware, and then nothing is added.
        "CSP_NONCE": getattr(request, "_csp_nonce", None) if request is not None else None,
        # How long the client waits before opening the socket again (#124)
        "RECONNECT": {
            "min_delay": settings.RECONNECT_MIN_DELAY_MS,
            "jitter": settings.RECONNECT_JITTER_MS,
            "max_delay": settings.RECONNECT_MAX_DELAY_MS,
            "grow_factor": settings.RECONNECT_GROW_FACTOR,
        },
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
    if repo.is_live and getattr(component.wire, "_state_slot", False):
        # A render many connections may take: each puts its own token here (#176).
        # This connection's is signed now, off the loop like any other: the state's
        # JSON may query (a QuerySet field, a computed field)
        component.wire._slot_token = sign_state(component)
        signed = STATE_SLOT
    else:
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
    # A sticky component's element is left alone by a boosted navigation's morph
    # (wireview-boost.js), so the instance, its DOM and its hooks carry over (#72).
    sticky = " wire-sticky" if component._meta.sticky else ""
    return format_html(
        (
            'id="{id}" data-name="{name}" data-state="{state}" data-is-live="{is_live}" '
            "wireview-component{sticky}{failed}"
        ),
        id=component.id,
        name=component._name,
        is_live=str(repo.is_live).lower(),
        state=_signed_state(component, repo),
        sticky=sticky,
        failed=_join_failed_mark(component, repo),
    )


def _join_failed_mark(component: Component, repo: ComponentRepository) -> str:
    """`` wire-join-failed`` on an instance a parent's pass built under the id of one whose join failed.

    The page shows it as ``wireview-error``, which the parent's patch would
    otherwise take away. An attribute and not ``class``: a template that writes
    its own ``class`` after the tag keeps it. Only such an instance changes its
    parent's static parts; every other render stays as it was.
    """
    return " wire-join-failed" if repo.is_live and repo.refused(component.id) else ""


def _hears_params_in_template(repo: ComponentRepository) -> bool:
    """Whether a template pass runs ``params_changed`` for what it builds, before drawing it.

    Only an HTTP render, and only with a query, as a join (``command_join``)
    hears one only then. On a socket the join and the parent's render settle it
    (``WireviewSession._render_tree``); a live pass draws inline what they will.
    """
    return not repo.is_live and bool(repo.params)


def _listens_to_params(component: Component) -> bool:
    """Whether hearing the query could change ``component``: if not, it needs no bridge for it.

    A ``handle_params`` hook an ``on_mount`` hook attaches is still heard: that
    component crosses the bridge for its hooks anyway.
    """
    hooks = getattr(component, "_lifecycle_hooks", None)
    return type(component).params_changed is not Component.params_changed or bool(
        isinstance(hooks, dict) and hooks.get("handle_params")
    )


async def _enter_in_template(
    component: Component, repo: ComponentRepository, serialize: t.Callable[[Component], t.Awaitable[str]]
) -> tuple[bool, str | None]:
    """Mount, then hear the page's query: Phoenix's mount -> handle_params, before the render.

    A LiveComponent's ``params_changed`` that raises is logged and the child
    still renders, as on a socket (``_render_tree``). Any other component's
    raises: its join would fail, and a page not yet sent fails louder.

    On an HTTP render nothing is connected to hear what ``start_async`` or
    ``assign_async`` would finish, so the work they started is cancelled before
    it runs: the page draws the loading state and the join starts it again, as
    Phoenix starts no async work on a dead render. Left alone it ran on the
    server's loop after the response, on an instance nothing would render.

    The HTML shows what the query made of the component, but ``data-state``
    carries the state from before it heard the query (``sign_state``): the join
    starts from the mounted state and hears the params again, as Phoenix's
    connected mount starts afresh rather than from the dead render's assigns.
    That state is serialized here, by ``serialize``, off this loop
    (``_mount_in_template`` says where): dumping a QuerySet or reading a
    computed field queries the database, which Django refuses on an event loop.

    Returns:
        Whether the component may be rendered, and the state before the query
        when it heard one.
    """
    unheard: str | None = None
    try:
        if not await component._mount(repo.params, repo.session):
            return False, None
        if _hears_params_in_template(repo):
            try:
                unheard = await serialize(component)
            except Exception as e:
                _snapshot_failed(component, e)
            uri = f"?{repo.get_query_string()}"
            if isinstance(component, LiveComponent):
                try:
                    await component._handle_params(dict(repo.params), uri)
                except Exception as e:
                    log.exception(f"Error in {component._name}.params_changed(): {e}")
            else:
                await component._handle_params(dict(repo.params), uri)
        return True, unheard
    finally:
        if not repo.is_live:
            component._cancel_async_tasks()


def _hold_back(component: Component, unheard: str) -> None:
    """Keep ``unheard`` for ``sign_state``, with the state the query left, on the template pass's thread.

    Taken after the bridge rather than inside it: here a state that queries the
    database to serialize is where ``{% tag_header %}`` will sign it, at no
    extra crossing, and a failure cannot hide one ``params_changed`` raised.
    """
    try:
        heard = signable_json(component)
    except Exception as e:
        _snapshot_failed(component, e)
        return
    component.wire._unheard_state = (unheard, heard)


def _snapshot_failed(component: Component, error: Exception) -> None:
    """A LiveComponent's failing snapshot is logged and it signs what it drew; any other component's raises.

    The same contract as a failing ``params_changed``. Called from an ``except``.
    """
    if not isinstance(component, LiveComponent):
        raise error
    log.exception(f"Error serializing {component._name}'s state: {error}")


async def _serialize_on_this_loop(component: Component) -> str:
    """``signable_json`` on the helper thread's loop: where the template pass signs, it is on a loop too."""
    return signable_json(component)


def _mount_in_template(component: Component, repo: ComponentRepository) -> bool:
    """Run a component's mount-time boundary from inside a template pass, and on an HTTP render its params.

    ``{% component %}`` builds its component and renders it **inline**, in the
    middle of whoever's template named it. That is the only seam there is, so
    the boundary has to be applied here or not at all -- and "not at all" ships
    the protected markup once and then refuses the join that follows, which is
    no boundary.

    Both renders come through here, and they used to be treated differently: the
    live path skipped the hooks entirely on the theory that the join had already
    covered the page. It had covered the *page*, not this component, so a page
    hook that meant to refuse one nested component never ran and the component
    became an event target as well as markup. A nested component mounts once per
    instance (``Component._mount`` is idempotent), so running it costs one bridge
    per instance rather than one per render.

    An HTTP render then runs ``params_changed`` with the page's query, once per
    instance, as a join does before its first render (#177). Without it a page
    opened at ``?q=...`` went out drawn as if it had no query, and only the
    join filled it in: a visitor without JavaScript, or a search engine, saw
    an empty page. A refused component hears nothing.

    Getting an async callback out of a synchronous template pass depends on
    which thread that pass runs on:

    - A sync view, a live render (whose template pass is inside
      ``database_sync_to_async``), or an async view whose pass is wrapped in
      ``sync_to_async``: a plain worker thread, where ``async_to_sync`` is the
      right bridge. The state before the query comes back to this thread to
      be serialized (``sync_to_async``), as the state after it is, where
      ``{% tag_header %}`` signs and a view under ``ATOMIC_REQUESTS`` holds
      its transaction.
    - An async view that calls ``render()`` directly renders on the event-loop
      thread itself, where ``async_to_sync`` refuses to run. Rather than fail
      the page, the hooks get a loop of their own on a helper thread. A hook
      that touches the ORM there opens its own connection, which is the same
      trade Django's own sync/async bridges make. The state before the query
      is serialized on that loop: ``{% tag_header %}`` signs on a loop as well,
      and a thread-sensitive executor could be waiting on the loop this blocks.

    A refusal freezes the component and answers ``False``, and the caller renders
    nothing for it. Freezing rather than skipping the render outright is what
    keeps a redirect working: ``WireviewMeta.render`` still turns the URL a hook
    queued into a ``<meta http-equiv="refresh">``, and emits nothing else -- no
    template output, no ``data-state``.

    Returns:
        Whether the component may be rendered.
    """
    if not declaration_allows(type(component), repo.live_session):
        # Answered without the bridge, because it needs no awaiting.
        repo.abandon(component)
        return False
    wire = component.wire
    if wire.has_mounted or wire.has_joined:
        # Already decided, and ``_mount`` would answer from the flag without
        # awaiting anything -- but the bridge would already have been paid for by
        # then. A parent re-renders far more often than it mounts, so the cheap
        # question has to be asked on this side of it.
        return not wire.mount_halted
    if (
        not component._meta.on_mount
        and repo.live_session is None
        and not (_hears_params_in_template(repo) and _listens_to_params(component))
    ):
        return True

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            mounted, unheard = async_to_sync(_enter_in_template)(component, repo, sync_to_async(signable_json))
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                mounted, unheard = pool.submit(
                    asyncio.run, _enter_in_template(component, repo, _serialize_on_this_loop)
                ).result()
        if unheard is not None:
            _hold_back(component, unheard)
    except Exception:
        # A crashing hook is a refusal here too, and the exception on its way out
        # is not the cleanup. On a live render it unwinds to the join, which
        # removes the *parent*; an instance left registered here would keep
        # answering events for a component whose guard never finished. A
        # ``params_changed`` that raises fails the page the same way.
        repo.abandon(component)
        raise

    if not mounted:
        repo.abandon(component)
    return mounted


def _sticky_default_id(component_class: type[Component]) -> str:
    """The id a sticky component rendered without one gets: the same on every page.

    A boosted navigation pairs a sticky element with the next page's by id. The
    per-render ``rx-<uuid>`` never pairs, so without this ``sticky = True`` was
    switched off in silence (#128). Derived from the fully qualified name, so two
    sticky classes that share a class name in different apps do not collide.
    """
    return "sticky-" + re.sub(r"[^A-Za-z0-9_-]+", "-", component_class._fqn)


def _default_sticky_id(component_name: str, repo: ComponentRepository) -> str | None:
    """A sticky component's id when the template gave none, or None to keep the random one.

    A second id-less instance of the same sticky class on one page would share
    the first one's id -- and ``repo.build`` would hand back the first instance.
    Only a page render (a fresh repository) can tell a second instance from a
    re-render of the first, so that is where it is caught: the second keeps a
    random id and does not stick, and the log says why and what to write.
    """
    component_class = Component._resolve(component_name)
    if not component_class._meta.sticky:
        return None
    sticky_id = _sticky_default_id(component_class)
    if not repo.is_live and sticky_id in repo.components:
        log.warning(
            "%s is sticky and rendered more than once on this page without an id; only the first "
            "one survives a boosted navigation. Give each one its own id: {%% component '%s' id=\"...\" %%}",
            component_class._fqn,
            component_name,
        )
        return None
    return sticky_id


def _page_repository(context: Context) -> ComponentRepository:
    """The page's repository, made in an HTTP render by the first tag that needs one.

    A function component's template has none of the page's names: there it is
    made in the context drawing the function, so the request's user, query and
    boundary reach it and the rest of the page shares it.
    """
    if (repo := context.get("wireview_repository")) is None:
        if (page := context.get(FUNCTION_PAGE)) is not None:
            repo = _page_repository(page)
        else:
            request = context.get("request")
            repo = ComponentRepository(
                is_live=False,
                user=context.get("user"),
                # From ``request.GET``, not the raw ``QUERY_STRING``: under WSGI that is
                # the bytes read as latin-1, so a query a client sent unencoded
                # (``?q=파이썬``) reached the components as mojibake. Django's
                # QueryDict decodes it with the request's encoding.
                params=ComponentRepository.decode_params(request.GET.dict()) if request is not None else {},
                session=getattr(request, "session", None),
                # The view decorator put the name here. Reading it off the request
                # rather than off a setting is what makes the boundary a property of
                # the page rather than of the project (#58).
                live_session=get_live_session(getattr(request, LIVE_SESSION_REQUEST_ATTR, "")) if request else None,
            )
        context["wireview_repository"] = repo
    return repo


def _build_and_render_component(
    context: Context,
    component_name: str,
    kwargs: dict[str, t.Any],
    slots: SlotContainer | None = None,
) -> str:
    """Helper function to build and render a component."""
    repo = _page_repository(context)

    if "id" not in kwargs and (sticky_id := _default_sticky_id(component_name, repo)):
        kwargs = {**kwargs, "id": sticky_id}
    # In a function component's template the drawer is the one that drew the function
    drawer = context.get("this")
    if drawer is None:
        drawer = context.get(FUNCTION_DRAWER)
    component_instance = repo.build(component_name, state=kwargs, drawer=drawer)
    if repo.is_live:
        # A pass with no fill forgets the last one: the next fill has nothing to line up with
        fills = component_instance.wire.fills
        slots = component_instance.wire.fills = slots.keeping_stale(fills) if slots is not None else None
    if slots is None:
        # No fill is no slot. The instance may be one another page's pass filled
        # (a boosted visit takes it over), and the slots it remembers are for its
        # own renders: falling back to them drew that page's slot here.
        component_instance.wire.slots = component_instance.wire.slots_from = None
    if not _mount_in_template(component_instance, repo):
        # Frozen: whatever comes back is a redirect meta or nothing at all.
        return component_instance._render(repo) or ""

    # This pass is the component's own as much as one of its own render is
    repo.begin_render(component_instance.id)
    # Use slot-aware rendering if slots are provided
    if slots is not None:
        component_instance.wire.slots_from = drawer
        html = component_instance._render_with_slots(repo, slots) or ""
    else:
        html = component_instance._render(repo) or ""
    if component_instance.wire.template_evaluated:
        repo.end_inline_pass(
            component_instance.id, drawer.id if drawer is not None else None, marked_component_refs(html)
        )
    # By id, so a render without the page's repository counts too (a function
    # component called from Python): the page joins what it drew under that id,
    # and that has not moved until it renders
    drew_component(component_instance)
    if repo.is_live and html and not component_instance.wire._rendered_own:
        # Its join's answer tells by this whether joined() drew something new
        component_instance.wire.passed = html
    if repo.is_live and html:
        # A fill holding this output keeps it as text; the slot's owner finds the
        # component by these marks and draws it as it is then (parsing drops them)
        return mark_safe(nested_component_html(component_instance.id, html))
    return html


@register.simple_tag(takes_context=True)
def wireview_toasts(context):
    """Put the toast receiver on the page: ``toast()`` and ``atoast()`` land here.

    Once per page, in the layout. It renders an empty hidden element; the
    messages go into the page's ``[wire-flash]`` container like any flash (#116).
    """
    from ..features.toasts import WireviewToasts

    return _build_and_render_component(context, WireviewToasts._fqn, {"id": "wireview-toasts"})


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
    slot_defs = component_cls._meta.slots

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
    # A part kept for a reset temporary assign shows the fill it drew (template_engine._PartNode)
    drew(("s", name, slots.drawn_key(name) if slots else None))
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
    from ..core.watched import stands_for
    from ..js import JS

    component: Component | None = context.get("this")
    # A Broadcast item renders once for every instance of its class (#178), so
    # its handlers are checked on the class. ``myself`` still reads the
    # instance's id, which the item cannot have, and raises.
    checked: Component | type[Component] | None = stands_for(component) or component
    assert checked, "Can't find a component in this context"

    # Validate handler for string commands (not JS objects)
    if isinstance(_command, str):
        _check_handler(checked, _command)
    elif isinstance(_command, JS):
        # Validate push events in JS commands reference valid handlers
        for cmd in _command._commands:
            if cmd.get("cmd") == "push":
                event_name = cmd.get("event")
                if event_name:
                    _check_handler(checked, event_name)

    # Add target ID for LiveComponent @myself targeting
    if myself:
        kwargs["_target"] = t.cast("Component", component).id

    name, value = binding(_event_and_modifiers, _command, kwargs)
    return format_html('{name}="{value}"', name=name, value=value)


def _check_handler(component: "Component | type[Component]", name: str) -> None:
    """Refuse a binding the dispatcher would refuse, while the page renders.

    The dispatcher takes only names ``is_client_callable`` allows. A binding to
    any other callable -- a framework method, one a minor release added under a
    handler's name, a ``_`` helper -- rendered fine and dropped every click
    with a log line.
    """
    from ..core.handlers import is_client_callable

    # Raised rather than asserted: ``python -O`` strips an assert, and the
    # binding went back to rendering and dropping every click.
    label = f"{(component if isinstance(component, type) else type(component)).__name__}.{name}"
    handler = getattr(component, name, None)
    if not handler:
        raise AssertionError(f"Missing handler: {label}")
    if not callable(handler):
        raise AssertionError(f"Not callable: {label}")
    if not is_client_callable(component, name):
        raise AssertionError(
            f"{label} is not an event handler: the framework owns the name, or it starts with '_'. "
            "Bind a method of your own with another name."
        )


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


class _Dotted(dict):
    """A dict whose keys read as attributes, the way a template's dots read them.

    The expression is Python, so ``forloop.counter0`` was an attribute lookup on
    Django's ``forloop`` dict and failed, as did ``item.title`` on a row from
    ``.values()`` -- the two things a class list inside a loop reaches for (#113).
    """

    def __getattr__(self, name: str) -> t.Any:
        try:
            return _dotted(self[name])
        except KeyError:
            raise AttributeError(name) from None


def _dotted(value: t.Any) -> t.Any:
    return _Dotted(value) if isinstance(value, dict) and not isinstance(value, _Dotted) else value


class CondNode(Node):
    def __init__(self, dict_expression):
        self.dict_expression = dict_expression

    def render(self, context):
        variables: dict[str, t.Any] = {name: _dotted(value) for name, value in context.flatten().items()}  # type: ignore
        terms = eval(self.dict_expression, variables)
        return " ".join(term for term, ok in terms.items() if ok)


class ClassNode(CondNode):
    def render(self, *args, **kwargs):
        text = super().render(*args, **kwargs)
        return f'class="{text}"'


# Upload template tags


def _html_attrs(attrs: dict[str, t.Any]) -> str:
    """Extra attributes from a tag's keyword arguments, every value escaped.

    Underscores become hyphens: a template keyword cannot contain one, and
    without this ``data_id=`` could never become ``data-id``.

    The upload tags used to join ``key="value"`` themselves and then either
    pass the result through ``format_html`` (escaping the quotes, so
    ``class="btn"`` arrived as ``class=&quot;btn&quot;``) or ``mark_safe`` it
    (so a value the client chose, like an upload's file name, could close the
    attribute and add an event handler).
    """
    return format_html_join(
        " ",
        '{}="{}"',
        ((key.replace("_", "-"), value) for key, value in attrs.items()),
    )


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

    accept = ",".join(config.accept) if config.accept else ""
    multiple = "multiple" if config.max_entries > 1 else ""

    # No inline handler: the client picks up changes on [wire-upload] inputs
    # from its delegated listener, which a Content Security Policy allows (#90).
    return format_html(
        '<input type="file" wire-upload="{name}" accept="{accept}" {multiple} {attrs}>',
        name=name,
        accept=accept,
        multiple=multiple,
        attrs=_html_attrs(attrs),
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


@register.simple_tag
def upload_button(name: str):
    """
    Return the attribute that makes an element open the file picker.

    Args:
        name: Upload field name (matches allow_upload name)

    Example:
        <button type="button" {% upload_button "images" %} class="btn">Select images</button>

    Like ``upload_drop_zone``, an attribute on an element you write. It rendered
    an opening ``<button>`` whose closing tag the template had to supply (#119).
    """
    return format_html('wire-upload-select="{name}"', name=name)


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
    # Support both UploadEntry objects and dicts. The conditional expression
    # this replaces bound as ``(a or b) if is_dict else ""`` and so gave every
    # UploadEntry an empty ref, a preview that never found its file.
    if isinstance(entry, dict):
        ref, upload_name = entry.get("ref", ""), entry.get("upload_name", "")
    else:
        ref, upload_name = getattr(entry, "ref", ""), getattr(entry, "upload_name", "")

    return format_html(
        '<img wire-preview="{upload_name}:{ref}" {attrs} />',
        upload_name=upload_name,
        ref=ref,
        attrs=_html_attrs(attrs),
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
    return fc.render(kwargs, context=context)


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

    if slots is not None and repo.is_live and (existing := repo.components.get(kwargs["id"])) is not None:
        slots = slots.keeping_stale(existing.wire.fills)

    # Build (or look up) the LiveComponent and record that this render names it
    live_comp = repo.build_live_component(
        name=name,
        state=kwargs,
        parent_id=parent.id,
        slots=slots,
    )
    if repo.is_live:
        live_comp.wire.fills = slots

    if not repo.is_live:
        # HTTP render: a dead render of the child, inline, like any nested component.
        # A refusal here matters more than elsewhere: the child renders *into* the
        # parent's output, so refusing it after the fact would leave its HTML in
        # a response already on the wire (docs/design/live-session.md §3-5).
        if not _mount_in_template(live_comp, repo):
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
            "wireview-component wireview-live{failed}"
        ),
        id=component.id,
        name=component._name,
        is_live=str(repo.is_live).lower(),
        state=_signed_state(component, repo),
        parent_id=parent_id,
        failed=_join_failed_mark(component, repo),
    )
