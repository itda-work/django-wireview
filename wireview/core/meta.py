"""WireviewMeta class for managing component rendering and server communication."""

from __future__ import annotations

import difflib
import logging
import typing as t
from asyncio import iscoroutine
from functools import reduce

from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer
from django.shortcuts import resolve_url
from django.utils.html import format_html
from django.utils.safestring import SafeText, mark_safe

from .. import settings
from ..schemas import DomAction
from ..utils import db
from .rendered import Rendered, has_markers

log = logging.getLogger("wireview")

if t.TYPE_CHECKING:
    from django.db import models

    from ..features.streams import StreamOp
    from ..features.uploads import UploadOp
    from ..js import JS
    from ..slots import SlotContainer
    from .component import Component

if settings.USE_HMIN:
    try:
        from hmin.base import html_minify  # type: ignore
    except ImportError as e:
        raise ImportError("If you enable WIREVIEW['USE_HMIN'] you need to install django-hmin") from e
else:

    def html_minify(html: str) -> str:
        return html


# Type aliases
RedirectDestination = t.Union[t.Callable[..., t.Any], "models.Model", str]
HTMLDiff = list[str | int]
# New diff format: either legacy HTMLDiff or Phoenix-style dict
DiffPayload = HTMLDiff | dict[str, t.Any]
Context = dict[str, t.Any]
P = t.ParamSpec("P")

ScrollPosition = t.Literal["start"] | t.Literal["end"] | t.Literal["center"] | t.Literal["nearest"]


class Repo(t.Protocol):
    """Protocol for component repository."""

    pass


class WireviewMeta:
    """
    Manages component rendering state and server-client communication.

    This class handles:
    - HTML diff generation for efficient updates (Phoenix LiveView style)
    - URL navigation (redirect, replace, push)
    - DOM actions and scroll positioning
    - WebSocket message sending
    """

    _last_sent_html: list[str]
    _last_rendered: Rendered | None

    def __init__(
        self,
        params: dict[str, t.Any],
        channel_name: str | None = None,
        channel_layer: BaseChannelLayer | None = None,
    ):
        self.params = params
        self.channel_name = channel_name
        self.channel_layer = channel_layer
        self._is_frozen: bool = False
        self._redirected_to: str | None = None
        self._last_sent_html: list[str] = []
        self._last_rendered: Rendered | None = None
        self._skip_render: bool = False
        # Pending operations queue for joined() lifecycle
        self._pending_mode: bool = False
        self._pending_operations: list[tuple[str, dict[str, t.Any]]] = []
        # Pending broadcasts queue (separate from operations since they go through channel layer)
        self._pending_broadcasts: list[tuple[str, dict[str, t.Any]]] = []

    def clone(self) -> WireviewMeta:
        """Create a copy of this meta instance."""
        cloned = type(self)(
            params=self.params,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )
        # Don't copy render state - child components start fresh
        return cloned

    def skip_render(self) -> None:
        """Skip the next render cycle."""
        self._skip_render = True

    def force_render(self) -> None:
        """Force a full re-render on the next cycle."""
        self._skip_render = False
        self._last_sent_html = []
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
        """Actually send a broadcast via channel layer."""
        if self.channel_layer:
            await self.channel_layer.group_send(
                channel,
                {"type": "notification", "channel": channel, "kwargs": kwargs},
            )

    async def destroy(self, component_id: str) -> None:
        """Destroy a component and notify the client."""
        self.freeze()
        await self.send("remove", id=component_id)

    def freeze(self) -> None:
        """Freeze the component to prevent further rendering."""
        self._is_frozen = True

    async def redirect_to(self, to: RedirectDestination, **kwargs: t.Any) -> None:
        """Redirect the client to a new URL."""
        url = resolve_url(to, **kwargs)
        self._redirected_to = url
        if self.channel_name:
            self.freeze()
            await self.send("url_change", command="redirect", url=url)

    async def replace_to(self, to: RedirectDestination, **kwargs: t.Any) -> None:
        """Replace the current URL without navigation."""
        url = resolve_url(to, **kwargs)
        await self.send("url_change", command="replace", url=url)

    async def push_to(self, to: RedirectDestination, **kwargs: t.Any) -> None:
        """Push a new URL to browser history."""
        url = resolve_url(to, **kwargs)
        await self.send("url_change", command="push", url=url)

    async def render_diff(self, component: "Component", repo: Repo) -> DiffPayload | None:
        """
        Render the component and return a diff if changed.

        Uses Phoenix LiveView-style static/dynamic separation when markers
        are present, falling back to legacy line-based diff otherwise.

        This method resolves async properties in the async context first,
        then performs template rendering in a sync context. This avoids
        nested async_to_sync/sync_to_async transitions which cause
        performance degradation.

        Returns:
            - dict with 's', 'd', 'f' keys for full render
            - dict with numeric keys for partial updates
            - list (legacy format) for unmarked templates
            - None if no changes
        """
        if self._skip_render:
            self._skip_render = False
            return None

        # Resolve async properties in async context first to avoid
        # nested async_to_sync calls inside sync template rendering
        context = await self._get_context_async(component, repo)

        # Template rendering is sync (Django templates are synchronous)
        html = await db(self._render_with_context)(component, context)
        if not html:
            return None

        html_str = str(html)

        # Use Phoenix-style diff if markers are present
        if has_markers(html_str):
            return self._compute_rendered_diff(html_str)

        # Fall back to legacy line-based diff
        return self._compute_legacy_diff(html_str)

    def _compute_rendered_diff(self, html: str) -> dict[str, t.Any] | None:
        """Compute Phoenix-style static/dynamic diff."""
        rendered = Rendered.from_marked_html(html)
        diff = rendered.get_diff(self._last_rendered)

        if diff is None:
            return None

        self._last_rendered = rendered
        return diff.to_payload()

    def _compute_legacy_diff(self, html: str) -> HTMLDiff | None:
        """Compute legacy line-based diff (backward compatibility)."""
        html_tokens = html.split(" ")

        if self._last_sent_html == html_tokens:
            return None

        if not settings.USE_HTML_DIFF:
            self._last_sent_html = html_tokens
            return html_tokens  # type: ignore

        diff: HTMLDiff = []
        for x in difflib.ndiff(self._last_sent_html, html_tokens):
            indicator = x[0]
            if indicator == " ":
                diff.append(1)
            elif indicator == "+":
                diff.append(x[2:])
            elif indicator == "-":
                diff.append(-1)

        if diff:
            diff = reduce(compress_diff, diff[1:], diff[:1])

        self._last_sent_html = html_tokens
        return diff if diff else None

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

        html = None
        if not self.channel_name and self._redirected_to:
            html = format_html(
                '<meta http-equiv="refresh" content="0; url={url}">',
                url=self._redirected_to,
            )
        elif not (self._is_frozen or self._redirected_to) and html is None:
            template = component._get_template()
            context = self._get_context(component, repo, slots)
            # Use marker-injected rendering for efficient diffing
            # The template type from component matches what render_with_markers expects
            html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
            html = html_minify(html)
        if html:
            return mark_safe(html)
        return None

    async def send_dom_action(self, action: DomAction, id: str, html: str) -> None:
        """Send a DOM manipulation action to the client."""
        await self.send("dom_action", action=action.value, id=id, html=html)

    async def send_stream_op(self, op: "StreamOp") -> None:
        """Send a stream operation to the client."""
        await self.send("stream_op", **op.to_payload())

    async def send_upload_op(self, op: "UploadOp") -> None:
        """Send an upload operation to the client."""
        await self.send("upload_op", **op.to_payload())

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

    async def scroll_into_view(
        self,
        id: str,
        behavior: t.Literal["smooth"] | t.Literal["instant"] | t.Literal["auto"] = "auto",
        block: ScrollPosition = "start",
        inline: ScrollPosition = "nearest",
    ) -> None:
        """Scroll an element into view."""
        await self.send("scroll_into_view", id=id, behavior=behavior, block=block, inline=inline)

    async def deffer(self, _id: str, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
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
        """Send a command to a specific channel."""
        if self.channel_layer:
            await self.channel_layer.send(
                _channel,
                {
                    "type": "message_from_component",
                    "command": _command,
                    "kwargs": kwargs,
                },
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

    async def _get_context_async(self, component: "Component", repo: Repo) -> Context:
        """Build the template context asynchronously.

        This method resolves async properties (coroutines) directly using await,
        avoiding the need for async_to_sync which would create nested transitions.
        This is the preferred method for building context in async contexts.

        Args:
            component: The component to build context for.
            repo: The component repository.

        Returns:
            A dictionary containing the template context.
        """
        context: Context = {}

        for attr_name in dir(component):
            if not attr_name.startswith("_") and attr_name not in self._PYDANTIC_CLASS_ATTRS:
                attr = getattr(component, attr_name)
                if not callable(attr):
                    # Await coroutines directly - no async_to_sync needed
                    if iscoroutine(attr):
                        attr = await attr
                    context[attr_name] = attr

        return dict(
            context,
            this=component,
            wireview_repository=repo,
        )

    def _render_with_context(self, component: "Component", context: Context) -> SafeText | None:
        """Render template with pre-resolved context (sync).

        This method performs the actual template rendering with a context
        that has already had its async properties resolved. This avoids
        the need for async_to_sync during template rendering.

        Args:
            component: The component to render.
            context: Pre-resolved template context from _get_context_async().

        Returns:
            Rendered HTML as SafeText, or None if rendering should be skipped.
        """
        from ..template_engine import render_with_markers

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
        html = render_with_markers(template, context).strip()  # type: ignore[arg-type]
        html = html_minify(html)

        return mark_safe(html) if html else None

    def _get_context(
        self,
        component: "Component",
        repo: Repo,
        slots: "SlotContainer | None" = None,
    ) -> Context:
        """Build the template context for rendering (sync version).

        WARNING: This method uses async_to_sync for async properties, which
        can cause performance issues when called from within a sync_to_async
        context. Prefer using _get_context_async() in async contexts.

        This method is kept for backward compatibility with sync rendering
        paths (e.g., HTTP responses without WebSocket).

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
                attr = getattr(component, attr_name)
                if not callable(attr):
                    # Handle async properties that return coroutine objects
                    if iscoroutine(attr):
                        log.warning(
                            "Sync context detected while resolving async property '%s' "
                            "on component '%s'. This may cause performance issues. "
                            "Consider using _get_context_async() or pre-loading data "
                            "in joined().",
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


def compress_diff(diff: HTMLDiff, diff_item: str | int) -> HTMLDiff:
    """Compress consecutive same-type diff items."""
    if isinstance(diff_item, str) or isinstance(diff[-1], str):
        diff.append(diff_item)
    else:
        same_sign = not (diff[-1] > 0) ^ (diff_item > 0)
        if same_sign:
            diff[-1] += diff_item
        else:
            diff.append(diff_item)
    return diff
