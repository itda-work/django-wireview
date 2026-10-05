"""WireviewMeta class for managing component rendering and server communication."""

from __future__ import annotations

import inspect
import itertools
import logging
import secrets
import typing as t
import weakref
from asyncio import iscoroutine
from types import FunctionType

from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer
from django.shortcuts import resolve_url
from django.utils.html import format_html
from django.utils.safestring import SafeText, mark_safe

from .. import telemetry
from ..utils import db
from .render_gate import RenderGate
from .render_reads import RenderReads
from .rendered import Rendered, keep_stale, page_drawing, strip_markers
from .transport import Broker, ChannelsBroker, NullBroker

log = logging.getLogger("wireview")

if t.TYPE_CHECKING:
    from django.db import models

    from ..features.streams import StreamOp
    from ..features.uploads import UploadOp
    from ..js import JS
    from ..slots import SlotContainer
    from .component import Component
    from .live_session import LiveSession

# Orders instances' births and their own renders across the process; only compared
_TICKS = itertools.count()

#: Per component class, per set of instance attributes: the public names a live
#: render reads into its context (``WireviewMeta._context_names``)
_CONTEXT_NAMES: weakref.WeakKeyDictionary[type, dict[tuple[str, ...], tuple[str, ...]]] = weakref.WeakKeyDictionary()
#: A plain method, a classmethod or a staticmethod: read on an instance it is always callable
_METHODS = (FunctionType, classmethod, staticmethod)

# Type aliases
RedirectDestination = t.Union[t.Callable[..., t.Any], "models.Model", str]
# A render's diff: a full render {"s", "d", "f"} or a partial {"<index>": value}
DiffPayload = dict[str, t.Any]
Context = dict[str, t.Any]
P = t.ParamSpec("P")

ScrollPosition = t.Literal["start"] | t.Literal["end"] | t.Literal["center"] | t.Literal["nearest"]


def resolve_destination(to: RedirectDestination, **kwargs: t.Any) -> str:
    """Where a navigation is headed, as a URL.

    ``resolve_url`` reverses any string without a ``/`` or a ``.`` in it, so a
    destination that is only a query string -- ``"?page=2"``, the natural way to
    say "this page, a different query", and what ``params_changed``'s own
    docstring shows -- would raise ``NoReverseMatch``. Same for a bare fragment.
    Everything else keeps ``resolve_url``'s behaviour: a view name, a model with
    ``get_absolute_url``, a path.
    """
    if isinstance(to, str) and to[:1] in ("?", "#"):
        return to
    return resolve_url(to, **kwargs)


class Repo(t.Protocol):
    """Protocol for component repository."""

    is_live: bool
    # The diff protocol version the connection's client speaks (``?vsn=``).
    vsn: int


#: What a component's code may use on ``self.wire`` (docs/COMPATIBILITY.md, #99).
#: The rest of this class is the framework's plumbing; a component reaches that
#: behaviour through Component's own methods (put_flash, push_js, defer ...).
PUBLIC_MEMBERS = frozenset({"params", "redirect_to", "replace_to", "push_to"})


class WireviewMeta:
    """
    Manages component rendering state and server-client communication.

    This class handles:
    - HTML diff generation for efficient updates (Phoenix LiveView style)
    - URL navigation (redirect, replace, push)
    - DOM actions and scroll positioning
    - WebSocket message sending

    Only ``PUBLIC_MEMBERS`` are public API.
    """

    _last_rendered: Rendered | None

    def __init__(
        self,
        params: dict[str, t.Any],
        channel_name: str | None = None,
        channel_layer: BaseChannelLayer | None = None,
        broker: Broker | None = None,
        connection_id: str | None = None,
        live_session: "LiveSession | None" = None,
    ):
        self.params = params
        self.channel_name = channel_name
        self.channel_layer = channel_layer
        # Identifies the WebSocket connection that owns this component, minted by
        # the consumer. Unlike ``channel_name`` it is safe to put in a URL, and it
        # is what scopes upload registries, tokens and progress groups (#77).
        self.connection_id = connection_id
        # The page boundary this component was mounted inside, or None when the
        # page declared none (#58). It decides what ``sign_state`` writes into the
        # envelope and which hooks ``Component._mount`` runs before the component's
        # own, so every path that builds a component has to carry it.
        self.live_session = live_session
        # Tells this instance from another under the same component id (#137).
        # The session drops an upload op sent by an instance that has since left
        # or been replaced by a new join, and the page takes an upload config only
        # from the instance it holds under the id: the first render of each
        # instance names it (``instance_announced``), and the config carries it.
        # Random, not counted: a number has to stay unique within the connection
        # even when the session moves to another process (GAP-027), and a
        # per-process counter starts over there (#141). 53 bits keeps it a safe
        # integer in JavaScript, where the page compares it with ``===``.
        self.instance: int = secrets.randbits(53)
        self.instance_announced: bool = False
        if broker is None:
            broker = ChannelsBroker(channel_layer) if channel_layer is not None else NullBroker()
        self.broker: Broker = broker
        self._is_frozen: bool = False
        self._redirected_to: str | None = None
        self._last_rendered: Rendered | None = None
        self._skip_render: bool = False
        # Holds the component's background work while a worker thread renders it (#138)
        self._render_gate = RenderGate()
        # Whether the last render evaluated the template. False when the render
        # was skipped, frozen or redirected; the consumer only settles nested
        # LiveComponents after a render that actually ran the template, and a
        # pass drawn within another's ends only then (``repo.end_pass``).
        self.template_evaluated: bool = False
        # Set once joined() has run for this instance. A join for an id whose
        # instance already joined means new DOM arrived for it (boost navigation):
        # that instance leaves and a fresh one joins, so joined() stays once per
        # instance.
        self.has_joined: bool = False
        # Set once the _on_mount hooks have run for this instance. The hooks are a
        # mount-time boundary (authentication, tracking), so they run once per
        # instance no matter how many render passes name the component.
        self.has_mounted: bool = False
        # Whether the last _on_mount run ended in {"halt": True}. Kept so a repeat
        # call to Component._mount() answers the same way it did the first time.
        self.mount_halted: bool = False
        # Slot content the enclosing template passed, without markers, so a render
        # the component does on its own (render_diff) still fills its slots.
        self.slots: SlotContainer | None = None
        # The component whose template pass gave those slots, and when this
        # instance came to be: a join under the id takes the slots over only from
        # a page whose filler came after it (ComponentRepository.retire).
        self.slots_from: Component | None = None
        # The slot content that pass gave, markers kept: the next pass keeps what
        # it drew of a reset temporary assign (SlotContainer.keeping_stale)
        self.fills: SlotContainer | None = None
        # What each part of its renders of its own drew from elsewhere -- a
        # nested component as it was then, a slot's content -- by the part's
        # key, and the record a render under way builds: a part kept for a
        # reset temporary assign is drawn again when one of those moved on
        # (template_engine._PartNode). Only a component with temporary assigns records.
        self.drawn: dict[tuple[t.Any, ...], frozenset[tuple[t.Any, ...]]] = {}
        self.drawing: dict[tuple[t.Any, ...], set[tuple[t.Any, ...]]] | None = None
        self.born: int = next(_TICKS)
        # When a render of its own last put something new on the page; 0 until
        # then. Another component's kept part that drew it before then is old --
        # whether or not the signed state moved with it. Its first, the join's
        # answer, counts only when it drew other than the last pass did
        # (``passed``, that pass's output, ``data-state`` included): joined() may
        # have changed a field -- its own or one it passes a nested component,
        # shown or not -- or loaded a temporary assign.
        self.moved: int = 0
        self._rendered_own: bool = False
        self.passed: str | None = None
        # When this instance last rendered on its own (render_diff), or ``born``.
        # A slot's owner puts back what it drew of this instance last while this
        # has not moved since (slots._NestedComponentNode).
        self.own_render: int = self.born
        # Pending operations queue for joined() lifecycle
        self._pending_mode: bool = False
        self._pending_operations: list[tuple[str, dict[str, t.Any]]] = []
        # Pending broadcasts queue (separate from operations since they go through channel layer)
        self._pending_broadcasts: list[tuple[str, dict[str, t.Any]]] = []
        # Last ``data-state`` token this component issued: (state_json, token, issued_at).
        # ``sign_state`` reuses it while the state is unchanged so an unchanged
        # render keeps producing the same attribute value (see wireview/core/state.py).
        self._state_token: tuple[str, str, float] | None = None
        # On an HTTP render that heard the query: (state before params_changed,
        # state after it). ``sign_state`` signs the first while the instance is
        # still in the second, and drops it once it signs another, so the join
        # starts from the mounted state and hears the params again, as Phoenix's
        # connected mount starts afresh (#177).
        self._unheard_state: tuple[str, str] | None = None

    def clone(self) -> WireviewMeta:
        """Create a copy of this meta instance."""
        cloned = type(self)(
            params=self.params,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
            broker=self.broker,
            connection_id=self.connection_id,
            live_session=self.live_session,
        )
        # Don't copy render state - child components start fresh
        return cloned

    def skip_render(self) -> None:
        """Skip the next render cycle."""
        self._skip_render = True

    def force_render(self) -> None:
        """Force a full re-render on the next cycle."""
        self._skip_render = False
        self._last_rendered = None

    def enter_pending_mode(self) -> None:
        """Enter pending mode to queue operations during joined().

        When in pending mode, all send() operations are queued instead of
        being sent immediately. This ensures that operations like stream(),
        push_js(), etc. are sent after the initial render is complete.

        Call flush_pending() after send_render() to send all queued operations.
        """
        self._pending_mode = True

    async def flush_pending(self) -> None:
        """Flush all pending operations after render is complete.

        This should be called after send_render() to ensure all operations
        queued during joined() are sent in the correct order.
        """
        self._pending_mode = False
        for command, kwargs in self._pending_operations:
            await self._do_send(command, **kwargs)
        self._pending_operations.clear()

        # Flush pending broadcasts
        for channel, kwargs in self._pending_broadcasts:
            await self._send_broadcast(channel, **kwargs)
        self._pending_broadcasts.clear()

    async def queue_broadcast(self, channel: str, **kwargs: t.Any) -> None:
        """Queue or send a broadcast.

        If in pending mode (during joined()), the broadcast is queued and will
        be sent after all subscriptions are ready. Otherwise, sends immediately.

        Args:
            channel: The channel name to broadcast to.
            **kwargs: Additional keyword arguments to include in the notification.
        """
        if self._pending_mode:
            self._pending_broadcasts.append((channel, kwargs))
        else:
            await self._send_broadcast(channel, **kwargs)

    async def _send_broadcast(self, channel: str, **kwargs: t.Any) -> None:
        """Publish a notification to every session subscribed to ``channel``."""
        message = {"type": "notification", "channel": channel, "kwargs": kwargs}
        with telemetry.span(telemetry.broadcast_published, sender=type(self.broker), topic=channel) as span:
            span.measure(message)
            await self.broker.publish(channel, message)

    async def destroy(self, component_id: str) -> None:
        """Destroy a component and notify the client."""
        self.freeze()
        await self.send("remove", id=component_id)

    def freeze(self) -> None:
        """Freeze the component to prevent further rendering."""
        self._is_frozen = True

    async def redirect_to(self, to: RedirectDestination, /, **kwargs: t.Any) -> None:
        """Redirect the client to a new URL.

        Always a page fetch, even to the page's own path: the way to move a page
        whose template reads the query outside its components, which a patch
        (``push_to``) would leave showing the old one (#169).
        """
        url = resolve_destination(to, **kwargs)
        self._redirected_to = url
        if self.channel_name:
            self.freeze()
            await self.send("url_change", command="redirect", url=url)

    async def replace_to(self, to: RedirectDestination, /, **kwargs: t.Any) -> None:
        """``push_to`` in place of the current history entry (#169).

        The page's own path is a patch; another path is fetched in place.
        """
        url = resolve_destination(to, **kwargs)
        await self.send("url_change", command="replace", url=url)

    async def push_to(self, to: RedirectDestination, /, **kwargs: t.Any) -> None:
        """Push a new URL to browser history.

        To the page's own path -- another query or fragment -- it is a patch
        (Phoenix's ``push_patch``, #169): nothing is fetched, and the page's
        components hear ``params_changed`` on the instances they are. Another
        path is fetched and its components join fresh (``push_navigate``); out
        of the live_session, as a full page load. The client decides, from the
        address bar it is at (navigation.mjs).
        """
        url = resolve_destination(to, **kwargs)
        await self.send("url_change", command="push", url=url)

    async def push_title(self, title: str) -> None:
        """Update the page title dynamically.

        Args:
            title: The new page title to display
        """
        await self.send("title", title=title)

    async def put_flash(
        self,
        flash_type: str,
        message: str,
        *,
        timeout: int = 5000,
        dismissible: bool = True,
    ) -> None:
        """Display a flash message on the client.

        Args:
            flash_type: Message type (e.g., "success", "error", "info", "warning")
            message: The message text to display
            timeout: Auto-dismiss timeout in milliseconds (0 = no auto-dismiss)
            dismissible: Whether the message can be manually dismissed
        """
        await self.send(
            "flash",
            flash_type=flash_type,
            message=message,
            timeout=timeout,
            dismissible=dismissible,
        )

    async def clear_flash(self, flash_id: str | None = None) -> None:
        """Clear flash message(s).

        Args:
            flash_id: Specific flash ID to clear, or None to clear all
        """
        await self.send("clear_flash", flash_id=flash_id)

    async def render_diff(self, component: "Component", repo: Repo) -> DiffPayload | None:
        """
        Render the component and return a diff if changed.

        Uses Phoenix LiveView-style static/dynamic separation. HTML without
        markers (django-hmin strips them) is one static part, so any change to
        it is a full render (#99 removed the token diff that used to cover it).

        This method resolves async properties in the async context first,
        then performs template rendering in a sync context. This avoids
        nested async_to_sync/sync_to_async transitions which cause
        performance degradation.

        Returns:
            - dict with 's', 'd', 'f' keys for full render
            - dict with numeric keys for partial updates
            - None if no changes
        """
        self.template_evaluated = False
        self.own_render = next(_TICKS)
        first, self._rendered_own = not self._rendered_own, True
        if self._skip_render:
            self._skip_render = False
            return None

        with telemetry.span(
            telemetry.component_rendered,
            sender=type(component),
            component_id=component.id,
            component_name=component._name,
            live=True,
        ) as render_span:
            # Temporary assigns reset after the last render and not assigned since:
            # what reads them renders as it did then (#111)
            stale = component._stale_temporaries()
            reads = RenderReads(id(component), stale, type(component).model_fields) if stale else None

            # Properties are read and the template rendered off the event loop,
            # where a property may query the database (#120). An async property
            # is awaited back on the loop between the two, and only then does
            # the render take a second trip. The loop runs on meanwhile, so the
            # component's own background work waits for the render to finish:
            # a step of it in between signed data-state from one state and drew
            # the body from another (#138).
            with self._render_gate.rendering():
                context, html, pending = await db(self._collect_and_render)(component, repo, reads)
                if pending:
                    with self._render_gate.awaiting_properties(f"{component._name} ({component.id})"):
                        await self._await_properties(context)
                    html = await db(self._render_with_context)(component, context, reads)
            if not html:
                return None

            html_str = str(html)
            render_span.measure(html_str)

        with telemetry.span(
            telemetry.diff_computed,
            sender=type(component),
            component_id=component.id,
            component_name=component._name,
        ) as diff_span:
            diff = self._compute_rendered_diff(html_str, repo.vsn, reads.slots if reads else ())
            diff_span.annotate(changed=diff is not None)
            diff_span.measure(diff)

        if first:
            passed, self.passed = self.passed, None
            if passed is not None and page_drawing(passed) != page_drawing(html_str):
                self.moved = next(_TICKS)
        elif diff is not None:
            self.moved = next(_TICKS)
        return diff

    def _compute_rendered_diff(self, html: str, vsn: int = 0, stale: t.Collection[int] = ()) -> dict[str, t.Any] | None:
        """Compute Phoenix-style static/dynamic diff in the forms protocol ``vsn`` allows.

        ``stale`` names the parts that read a reset temporary assign; they keep
        the previous render's value.
        """
        rendered = Rendered.from_marked_html(html, stale)
        rendered.settle(self._last_rendered)
        diff = rendered.get_diff(self._last_rendered, vsn)

        if diff is None:
            return None

        self._last_rendered = rendered
        return diff.to_payload()

    def render(
        self,
        component: "Component",
        repo: Repo,
        slots: "SlotContainer | None" = None,
    ) -> None | SafeText:
        """Render the component to HTML with automatic marker injection.

        Args:
            component: The component to render.
            repo: The component repository.
            slots: Optional slot container with slot content for composition.

        Returns:
            Rendered HTML as SafeText, or None if rendering should be skipped.
        """
        from ..template_engine import render_with_markers

        with telemetry.span(
            telemetry.component_rendered,
            sender=type(component),
            component_id=component.id,
            component_name=component._name,
            live=repo.is_live,
        ) as span:
            html = None
            self.template_evaluated = False
            if not self.channel_name and self._redirected_to:
                html = format_html(
                    '<meta http-equiv="refresh" content="0; url={url}">',
                    url=self._redirected_to,
                )
            elif not (self._is_frozen or self._redirected_to) and html is None:
                template = component._get_template()
                self.template_evaluated = True
                if slots is not None:
                    self.slots = slots.without_markers()
                elif self.slots is not None:
                    slots = self.slots
                if repo.is_live and self._last_rendered is not None and (stale := component._stale_temporaries()):
                    html = self._render_keeping(component, repo, template, slots, stale)
                else:
                    context = self._get_context(component, repo, slots)
                    # Use marker-injected rendering for efficient diffing
                    # The template type from component matches what render_with_markers expects
                    html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
                if not repo.is_live:
                    # HTTP render: markers inside attributes (value="<!--$0-->…") would
                    # corrupt the page until the WebSocket join replaces the DOM.
                    html = strip_markers(html)
            span.measure(html)
        if html:
            return mark_safe(html)
        return None

    def _render_keeping(
        self,
        component: "Component",
        repo: Repo,
        template: t.Any,
        slots: "SlotContainer | None",
        stale: frozenset[str],
    ) -> str:
        """Draw the component in another component's pass, keeping what its reset temporary assigns drew.

        A nested ``{% component %}`` is drawn again whenever the component
        around it renders, and that render's diff puts the drawing on the page
        in place of the component's own. Its temporary assigns were reset after
        its own render, so the parts that read only them take back what that
        render drew -- as its own next render would (#111). Without it the list
        its last event loaded left the page on any render of the host.
        """
        from ..template_engine import get_template_marker, render_with_markers

        reads = RenderReads(id(component), stale, type(component).model_fields)
        context = self._get_context(component, repo, slots, reads)
        marker = get_template_marker()
        # A fill the pass drew before this component holds markers numbered below its own
        first = marker.next_index()
        with marker.tracking(reads):
            html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
        # A LiveComponent a kept part names stays named in the pass's render, as on the page
        return keep_stale(html, reads.slots, self._last_rendered, first, lambda: marker.marker_context.skip(1))

    async def send_stream_op(self, op: "StreamOp", owner: str | None = None) -> None:
        """Send a stream operation to the client, on behalf of component ``owner``.

        The page looks for the ``wire-stream`` container inside the owner's element
        and not inside a component nested in it, so two components can name their
        streams alike. Without an owner it takes the first container of that name
        on the page.
        """
        if owner is None:
            await self.send("stream_op", **op.to_payload())
        else:
            await self.send("stream_op", **op.to_payload(), id=owner)

    async def send_upload_op(self, op: "UploadOp", owner: str) -> None:
        """Send an upload operation to the client, on behalf of component ``owner``.

        The mail names the instance that sent it. It may reach the session after
        that instance left or a new join replaced it under the same id -- a
        config goes out from a task, through the channel layer -- and the session
        drops it then, before anything reaches the page. A config the session
        forwards names the instance to the page too (#137).
        """
        await self.send("upload_op", **op.to_payload(), owner=owner, instance=self.instance)

    async def push_js(self, component_id: str, js: "JS") -> None:
        """
        Send JS commands to be executed on the client.

        Args:
            component_id: The ID of the target component element
            js: JS command builder instance

        Example:
            from wireview.js import JS
            await self.push_js(self.wire_id, JS().set_value("input[name=content]", ""))
        """
        import json

        commands = json.loads(js.to_json())
        await self.send("exec_js", id=component_id, commands=commands)

    async def _send_push_event(
        self,
        component_id: str,
        event: str,
        payload: dict[str, t.Any],
        hook_id: str | None = None,
    ) -> None:
        """Push an event to client-side JavaScript hooks.

        Args:
            component_id: The ID of the component containing the hooks
            event: Event name to dispatch
            payload: Event data
            hook_id: Target specific hook (None = broadcast to all)
        """
        await self.send(
            "push_event",
            component_id=component_id,
            event=event,
            payload=payload,
            hook_id=hook_id,
        )

    async def scroll_into_view(
        self,
        id: str,
        behavior: t.Literal["smooth"] | t.Literal["instant"] | t.Literal["auto"] = "auto",
        block: ScrollPosition = "start",
        inline: ScrollPosition = "nearest",
    ) -> None:
        """Scroll an element into view."""
        await self.send("scroll_into_view", id=id, behavior=behavior, block=block, inline=inline)

    async def defer(self, _id: str, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
        """Defer a function call to be executed later."""
        await self.send("dispatch_event", command=_f.__name__, id=_id, args=args, kwargs=kwargs)

    async def send(self, _command: str, **kwargs: t.Any) -> None:
        """Send a command to the current channel.

        If in pending mode (during joined() lifecycle), the command is queued
        and will be sent when flush_pending() is called after render.
        """
        if self._pending_mode:
            self._pending_operations.append((_command, kwargs))
        else:
            await self._do_send(_command, **kwargs)

    async def _do_send(self, _command: str, **kwargs: t.Any) -> None:
        """Actually send a command to the current channel."""
        if self.channel_name:
            await self.send_to(self.channel_name, _command, **kwargs)

    async def send_to(self, _channel: str, _command: str, **kwargs: t.Any) -> None:
        """Send a command to a specific session (channel name)."""
        await self.broker.send_to_session(
            _channel,
            {
                "type": "message_from_component",
                "command": _command,
                "kwargs": kwargs,
            },
        )

    async def send_to_parent(
        self,
        parent_id: str,
        event: str,
        kwargs: dict[str, t.Any],
    ) -> None:
        """Send an event from LiveComponent to its parent Component.

        Args:
            parent_id: The ID of the parent component
            event: Event name (method name on parent)
            kwargs: Event arguments
        """
        await self.send(
            "dispatch_event",
            id=parent_id,
            command=event,
            args=[],
            kwargs=kwargs,
        )

    # Pydantic v2 class-level attributes that should not be accessed on instances
    _PYDANTIC_CLASS_ATTRS = frozenset(
        {
            "model_fields",
            "model_computed_fields",
            "model_config",
            "model_extra",
            "model_fields_set",
        }
    )

    def _collect_and_render(
        self, component: "Component", repo: Repo, reads: RenderReads | None = None
    ) -> tuple[Context, SafeText | None, bool]:
        """Read the context and render it in one trip off the event loop.

        Returns ``(context, html, pending)``. When an async property left a
        coroutine in the context, ``pending`` is true and nothing is rendered:
        the caller awaits it on the loop and renders then.
        """
        context = self._collect_context(component, repo, reads)
        if any(iscoroutine(value) for value in context.values()):
            return context, None, True
        return context, self._render_with_context(component, context, reads), False

    @staticmethod
    async def _await_properties(context: Context) -> None:
        for name, value in context.items():
            if iscoroutine(value):
                context[name] = await value

    @staticmethod
    def _read(component: "Component", attr_name: str, reads: RenderReads | None) -> t.Any:
        """``component``'s attribute ``attr_name``; with ``reads``, sorted stale or other."""
        if reads is None:
            return getattr(component, attr_name)
        # A property computed from stale fields alone is stale too
        with reads:
            slot = reads.open()
            attr = getattr(component, attr_name)
            reads.close(slot)
        if attr_name not in reads.stale and attr_name not in reads.other:
            (reads.stale if slot.stale and not slot.other else reads.other).add(attr_name)
        return attr

    @classmethod
    def _context_names(cls, component: "Component") -> tuple[str, ...]:
        """The public names of ``component`` a render reads, in ``dir()`` order, but its methods.

        ``dir()`` lists a few hundred names, most of them methods: each read
        built a bound method only to find it callable, about 15 µs a render
        (#176). Which names those are depends on the class and on the names
        the instance holds itself -- one there hides a method of the same name
        -- so the list is kept per class and per instance attribute set.
        """
        klass = type(component)
        keys = tuple(component.__dict__)
        by_keys = _CONTEXT_NAMES.get(klass)
        if by_keys is None:
            by_keys = _CONTEXT_NAMES[klass] = {}
        names = by_keys.get(keys)
        if names is None:
            own = set(keys)
            names = tuple(
                name
                for name in dir(component)
                if not name.startswith("_")
                and name not in cls._PYDANTIC_CLASS_ATTRS
                and (name in own or not isinstance(inspect.getattr_static(klass, name, None), _METHODS))
            )
            if len(by_keys) < 32:  # instances that come and go with attributes of their own: read as they are
                by_keys[keys] = names
        return names

    def _collect_context(self, component: "Component", repo: Repo, reads: RenderReads | None = None) -> Context:
        """Read every public attribute of the component into a context (sync).

        Async properties are left as coroutines for the caller to await.
        """
        context: Context = {}

        if reads is None:
            for attr_name in self._context_names(component):
                attr = getattr(component, attr_name)
                if not callable(attr):
                    context[attr_name] = attr
        else:
            # Every name is read: the reads sort the methods too (_read)
            for attr_name in dir(component):
                if not attr_name.startswith("_") and attr_name not in self._PYDANTIC_CLASS_ATTRS:
                    attr = self._read(component, attr_name, reads)
                    if not callable(attr):
                        context[attr_name] = attr

        from ..slots import SlotContainer

        context["slots"] = self.slots if self.slots is not None else SlotContainer()

        return dict(
            context,
            this=component,
            wireview_repository=repo,
        )

    def _render_with_context(
        self, component: "Component", context: Context, reads: RenderReads | None = None
    ) -> SafeText | None:
        """Render template with pre-resolved context (sync).

        This method performs the actual template rendering with a context
        that has already had its async properties resolved. This avoids
        the need for async_to_sync during template rendering.

        Args:
            component: The component to render.
            context: Template context from _collect_context(), its async
                properties already awaited (see render_diff()).

        Returns:
            Rendered HTML as SafeText, or None if rendering should be skipped.
        """
        from ..template_engine import get_template_marker, render_with_markers

        if not self.channel_name and self._redirected_to:
            return mark_safe(
                format_html(
                    '<meta http-equiv="refresh" content="0; url={url}">',
                    url=self._redirected_to,
                )
            )

        if self._is_frozen or self._redirected_to:
            return None

        template = component._get_template()
        self.template_evaluated = True
        # Its renders of its own are what its kept parts take back (settle), so
        # only they record what those parts drew
        self.drawing = {} if component._meta.temporary_assigns else None
        marker_context = get_template_marker().marker_context
        if self.drawing is not None:
            marker_context.recording += 1  # the parts look their owner up only while one records
        try:
            if reads is None:
                html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
            else:
                with reads:
                    html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
        finally:
            drawing, self.drawing = self.drawing, None
            if drawing is not None:
                marker_context.recording -= 1
        if drawing is not None:
            # A part this render did not run -- inside one it kept -- keeps its record
            self.drawn = {**self.drawn, **{key: frozenset(items) for key, items in drawing.items()}}

        return mark_safe(html) if html else None

    def _get_context(
        self,
        component: "Component",
        repo: Repo,
        slots: "SlotContainer | None" = None,
        reads: RenderReads | None = None,
    ) -> Context:
        """Build the template context for rendering (sync version).

        Used by render(), the synchronous render (the HTTP response among
        others). An async property is resolved here with async_to_sync, one
        event-loop trip per property. The live render,
        render_diff(), reads the context off the loop with _collect_context()
        and awaits async properties on the loop instead.

        Args:
            component: The component to build context for.
            repo: The component repository.
            slots: Optional slot container with slot content for composition.

        Returns:
            A dictionary containing the template context.
        """
        from ..slots import SlotContainer

        context: Context = {}

        def _run_coro(coro: t.Coroutine) -> t.Any:
            """Helper to run a coroutine object synchronously."""

            async def awaiter():
                return await coro

            return async_to_sync(awaiter)()

        for attr_name in dir(component):
            if not attr_name.startswith("_") and attr_name not in self._PYDANTIC_CLASS_ATTRS:
                attr = self._read(component, attr_name, reads)
                if not callable(attr):
                    # Handle async properties that return coroutine objects
                    if iscoroutine(attr):
                        log.warning(
                            "Sync context detected while resolving async property '%s' "
                            "on component '%s'. The synchronous render awaits it with "
                            "async_to_sync, which may cause performance issues. Consider "
                            "a plain property, or pre-loading the data in joined().",
                            attr_name,
                            type(component).__name__,
                        )
                        attr = _run_coro(attr)
                    context[attr_name] = attr

        # Add slots to context (use empty container if not provided)
        context["slots"] = slots if slots is not None else SlotContainer()

        return dict(
            context,
            this=component,
            wireview_repository=repo,
        )
