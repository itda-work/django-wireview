"""Component base class for wireview."""

from __future__ import annotations

import typing as t
from uuid import uuid4

from channels.layers import get_channel_layer
from django.apps import apps
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.http import HttpRequest
from django.template import loader
from django.utils.safestring import SafeString
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator, validate_call

from .. import settings, utils
from ..async_result import AsyncResult
from ..schemas import DomAction, ModelAction
from ..utils import db
from .meta import Repo, WireviewMeta

if t.TYPE_CHECKING:
    from ..features.uploads import (
        ConsumedUpload,
        ExternalUploadCallback,
        UploadEntry,
        UploadRegistry,
    )
    from ..js import JS
    from ..slots import SlotContainer

# Type aliases
ComponentState = dict[str, t.Any]
MessagePayload = dict[str, t.Any]
P = t.ParamSpec("P")


class Template(t.Protocol):
    """Protocol for Django template (both raw and backend-wrapped)."""

    def render(
        self,
        context: dict[str, t.Any] | None = ...,
        request: HttpRequest | None = ...,
    ) -> str: ...


# Also support backend templates that have a .template attribute
class BackendTemplate(t.Protocol):
    """Protocol for Django template backend wrappers."""

    template: "Template"

    def render(
        self,
        context: dict[str, t.Any] | None = ...,
        request: HttpRequest | None = ...,
    ) -> str: ...


# Union type for any template-like object
AnyTemplate = Template | BackendTemplate


__all__ = ("Component", "ComponentNotFound", "MessagePayload", "broadcast", "abroadcast")


def broadcast(channel: str, **kwargs: t.Any) -> None:
    """Broadcast a notification to a channel.

    This is the synchronous version. Use `abroadcast()` for async contexts
    like Component methods (joined, mutation, notification, etc.)

    Args:
        channel: The channel name to broadcast to.
        **kwargs: Additional keyword arguments to include in the notification.
    """
    utils.send_to(channel, type="notification", kwargs=kwargs)


async def abroadcast(channel: str, **kwargs: t.Any) -> None:
    """Broadcast a notification to a channel asynchronously.

    This is the async version of `broadcast()`. Use this in async contexts
    like Component methods (joined, mutation, notification, etc.)

    Args:
        channel: The channel name to broadcast to.
        **kwargs: Additional keyword arguments to include in the notification.

    Example:
        class MyComponent(Component):
            async def joined(self):
                await abroadcast("my-channel", action="joined", user=self.user.username)

            async def notification(self, channel: str, **kwargs):
                # Handle broadcast notifications
                if kwargs.get("action") == "joined":
                    self.online_users.append(kwargs.get("user"))
    """
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        channel,
        {"type": "notification", "channel": channel, "kwargs": kwargs},
    )


class Component(BaseModel):
    """
    Base class for wireview components.

    Components are Pydantic models that can be rendered to HTML and
    updated in real-time via WebSocket.
    """

    __name__: str

    _all: t.ClassVar[dict[str, t.Type["Component"]]] = {}
    _by_fqn: t.ClassVar[dict[str, t.Type["Component"]]] = {}
    _by_app: t.ClassVar[dict[str, t.Type["Component"]]] = {}
    _urls: t.ClassVar[dict] = {}
    _name: t.ClassVar[str]
    _template_name: t.ClassVar[str]
    _templates: t.ClassVar[dict[str, AnyTemplate]] = {}
    _fqn: t.ClassVar[str]

    # fields to exclude from the component state during serialization
    _exclude_fields: t.ClassVar[set[str]] = {"user", "wire"}

    # Subscriptions: you can define here which channels this component is subscribed to
    _subscriptions: t.ClassVar[set[str]] = set()

    # Temporary assigns: fields that are reset to their default values after each render.
    # This is useful for large collections that only need to be in memory during rendering.
    # Similar to Phoenix LiveView's temporary_assigns option.
    #
    # Example:
    #     class MessageList(Component):
    #         _temporary_assigns = {"messages"}
    #         messages: list[Message] = []
    #
    #         async def joined(self):
    #             self.messages = await Message.objects.all()[:100]
    #             # After rendering, self.messages will be reset to []
    _temporary_assigns: t.ClassVar[set[str]] = set()

    # Slot definitions: defines expected slots for this component.
    # Used for validation and documentation.
    #
    # Example:
    #     class Card(Component):
    #         _slots = {
    #             "header": {"required": True, "doc": "Card header content"},
    #             "footer": {"required": False, "doc": "Optional footer"},
    #         }
    _slots: t.ClassVar[dict[str, dict[str, t.Any]]] = {}

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _load_django_models(cls, data: dict[str, t.Any]) -> dict[str, t.Any]:
        """Auto-load Django Model/QuerySet instances from PKs."""
        if not isinstance(data, dict):
            return data

        for field_name, field_info in cls.model_fields.items():
            if field_name not in data:
                continue

            value = data[field_name]
            if value is None:
                continue

            field_type = field_info.annotation

            # Handle Model fields: load from PK
            try:
                if isinstance(field_type, type) and issubclass(field_type, models.Model):
                    if not isinstance(value, field_type):
                        data[field_name] = field_type.objects.filter(pk=value).first()
            except TypeError:
                pass

            # Handle QuerySet fields: deserialize from dict
            if isinstance(value, dict) and "app" in value and "model" in value:
                model_class = apps.get_model(value["app"], value["model"])
                data[field_name] = model_class.objects.filter(pk__in=value.get("ids", []))

        return data

    @field_serializer("*", mode="wrap")
    def _serialize_django_types(self, value: t.Any, handler: t.Callable) -> t.Any:
        """Serialize Django Model and QuerySet instances."""
        if isinstance(value, models.Model):
            return value.pk
        if isinstance(value, models.QuerySet):
            return {
                "app": value.model._meta.app_label,
                "model": value.model._meta.model_name,
                "ids": list(value.values_list("pk", flat=True)),
            }
        return handler(value)

    def __init_subclass__(cls: t.Type["Component"], name: str | None = None, public: bool = True) -> None:
        if public:
            import warnings

            name = name or cls.__name__
            fqn = f"{cls.__module__}.{name}"

            # Extract app name: 'myapp.live' -> 'myapp'
            app_name = cls.__module__.split(".")[0]
            app_key = f"{app_name}:{name}"

            # Warn about name collisions
            if name in cls._all and cls._all[name] is not cls:
                existing = cls._all[name]
                warnings.warn(
                    f"Component name '{name}' conflicts: "
                    f"{existing._fqn} vs {fqn}. "
                    f"Use FQN or app prefix to disambiguate.",
                    UserWarning,
                    stacklevel=2,
                )

            # Register in all registries
            cls._all[name] = cls  # 'Counter'
            cls._by_fqn[fqn] = cls  # 'myapp.live.Counter'
            cls._by_app[app_key] = cls  # 'myapp:Counter'

            # Component name and fully qualified name
            cls._name = name
            cls._fqn = fqn

        for attr_name in vars(cls):
            attr = getattr(cls, attr_name)
            if not attr_name.startswith("_") and attr_name.islower() and callable(attr):
                try:
                    setattr(
                        cls,
                        attr_name,
                        validate_call(config={"arbitrary_types_allowed": True})(attr),
                    )
                except (NameError, TypeError):
                    # Skip validation for methods with unresolvable type hints
                    pass

        super().__init_subclass__()

    @classmethod
    def _resolve(cls, name: str) -> t.Type["Component"]:
        """Resolve component by name, FQN, or app prefix.

        Resolution order:
        1. FQN (e.g., 'myapp.live.Counter')
        2. App prefix (e.g., 'myapp:Counter')
        3. Simple name (e.g., 'Counter')

        Args:
            name: Component name in any supported format.

        Returns:
            The component class.

        Raises:
            ComponentNotFound: If no component matches the given name.
        """
        # 1. FQN lookup (exact module path)
        if name in cls._by_fqn:
            return cls._by_fqn[name]

        # 2. App prefix lookup (app:Name)
        if ":" in name and name in cls._by_app:
            return cls._by_app[name]

        # 3. Simple name lookup (backward compatible)
        if name in cls._all:
            return cls._all[name]

        raise ComponentNotFound(f"Component '{name}' not found. " f"Available: {list(cls._all.keys())}")

    @classmethod
    def _build(
        cls,
        _component_name: str,
        state: ComponentState,
        params: dict[str, t.Any],
        user: AnonymousUser | AbstractBaseUser | None = None,
        channel_name: str | None = None,
        channel_layer=None,
    ) -> "Component":
        """Build a component instance from state."""
        component_class = cls._resolve(_component_name)

        instance = component_class.new(
            user=user or AnonymousUser(),
            wire=WireviewMeta(
                params=params,
                channel_name=channel_name,
                channel_layer=channel_layer,
            ),
            **state,
        )
        return instance

    @classmethod
    def _get_template(cls, template_name: str | None = None) -> AnyTemplate:
        """Get the template for this component."""
        template_name = template_name or cls._template_name
        if settings.DEBUG:
            return loader.get_template(template_name)  # type: ignore[return-value]
        else:
            if (template := cls._templates.get(template_name)) is None:
                template = loader.get_template(template_name)  # type: ignore[assignment]
                cls._templates[template_name] = template
            return template

    # State
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: AnonymousUser | AbstractBaseUser
    wire: WireviewMeta

    @classmethod
    def new(cls, **kwargs: t.Any) -> "Component":
        """Create a new component instance."""
        return cls(**kwargs)

    async def joined(self) -> None:
        """Called when the component joins the page."""
        ...

    async def leaving(self) -> None:
        """Called when the component is about to leave.

        This is called when:
        - The WebSocket connection is closed (browser close, navigation, network loss)
        - The component is explicitly destroyed

        Use this hook to perform cleanup operations like:
        - Broadcasting presence "left" notifications
        - Releasing external resources
        - Persisting state

        Example:
            class ChatRoom(Component):
                async def leaving(self):
                    await self.broadcast(
                        f"room.{self.room.id}.presence",
                        action="left",
                        username=self.username,
                    )
        """
        ...

    async def mutation(self, channel: str, action: ModelAction, instance: t.Any) -> None:
        """Called when a model mutation is broadcast."""
        ...

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Called when a notification is broadcast."""
        ...

    async def params_changed(self, params: dict[str, str], uri: str) -> None:
        """Called when URL parameters change.

        This callback is automatically invoked when:
        - push_to() or replace_to() is called and the client updates the URL
        - Browser back/forward navigation occurs
        - Initial page load with URL parameters (after joined())

        Args:
            params: URL query parameters as a dict (e.g., {"page": "2", "sort": "name"})
            uri: Full URI including query string (e.g., "/products?page=2&sort=name")

        Example:
            class ProductList(Component):
                page: int = 1
                sort: str = "created_at"
                products: list[Product] = []

                async def params_changed(self, params, uri):
                    self.page = int(params.get("page", "1"))
                    self.sort = params.get("sort", "created_at")
                    self.products = await self.fetch_products()

                async def next_page(self):
                    # URL change triggers params_changed automatically
                    await self.wire.push_to(f"?page={self.page + 1}")
        """
        ...

    async def handle_hook_event(
        self,
        hook_id: str,
        event: str,
        payload: dict[str, t.Any],
    ) -> t.Any:
        """Handle events from client-side JavaScript hooks.

        Override this method to process events sent from hooks via pushEvent().
        The return value will be sent back to the hook's callback function.

        Args:
            hook_id: Unique identifier of the hook instance
            event: Event name sent by the hook
            payload: Event data from the hook

        Returns:
            Response data to send back to the hook callback (None if no response)

        Example:
            class Dashboard(Component):
                async def handle_hook_event(self, hook_id, event, payload):
                    if event == "chart_click":
                        point = payload.get("point")
                        await self.handle_point_click(point)
                        return {"handled": True}
                    elif event == "validate":
                        return {"valid": self.validate(payload.get("value"))}
                    return None
        """
        return None

    async def push_event(
        self,
        event: str,
        payload: dict[str, t.Any] | None = None,
        hook_id: str | None = None,
    ) -> None:
        """Push an event to client-side JavaScript hooks.

        Sends an event that can be received by hooks using handleEvent().
        If hook_id is None, the event is broadcast to all hooks in this component.

        Args:
            event: Event name to dispatch
            payload: Event data (default: empty dict)
            hook_id: Target specific hook instance (None = broadcast to all)

        Example:
            # In component method
            async def update_chart(self):
                await self.push_event("update_data", {"values": [1, 2, 3, 4, 5]})

            # On client hook
            # this.handleEvent("update_data", ({values}) => {
            #     this.chart.data.datasets[0].data = values;
            #     this.chart.update();
            # });
        """
        await self.wire._send_push_event(self.id, event, payload or {}, hook_id)

    async def destroy(self) -> None:
        """Destroy this component."""
        await self.wire.destroy(self.id)

    async def send_render(self) -> None:
        """Request a re-render of this component."""
        await self.wire.send("send_render", id=self.id)

    async def focus_on(self, selector: str) -> None:
        """Focus on an element matching the selector."""
        await self.wire.send("focus_on", selector=selector)

    async def scroll_into_view(
        self,
        element_id: str,
        behavior: t.Literal["smooth", "instant", "auto"] = "auto",
        block: t.Literal["start", "end", "center", "nearest"] = "start",
        inline: t.Literal["start", "end", "center", "nearest"] = "nearest",
    ) -> None:
        """Scroll an element into view.

        Args:
            element_id: The ID of the element to scroll to
            behavior: Scroll animation - "smooth", "instant", or "auto"
            block: Vertical alignment - "start", "center", "end", or "nearest"
            inline: Horizontal alignment - "start", "center", "end", or "nearest"
        """
        await self.wire.scroll_into_view(element_id, behavior, block, inline)

    async def push_title(self, title: str) -> None:
        """Update the page title dynamically.

        Changes the browser's document.title to the specified value.
        Useful for updating the title based on component state.

        Args:
            title: The new page title to display

        Example:
            class ProductDetail(Component):
                product: Product

                async def joined(self):
                    await self.push_title(f"{self.product.name} - My Store")
        """
        await self.wire.push_title(title)

    async def put_flash(
        self,
        flash_type: str,
        message: str,
        *,
        timeout: int = 5000,
        dismissible: bool = True,
    ) -> None:
        """Display a flash message to the user.

        Flash messages are temporary notifications that appear on the page
        and automatically dismiss after a timeout.

        Args:
            flash_type: Message type - "success", "error", "info", or "warning"
            message: The message text to display
            timeout: Auto-dismiss time in ms (default: 5000, 0 = no auto-dismiss)
            dismissible: Whether user can manually dismiss (default: True)

        Example:
            class ProductForm(Component):
                async def save(self):
                    try:
                        await Product.objects.acreate(name=self.name)
                        await self.put_flash("success", "Product saved!")
                    except Exception as e:
                        await self.put_flash("error", f"Failed: {e}")
        """
        await self.wire.put_flash(flash_type, message, timeout=timeout, dismissible=dismissible)

    async def clear_flash(self, flash_id: str | None = None) -> None:
        """Clear flash message(s).

        Args:
            flash_id: Specific flash ID to clear, or None to clear all

        Example:
            await self.clear_flash()  # Clear all flashes
            await self.clear_flash("flash-123")  # Clear specific flash
        """
        await self.wire.clear_flash(flash_id)

    # DOM operations

    def skip_render(self) -> None:
        """Skip the next render cycle."""
        self.wire.skip_render()

    def force_render(self) -> None:
        """Force a full re-render on the next cycle."""
        self.wire.force_render()

    async def push_js(self, js: "JS") -> None:
        """
        Send JS commands to be executed on the client.

        Args:
            js: JS command builder instance

        Example:
            from wireview.js import JS
            await self.push_js(JS().set_value("input[name=search]", ""))
        """
        await self.wire.push_js(self.id, js)

    async def deffer(self, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
        """Defer a function call to be executed later."""
        await self.wire.deffer(self.id, _f, *args, **kwargs)

    # Broadcasting

    async def broadcast(self, channel: str, **kwargs: t.Any) -> None:
        """Broadcast a notification to a channel.

        This method is pending-aware: when called during joined(), the broadcast
        is queued and sent after all subscriptions are registered. This ensures
        that other components in the same WebSocket connection receive the
        notification.

        For broadcasts outside of component context, use the module-level
        `abroadcast()` function instead.

        Args:
            channel: The channel name to broadcast to.
            **kwargs: Additional keyword arguments to include in the notification.

        Example:
            class ChatRoom(Component):
                async def joined(self):
                    # This broadcast will be queued and sent after all
                    # components have subscribed to their channels
                    await self.broadcast(
                        f"room.{self.room.id}.presence",
                        action="joined",
                        username=self.username,
                    )

                async def leaving(self):
                    # This broadcast is sent immediately since we're not
                    # in pending mode
                    await self.broadcast(
                        f"room.{self.room.id}.presence",
                        action="left",
                        username=self.username,
                    )
        """
        await self.wire.queue_broadcast(channel, **kwargs)

    # Async operations

    async def assign_async(
        self,
        coro: t.Coroutine[t.Any, t.Any, t.Any],
        *,
        on_error: t.Callable[[Exception], None] | None = None,
    ) -> "AsyncResult[t.Any]":
        """
        Execute an async operation and track its loading/result/error state.

        This method immediately returns an AsyncResult in loading state,
        schedules the coroutine to run, and when complete, updates the
        result and triggers a re-render.

        Args:
            coro: The coroutine to execute
            on_error: Optional callback for error handling

        Returns:
            An AsyncResult that will be updated when the operation completes

        Example:
            class Dashboard(Component):
                stats: AsyncResult[Stats] = None

                async def joined(self):
                    self.stats = await self.assign_async(self.load_stats())

                async def load_stats(self):
                    return await Stats.objects.aget()

            # In template:
            {% if stats.loading %}Loading...{% endif %}
            {% if stats.ok %}{{ stats.result }}{% endif %}
            {% if stats.failed %}Error: {{ stats.error_message }}{% endif %}
        """
        import asyncio

        # Create initial loading state
        result: AsyncResult[t.Any] = AsyncResult.loading_state()

        async def run_and_update() -> None:
            nonlocal result
            try:
                value = await coro
                result.state = AsyncResult.success(value).state
                result.result = value
            except Exception as e:
                result.state = AsyncResult.failure(e).state
                result.error = e
                if on_error:
                    on_error(e)
            finally:
                # Trigger re-render
                await self.send_render()

        # Schedule the task to run
        asyncio.create_task(run_and_update())

        return result

    def freeze(self) -> None:
        """Freeze the component to prevent further rendering."""
        self.wire.freeze()

    async def dom(
        self,
        _action: DomAction,
        _id: str,
        _component_class_or_template_name: t.Type["Component"] | str,
        **kwargs: t.Any,
    ) -> None:
        """Perform a DOM manipulation action."""
        if isinstance(_component_class_or_template_name, str):
            template = self._get_template(_component_class_or_template_name)
            html = await db(template.render)(kwargs)
        else:
            from ..repository import ComponentRepository

            component = _component_class_or_template_name.new(
                wire=self.wire.clone(),
                user=self.user,
                **kwargs,
            )
            html = await db(component._render)(
                ComponentRepository(
                    is_live=False,
                    user=self.user,
                    params=self.wire.params,
                )
            )
        if html is not None:
            await self.wire.send_dom_action(_action, _id, html)

    # Stream operations

    async def stream(
        self,
        name: str,
        items: t.Iterable[t.Any],
        *,
        template: str | None = None,
        dom_id: t.Callable[[t.Any], str] | None = None,
        limit: int = 0,
    ) -> None:
        """
        Initialize or reset a stream with items.

        Streams provide memory-efficient handling of large lists by rendering
        items individually and sending them to the client via WebSocket.

        Args:
            name: Stream name (matches wire-stream attribute in template)
            items: Iterable of items to render
            template: Template name for rendering items
                     (default: {component_template}_item.html)
            dom_id: Function to generate DOM ID for each item
                   (default: {name}-{item.pk})
            limit: Maximum number of items to keep in DOM (0 = no limit).
                  When exceeded, oldest items are removed automatically.

        Example:
            async def joined(self):
                await self.stream("items", Item.objects.all()[:100])

            # With limit - keeps only 50 most recent items in DOM
            async def joined(self):
                await self.stream("messages", messages, limit=50)
        """
        from ..features.streams import StreamItem, StreamOp

        template_name = template or self._get_stream_item_template()
        dom_id_fn = dom_id or (lambda item: f"{name}-{item.pk}")

        stream_items = []
        for item in items:
            html = await self._render_stream_item(template_name, item)
            stream_items.append(StreamItem(dom_id=dom_id_fn(item), html=html))

        op = StreamOp(op="reset", stream=name, items=stream_items, limit=limit)
        await self.wire.send_stream_op(op)

    async def stream_insert(
        self,
        name: str,
        item: t.Any,
        *,
        at: int = -1,
        template: str | None = None,
        dom_id: t.Callable[[t.Any], str] | None = None,
        limit: int = 0,
    ) -> None:
        """
        Insert an item into a stream.

        Args:
            name: Stream name (matches wire-stream attribute)
            item: Item to insert
            at: Insert position (-1 = append, 0 = prepend, n = at index)
            template: Template name for rendering item
            dom_id: Function to generate DOM ID
            limit: Maximum items to keep in DOM (0 = no limit).
                  When exceeded, items are removed from the opposite end.

        Example:
            async def add_item(self, name: str):
                item = await Item.objects.acreate(name=name)
                await self.stream_insert("items", item, at=0)  # prepend

            # With limit - removes oldest when prepending new items
            async def add_message(self, text: str):
                msg = await Message.objects.acreate(text=text)
                await self.stream_insert("messages", msg, at=0, limit=100)
        """
        from ..features.streams import StreamItem, StreamOp

        template_name = template or self._get_stream_item_template()
        dom_id_fn = dom_id or (lambda i: f"{name}-{i.pk}")

        html = await self._render_stream_item(template_name, item)
        stream_item = StreamItem(dom_id=dom_id_fn(item), html=html)

        op = StreamOp(op="insert", stream=name, items=[stream_item], at=at, limit=limit)
        await self.wire.send_stream_op(op)

    async def stream_delete(self, name: str, dom_id: str | int) -> None:
        """
        Delete an item from a stream by DOM ID.

        Args:
            name: Stream name (matches wire-stream attribute)
            dom_id: DOM ID of the item to delete, or item PK (int)
                   If int, will be converted to "{name}-{dom_id}"

        Example:
            async def remove_item(self, item_id: int):
                await self.stream_delete("items", item_id)
        """
        from ..features.streams import StreamItem, StreamOp

        if isinstance(dom_id, int):
            dom_id = f"{name}-{dom_id}"

        op = StreamOp(op="delete", stream=name, items=[StreamItem(dom_id=dom_id, html="")])
        await self.wire.send_stream_op(op)

    def _get_stream_item_template(self) -> str:
        """Get the default stream item template name."""
        # Convert "myapp/item_list.html" to "myapp/item_list_item.html"
        base = self._template_name.rsplit(".", 1)[0]
        return f"{base}_item.html"

    async def _render_stream_item(self, template_name: str, item: t.Any) -> str:
        """Render a single stream item to HTML."""
        template = self._get_template(template_name)
        context = {"item": item, "this": self}
        return await db(template.render)(context)

    # Upload operations

    _upload_registry: "UploadRegistry | None" = None

    def allow_upload(
        self,
        name: str,
        *,
        accept: list[str] | None = None,
        max_entries: int = 1,
        max_file_size: int | None = None,
        chunk_size: int | None = None,
        auto_upload: bool = True,
        external: "ExternalUploadCallback | None" = None,
    ) -> None:
        """
        Configure an upload field for this component.

        Call this in joined() to enable file uploads.

        Args:
            name: Unique name for this upload field
            accept: List of accepted file extensions (e.g., [".jpg", ".png"])
            max_entries: Maximum number of concurrent uploads (default 1)
            max_file_size: Maximum file size in bytes (default from settings)
            chunk_size: Chunk size for large file uploads (default from settings)
            auto_upload: Start upload immediately when files are selected
            external: Optional callback for external uploads (S3, GCS, etc.)
                      The callback receives (entry, component) and returns
                      ExternalUploadMeta with presigned URL.

        Example:
            async def joined(self):
                self.allow_upload(
                    "images",
                    accept=[".jpg", ".png", ".gif"],
                    max_entries=5,
                    max_file_size=5 * 1024 * 1024  # 5MB
                )

            # External upload example (S3):
            async def joined(self):
                self.allow_upload(
                    "documents",
                    accept=[".pdf", ".doc"],
                    external=self.presign_s3_upload,
                )

            def presign_s3_upload(self, entry, component):
                from wireview.features.uploads import ExternalUploadMeta
                import boto3

                s3 = boto3.client("s3")
                url = s3.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": "my-bucket", "Key": f"uploads/{entry.ref}"},
                    ExpiresIn=3600,
                )
                return ExternalUploadMeta(uploader="S3", url=url)
        """
        import asyncio

        # Get defaults from settings
        from .. import settings as wireview_settings
        from ..features.uploads import UploadConfig, UploadOp, UploadRegistry

        actual_max_file_size: int = (
            max_file_size
            if max_file_size is not None
            else getattr(wireview_settings, "UPLOAD_MAX_FILE_SIZE", 10 * 1024 * 1024)
        )
        actual_chunk_size: int = (
            chunk_size if chunk_size is not None else getattr(wireview_settings, "UPLOAD_CHUNK_SIZE", 64 * 1024)
        )

        if self._upload_registry is None:
            self._upload_registry = UploadRegistry(self.id)

        config = UploadConfig(
            name=name,
            accept=accept or [],
            max_entries=max_entries,
            max_file_size=actual_max_file_size,
            chunk_size=actual_chunk_size,
            auto_upload=auto_upload,
            external=external,
        )
        self._upload_registry.allow_upload(config)

        # Send config to client asynchronously
        async def send_config() -> None:
            endpoint = f"/__wireview_upload__/{self.id}/{name}/"
            op = UploadOp(
                op="config",
                upload=name,
                data=config.to_client_dict(endpoint),
            )
            await self.wire.send_upload_op(op)

        asyncio.create_task(send_config())

    @property
    def uploads(self) -> dict[str, list["UploadEntry"]]:
        """
        Access current upload entries grouped by upload name.

        Returns:
            Dictionary mapping upload names to lists of UploadEntry objects

        Example:
            # In template
            {% for entry in this.uploads.images %}
                <div>{{ entry.client_name }} - {{ entry.progress }}%</div>
            {% endfor %}
        """

        if self._upload_registry is None:
            return {}

        return {name: self._upload_registry.get_entries(name) for name in self._upload_registry.configs}

    async def cancel_upload(self, name: str, ref: str) -> None:
        """
        Cancel an upload entry.

        Args:
            name: Upload field name
            ref: Entry reference from the client

        Example:
            async def cancel_image(self, ref: str):
                await self.cancel_upload("images", ref)
        """
        from ..features.uploads import UploadOp

        if self._upload_registry:
            entry = self._upload_registry.cancel_entry(name, ref)
            if entry:
                op = UploadOp(op="cancel", upload=name, ref=ref)
                await self.wire.send_upload_op(op)

    async def consume_uploads(
        self,
        name: str,
    ) -> t.AsyncIterator["ConsumedUpload"]:
        """
        Consume completed uploads for processing.

        Yields ConsumedUpload objects for each completed file.
        Files are cleaned up after the context exits.

        Args:
            name: Upload field name to consume

        Yields:
            ConsumedUpload objects with save methods

        Example:
            async def save_images(self):
                async for upload in self.consume_uploads("images"):
                    path = await upload.save_to("uploads/images/")
                    # Create model instance with path
        """
        from ..features.uploads import ConsumedUpload

        if not self._upload_registry:
            return

        entries = self._upload_registry.get_completed_entries(name)
        for entry in entries:
            yield ConsumedUpload(entry)

    # LiveComponent communication

    async def send_update(
        self,
        live_component_id: str,
        **assigns: t.Any,
    ) -> None:
        """Send an update to a child LiveComponent.

        Updates the LiveComponent's state and triggers a re-render.
        The LiveComponent's update() callback is called with the new assigns.

        Args:
            live_component_id: ID of the target LiveComponent
            **assigns: New values to update on the LiveComponent

        Example:
            class Dashboard(Component):
                async def reset_counter(self):
                    await self.send_update("counter-1", count=0)

                async def update_all_counters(self, value: int):
                    for child in self.get_live_components():
                        await self.send_update(child.id, count=value)
        """
        # Note: This requires access to repository which we don't have directly.
        # The actual update will be dispatched through the consumer.
        await self.wire.send(
            "update_live_component",
            parent_id=self.id,
            live_component_id=live_component_id,
            assigns=assigns,
        )

    # Internal render operations

    def _render(self, repo: Repo) -> SafeString | None:
        """Render the component."""
        return self.wire.render(self, repo)

    def _render_with_slots(
        self,
        repo: Repo,
        slots: "SlotContainer | None" = None,
    ) -> SafeString | None:
        """Render the component with slot content.

        This method is called by the {% component_block %} template tag
        when rendering a component with slot content.

        Args:
            repo: The component repository.
            slots: SlotContainer with captured slot content.

        Returns:
            Rendered HTML as SafeString, or None if rendering should be skipped.
        """
        return self.wire.render(self, repo, slots)

    def _render_diff(self, repo: Repo):
        """Render the component and return a diff."""
        return self.wire.render_diff(self, repo)

    def _clear_temporary_assigns(self) -> None:
        """Clear temporary assigns after rendering.

        Resets fields listed in _temporary_assigns to their default values.
        This frees memory for large collections that are only needed during rendering.

        Called automatically by consumer.send_render() after each render cycle.
        """
        if not self._temporary_assigns:
            return

        for field_name in self._temporary_assigns:
            if field_name not in self.model_fields:
                continue

            field_info = self.model_fields[field_name]
            # Get the default value for this field
            # Note: In Pydantic v2, default_factory is the actual callable (e.g., list class)
            if field_info.default is not None:
                default_value = field_info.default
            elif field_info.default_factory is not None:
                # default_factory is a callable like `list` or a lambda
                default_value = field_info.default_factory()  # type: ignore[call-arg]
            else:
                # No default, skip this field
                continue

            # Set the field to its default value
            # Use object.__setattr__ to bypass Pydantic validation for performance
            object.__setattr__(self, field_name, default_value)


class ComponentNotFound(LookupError):
    """Raised when a component cannot be found."""

    pass
