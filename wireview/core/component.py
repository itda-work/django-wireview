"""Component base class for wireview."""

from __future__ import annotations

import typing as t
from uuid import uuid4

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

# Type aliases
ComponentState = dict[str, t.Any]
MessagePayload = dict[str, t.Any]
P = t.ParamSpec("P")


class Template(t.Protocol):
    """Protocol for Django template."""

    def render(
        self,
        context: dict[str, t.Any] | None = ...,
        request: HttpRequest | None = ...,
    ) -> SafeString: ...


__all__ = ("Component", "ComponentNotFound", "MessagePayload", "broadcast")


def broadcast(channel: str, **kwargs: t.Any) -> None:
    """Broadcast a notification to a channel."""
    utils.send_to(channel, type="notification", kwargs=kwargs)


class Component(BaseModel):
    """
    Base class for wireview components.

    Components are Pydantic models that can be rendered to HTML and
    updated in real-time via WebSocket.
    """

    __name__: str

    _all: t.ClassVar[dict[str, t.Type["Component"]]] = {}
    _urls: t.ClassVar[dict] = {}
    _name: t.ClassVar[str]
    _template_name: t.ClassVar[str]
    _templates: t.ClassVar[dict[str, Template]] = {}
    _fqn: t.ClassVar[str]

    # fields to exclude from the component state during serialization
    _exclude_fields: t.ClassVar[set[str]] = {"user", "wire"}

    # Subscriptions: you can define here which channels this component is subscribed to
    _subscriptions: t.ClassVar[set[str]] = set()

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
            name = name or cls.__name__
            cls._all[name] = cls
            # Component name
            cls._name = name
            # Fully qualified name
            cls._fqn = f"{cls.__module__}.{name}"

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
        if _component_name not in cls._all:
            raise ComponentNotFound(
                f"Could not find requested component '{_component_name}'. Did you load the component?"
            )

        instance = cls._all[_component_name].new(
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
    def _get_template(cls, template_name: str | None = None) -> Template:
        """Get the template for this component."""
        template_name = template_name or cls._template_name
        if settings.DEBUG:
            return loader.get_template(template_name)
        else:
            if (template := cls._templates.get(template_name)) is None:
                template = loader.get_template(template_name)
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

    async def mutation(self, channel: str, action: ModelAction, instance: t.Any) -> None:
        """Called when a model mutation is broadcast."""
        ...

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        """Called when a notification is broadcast."""
        ...

    async def destroy(self) -> None:
        """Destroy this component."""
        await self.wire.destroy(self.id)

    async def send_render(self) -> None:
        """Request a re-render of this component."""
        await self.wire.send("send_render", id=self.id)

    async def focus_on(self, selector: str) -> None:
        """Focus on an element matching the selector."""
        await self.wire.send("focus_on", selector=selector)

    # DOM operations

    def skip_render(self) -> None:
        """Skip the next render cycle."""
        self.wire.skip_render()

    def force_render(self) -> None:
        """Force a full re-render on the next cycle."""
        self.wire.force_render()

    async def deffer(self, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
        """Defer a function call to be executed later."""
        await self.wire.deffer(self.id, _f, *args, **kwargs)

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
        await self.wire.send_dom_action(_action, _id, html)

    # Internal render operations

    def _render(self, repo: Repo) -> SafeString | None:
        """Render the component."""
        return self.wire.render(self, repo)

    def _render_diff(self, repo: Repo):
        """Render the component and return a diff."""
        return self.wire.render_diff(self, repo)


class ComponentNotFound(LookupError):
    """Raised when a component cannot be found."""

    pass
