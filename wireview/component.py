from __future__ import annotations

import asyncio
import difflib
import typing as t
from asyncio import iscoroutine
from functools import reduce
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer
from django.apps import apps
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.http import HttpRequest
from django.shortcuts import resolve_url  # type: ignore
from django.template import loader
from django.utils.html import format_html
from django.utils.safestring import SafeString, SafeText, mark_safe
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
    validate_call,
)

from . import settings, utils
from .schemas import DomAction, ModelAction
from .utils import db

if settings.USE_HMIN:
    try:
        from hmin.base import html_minify  # type: ignore
    except ImportError as e:
        raise ImportError(
            "If you enable REACTOR['USE_HMIN'] you need to install django-hmin"
        ) from e
else:

    def html_minify(html: str) -> str:
        return html


ComponentState = Context = MessagePayload = dict[str, t.Any]
RedirectDestination = t.Callable[(...), t.Any] | models.Model | str
HTMLDiff = list[str | int]
ComponentOrHtml: t.TypeAlias = "Component | SafeString"
P = t.ParamSpec("P")


class Template(t.Protocol):
    def render(
        self,
        context: Context | dict[str, t.Any] | None = ...,
        request: HttpRequest | None = ...,
    ) -> SafeString:
        ...


class Repo(t.Protocol):
    pass


ScrollPosition = (
    t.Literal["start"]
    | t.Literal["end"]
    | t.Literal["center"]
    | t.Literal["nearest"]
)


__all__ = ("Component", "broadcast")


def broadcast(channel: str, **kwargs: t.Any):
    utils.send_to(channel, type="notification", kwargs=kwargs)


class ReactorMeta:
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

    def clone(self):
        return type(self)(
            params=self.params,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )

    def skip_render(self):
        self._skip_render = True

    def force_render(self):
        self._skip_render = False
        self._last_sent_html = []

    async def destroy(self, component_id: str):
        self.freeze()
        await self.send("remove", id=component_id)

    def freeze(self):
        self._is_frozen = True

    async def redirect_to(self, to: RedirectDestination, **kwargs: t.Any):
        url = resolve_url(to, **kwargs)
        self._redirected_to = url
        if self.channel_name:
            self.freeze()
            await self.send("url_change", command="redirect", url=url)

    async def replace_to(self, to: RedirectDestination, **kwargs):
        url = resolve_url(to, **kwargs)
        await self.send("url_change", command="replace", url=url)

    async def push_to(self, to: RedirectDestination, **kwargs):
        url = resolve_url(to, **kwargs)
        await self.send("url_change", command="push", url=url)

    async def render_diff(
        self, component: "Component", repo: Repo
    ) -> HTMLDiff | None:
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

    def render(self, component: "Component", repo: Repo) -> None | SafeText:
        html = None
        if not self.channel_name and self._redirected_to:
            html = format_html(
                '<meta http-equiv="refresh" content="0; url={url}">',
                url=self._redirected_to,
            )
        elif not (self._is_frozen or self._redirected_to) and html is None:
            template = component._get_template()
            context = self._get_context(component, repo)
            html = template.render(context).strip()
            html = html_minify(html)
        if html:
            return mark_safe(html)

    async def send_dom_action(
        self,
        action: DomAction,
        id: str,
        html: SafeString,
    ):
        await self.send("dom_action", action=action.value, id=id, html=html)

    async def scroll_into_view(
        self,
        id: str,
        behavoir: t.Literal["smooth"]
        | t.Literal["instant"]
        | t.Literal["auto"] = "auto",
        block: ScrollPosition = "start",
        inline: ScrollPosition = "nearest",
    ):
        await self.send(
            "scroll_into_view",
            id=id,
            behavoir=behavoir,
            block=block,
            inline=inline,
        )

    async def deffer(
        self,
        _id: str,
        _f: t.Callable[P, t.Coroutine],
        *args: P.args,
        **kwargs: P.kwargs,
    ):
        await self.send(
            "dispatch_event",
            command=_f.__name__,
            id=_id,
            args=args,
            kwargs=kwargs,
        )

    async def send(self, _command: str, **kwargs: t.Any):
        if self.channel_name:
            await self.send_to(self.channel_name, _command, **kwargs)

    async def send_to(self, _channel: str, _command: str, **kwargs: t.Any):
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
    _PYDANTIC_CLASS_ATTRS = frozenset({
        "model_fields",
        "model_computed_fields",
        "model_config",
        "model_extra",
        "model_fields_set",
    })

    def _get_context(
        self,
        component: "Component",
        repo: Repo,
    ) -> Context:
        context = {}

        def _run_coro(coro):
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
            reactor_repository=repo,
        )


def compress_diff(diff: HTMLDiff, diff_item: str | int) -> HTMLDiff:
    if isinstance(diff_item, str) or isinstance(diff[-1], str):
        diff.append(diff_item)
    else:
        same_sign = not (diff[-1] > 0) ^ (diff_item > 0)
        if same_sign:
            diff[-1] += diff_item
        else:
            diff.append(diff_item)
    return diff


class Component(BaseModel):
    __name__: str

    _all: t.ClassVar[dict[str, t.Type["Component"]]] = {}
    _urls: t.ClassVar[dict] = {}
    _name: t.ClassVar[str]
    _template_name: t.ClassVar[str]
    _templates: t.ClassVar[dict[str, Template]] = {}
    _fqn: t.ClassVar[str]

    # fields to exclude from the component state during serialization
    _exclude_fields: t.ClassVar[set[str]] = {"user", "reactor"}

    # Subscriptions: you can define here which channels this component is
    # subscribed to
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
                data[field_name] = model_class.objects.filter(
                    pk__in=value.get("ids", [])
                )

        return data

    @field_serializer("*", mode="wrap")
    def _serialize_django_types(self, value: t.Any, handler) -> t.Any:
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

    def __init_subclass__(
        cls: t.Type["Component"], name: str | None = None, public: bool = True
    ):
        if public:
            name = name or cls.__name__
            cls._all[name] = cls
            # Component name
            cls._name = name
            # Fully qualified name
            cls._fqn = f"{cls.__module__}.{name}"

        for attr_name in vars(cls):
            attr = getattr(cls, attr_name)
            if (
                not attr_name.startswith("_")
                and attr_name.islower()
                and callable(attr)
            ):
                try:
                    setattr(
                        cls,
                        attr_name,
                        validate_call(
                            config={"arbitrary_types_allowed": True}
                        )(attr),
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
        channel_layer: BaseChannelLayer | None = None,
    ) -> "Component":
        if _component_name not in cls._all:
            raise ComponentNotFound(
                (
                    f"Could not find requested component '{_component_name}'. "
                    f"Did you load the component?"
                )
            )

        # TODO: rename state to initial_state
        instance = cls._all[_component_name].new(
            user=user or AnonymousUser(),
            reactor=ReactorMeta(
                params=params,
                channel_name=channel_name,
                channel_layer=channel_layer,
            ),
            **state,
        )
        return instance

    @classmethod
    def _get_template(cls, template_name: str | None = None) -> Template:
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
    reactor: ReactorMeta

    @classmethod
    def new(cls, **kwargs: t.Any):
        return cls(**kwargs)

    async def joined(self):
        ...

    async def mutation(
        self, channel: str, action: ModelAction, instance: t.Any
    ):
        ...

    async def notification(self, channel: str, **kwargs: t.Any):
        ...

    async def destroy(self):
        await self.reactor.destroy(self.id)

    async def send_render(self):
        await self.reactor.send("send_render", id=self.id)

    async def focus_on(self, selector: str):
        await self.reactor.send("focus_on", selector=selector)

    # Dom operations

    def skip_render(self):
        self.reactor.skip_render()

    def force_render(self):
        self.reactor.force_render()

    async def deffer(
        self,
        _f: t.Callable[P, t.Coroutine],
        *args: P.args,
        **kwargs: P.kwargs,
    ):
        await self.reactor.deffer(self.id, _f, *args, **kwargs)

    def freeze(self):
        self.reactor.freeze()

    async def dom(
        self,
        _action: DomAction,
        _id: str,
        _component_class_or_template_name: t.Type["Component"] | str,
        **kwargs,
    ):
        if isinstance(_component_class_or_template_name, str):
            template = self._get_template(_component_class_or_template_name)
            html = await db(template.render)(kwargs)
        else:
            from .repository import ComponentRepository

            component = _component_class_or_template_name.new(
                reactor=self.reactor.clone(),
                user=self.user,
                **kwargs,
            )
            html = await db(component._render)(
                ComponentRepository(
                    is_live=False,
                    user=self.user,
                    params=self.reactor.params,
                )
            )
        await self.reactor.send_dom_action(_action, _id, html)

    # Internal render operations

    def _render(self, repo: Repo):
        return self.reactor.render(self, repo)

    def _render_diff(self, repo: Repo):
        return self.reactor.render_diff(self, repo)


class ComponentNotFound(LookupError):
    pass
