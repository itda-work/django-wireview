"""WireviewMeta class for managing component rendering and server communication."""

from __future__ import annotations

import difflib
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

if t.TYPE_CHECKING:
    from django.db import models
    from django.utils.safestring import SafeString

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
    - HTML diff generation for efficient updates
    - URL navigation (redirect, replace, push)
    - DOM actions and scroll positioning
    - WebSocket message sending
    """

    _last_sent_html: list[str]

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
        self._skip_render: bool = False

    def clone(self) -> WireviewMeta:
        """Create a copy of this meta instance."""
        return type(self)(
            params=self.params,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )

    def skip_render(self) -> None:
        """Skip the next render cycle."""
        self._skip_render = True

    def force_render(self) -> None:
        """Force a full re-render on the next cycle."""
        self._skip_render = False
        self._last_sent_html = []

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

    async def render_diff(self, component: "Component", repo: Repo) -> HTMLDiff | None:
        """Render the component and return a diff if changed."""
        if self._skip_render:
            self._skip_render = False
        else:
            html = await db(self.render)(component, repo)
            if html and self._last_sent_html != (html := html.split(" ")):
                if settings.USE_HTML_DIFF:
                    diff: HTMLDiff = []
                    for x in difflib.ndiff(self._last_sent_html, html):
                        indicator = x[0]
                        if indicator == " ":
                            diff.append(1)
                        elif indicator == "+":
                            diff.append(x[2:])
                        elif indicator == "-":
                            diff.append(-1)

                    if diff:
                        diff = reduce(compress_diff, diff[1:], diff[:1])
                else:
                    diff = html  # type: ignore
                self._last_sent_html = html
                return diff
        return None

    def render(self, component: "Component", repo: Repo) -> None | SafeText:
        """Render the component to HTML."""
        html = None
        if not self.channel_name and self._redirected_to:
            html = format_html('<meta http-equiv="refresh" content="0; url={url}">', url=self._redirected_to)
        elif not (self._is_frozen or self._redirected_to) and html is None:
            template = component._get_template()
            context = self._get_context(component, repo)
            html = template.render(context).strip()
            html = html_minify(html)
        if html:
            return mark_safe(html)
        return None

    async def send_dom_action(self, action: DomAction, id: str, html: "SafeString") -> None:
        """Send a DOM manipulation action to the client."""
        await self.send("dom_action", action=action.value, id=id, html=html)

    async def scroll_into_view(
        self,
        id: str,
        behavoir: t.Literal["smooth"] | t.Literal["instant"] | t.Literal["auto"] = "auto",
        block: ScrollPosition = "start",
        inline: ScrollPosition = "nearest",
    ) -> None:
        """Scroll an element into view."""
        await self.send("scroll_into_view", id=id, behavoir=behavoir, block=block, inline=inline)

    async def deffer(self, _id: str, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
        """Defer a function call to be executed later."""
        await self.send("dispatch_event", command=_f.__name__, id=_id, args=args, kwargs=kwargs)

    async def send(self, _command: str, **kwargs: t.Any) -> None:
        """Send a command to the current channel."""
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

    def _get_context(self, component: "Component", repo: Repo) -> Context:
        """Build the template context for rendering."""
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
                        attr = _run_coro(attr)
                    context[attr_name] = attr
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
