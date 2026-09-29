"""Component base class for wireview."""

from __future__ import annotations

import dataclasses
import logging
import typing as t
from uuid import uuid4

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.template import loader
from django.utils.safestring import SafeString
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator, validate_call

from .. import utils
from ..async_result import AsyncResult
from ..schemas import ModelAction
from ..utils import db
from . import model_state, render_reads
from .meta import Repo, WireviewMeta
from .session import SessionView

if t.TYPE_CHECKING:
    import asyncio

    from ..features.uploads import (
        ConsumedUpload,
        ExternalUploadCallback,
        UploadEntry,
        UploadRegistry,
    )
    from ..js import JS
    from ..slots import SlotContainer
    from .live_session import LiveSession

log = logging.getLogger("wireview")

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
    # Through utils like every other fan-out, so broadcast_published fires for it too.
    await utils.asend_to(channel, "notification", kwargs=kwargs)


class LifecycleHook(t.TypedDict):
    """Hook entry registered via Component.attach_hook()."""

    name: str
    callback: t.Callable[..., t.Any]


#: Fields that never go into the signed state, whatever a Meta says. ``session``
#: is here for more than tidiness: signed state travels to the browser, and
#: session data must not (#68).
ALWAYS_EXCLUDED = frozenset({"user", "wire", "session"})

#: Where each configuration attribute used to live, for the error that says so.
_MOVED_TO_META = {
    "_template_name": "template_name",
    "_exclude_fields": "exclude_fields",
    "_subscriptions": "subscriptions",
    "_temporary_assigns": "temporary_assigns",
    "_slots": "slots",
    "_on_mount": "on_mount",
    "_live_sessions": "live_sessions",
    "_presence_config": "presence",
}


@dataclasses.dataclass(frozen=True)
class ComponentOptions:
    """A component class's configuration, read from its ``class Meta`` (#99).

    ::

        class Inbox(Component):
            class Meta:
                template_name = "mail/inbox.html"
                subscriptions = {"mail"}
                temporary_assigns = {"messages"}

    A subclass inherits each key it does not set: a base that guards itself
    with ``on_mount`` or ``live_sessions`` keeps guarding its subclasses. A
    key a Meta does not know is an error, so a typo is not silently ignored.
    """

    #: The template the component renders.
    template_name: str | None = None
    #: Fields left out of the signed state, on top of ``user``, ``wire`` and ``session``.
    exclude_fields: frozenset[str] = ALWAYS_EXCLUDED
    #: Channels every instance listens on. For channels that depend on the
    #: instance's state, override ``get_subscriptions()``.
    subscriptions: frozenset[str] = frozenset()
    #: Fields reset to their default after each render, like Phoenix's temporary_assigns.
    temporary_assigns: frozenset[str] = frozenset()
    #: Expected slots: ``{"header": {"required": True, "doc": "..."}}``.
    slots: t.Mapping[str, t.Mapping[str, t.Any]] = dataclasses.field(default_factory=dict)
    #: Hooks run in order before ``joined()``, like Phoenix's on_mount.
    on_mount: tuple[t.Any, ...] = ()
    #: The ``live_session`` names the component may mount in. Empty means anywhere (#58).
    live_sessions: frozenset[str] = frozenset()
    #: Presence settings for ``PresenceMixin`` and ``PresenceTrackerMixin``.
    presence: t.Any = None

    def extended(self, meta: type, owner: type) -> "ComponentOptions":
        """These options with ``meta``'s attributes on top."""
        given = {name: value for name, value in vars(meta).items() if not name.startswith("__")}
        known = {f.name for f in dataclasses.fields(self)}
        if unknown := sorted(set(given) - known):
            raise TypeError(
                f"{owner.__qualname__}.Meta has no option {', '.join(map(repr, unknown))}. "
                f"Known options: {', '.join(sorted(known))}."
            )
        for name in ("exclude_fields", "subscriptions", "temporary_assigns", "live_sessions"):
            if name in given:
                given[name] = frozenset(given[name])
        if "exclude_fields" in given:
            given["exclude_fields"] |= ALWAYS_EXCLUDED
        if "on_mount" in given:
            given["on_mount"] = tuple(given["on_mount"])
        if "slots" in given:
            given["slots"] = dict(given["slots"])
        return dataclasses.replace(self, **given)


def _validate_handlers(cls: type) -> None:
    """Wrap the methods a class defines, the ones a client may call, in ``validate_call``.

    Shared by Component and LiveComponent, which register differently but must
    expose and validate their handlers the same way.
    """
    for attr_name, raw in list(vars(cls).items()):
        if attr_name.startswith("_") or not attr_name.islower():
            continue
        validate = validate_call(config={"arbitrary_types_allowed": True})
        try:
            if isinstance(raw, (classmethod, staticmethod)):
                # Validate the function and put the descriptor back. Wrapping
                # what getattr() returns instead stored a plain function: a
                # classmethod stayed bound to this class in every subclass,
                # and a staticmethod got the instance as its first argument.
                setattr(cls, attr_name, type(raw)(validate(raw.__func__)))
            elif callable(raw):
                setattr(cls, attr_name, validate(raw))
        except (NameError, TypeError):
            # Skip validation for methods with unresolvable type hints
            pass


def _resolve_options(cls: type) -> "ComponentOptions":
    """``cls``'s options: the nearest component base's, extended by its own Meta."""
    # Pydantic takes ``_name = value`` out of the class namespace and makes it a
    # private attribute, so vars() alone would let an old spelling through in
    # silence: the value would sit on every instance and configure nothing.
    declared = set(vars(cls)) | set(getattr(cls, "__private_attributes__", {}))
    if moved := sorted(name for name in _MOVED_TO_META if name in declared):
        raise TypeError(
            f"{cls.__qualname__} sets {', '.join(moved)}; component configuration lives in "
            f"`class Meta:` now ({', '.join(f'{name} -> Meta.{_MOVED_TO_META[name]}' for name in moved)})."
        )
    inherited = next(
        (base.__dict__["_meta"] for base in cls.__mro__[1:] if "_meta" in base.__dict__),
        ComponentOptions(),
    )
    meta = vars(cls).get("Meta")
    return inherited.extended(meta, cls) if meta is not None else inherited


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
    _fqn: t.ClassVar[str]

    # The class's resolved ``class Meta`` (#99). Set by __init_subclass__ from
    # the nearest component base's options and the class's own Meta.
    _meta: t.ClassVar["ComponentOptions"]

    # Instance-level lifecycle hooks attached via attach_hook()
    _lifecycle_hooks: dict[str, list[LifecycleHook]] = {}

    # What _clear_temporary_assigns() put back, by field: a field still holding
    # that object, unchanged, is stale (#111)
    _temporary_defaults: dict[str, t.Any] = {}

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        # A class-valued attribute is never a field. Pydantic already skips a
        # ``class Meta:`` written in the body, but not ``Meta = SharedMeta`` or
        # one built with type() (#99).
        ignored_types=(type,),
    )

    def get_subscriptions(self) -> set[str]:
        """The channels this instance listens on: ``Meta.subscriptions`` by default.

        Override it when they depend on the instance's state; a mixin adds its
        own with ``super()``::

            def get_subscriptions(self) -> set[str]:
                return {f"room.{self.room_id}"}

        The name belongs to the framework, so a client cannot call it.
        """
        return set(self._meta.subscriptions)

    @model_validator(mode="before")
    @classmethod
    def _load_django_models(cls, data: dict[str, t.Any]) -> dict[str, t.Any]:
        """Load the model instances a signed state holds as primary keys.

        Anywhere the field's annotation names a model -- ``Book``, ``Book | None``,
        ``list[Book]``, ``dict[str, Book]``, ``AsyncResult[Book]`` -- and any
        QuerySet (wireview/core/model_state.py).
        """
        if not isinstance(data, dict):
            return data
        for field_name, field_info in cls.model_fields.items():
            value = data.get(field_name)
            if value is None:
                continue
            if model_state.mentions_models(field_info.annotation) or model_state.has_queryset_marker(value):
                data[field_name] = model_state.load(field_info.annotation, value)
        return data

    @field_serializer("*", mode="wrap")
    def _serialize_django_types(self, value: t.Any, handler: t.Callable) -> t.Any:
        """Model instances go into the signed state as their pks, however deep (#113)."""
        if model_state.has_models(value):
            return model_state.dump(value)
        return handler(value)

    def __init_subclass__(cls: t.Type["Component"], name: str | None = None, public: bool = True) -> None:
        cls._meta = _resolve_options(cls)
        render_reads.install(cls)
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

        _validate_handlers(cls)

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

        raise ComponentNotFound(f"Component '{name}' not found. Available: {list(cls._all.keys())}")

    @classmethod
    def _build(
        cls,
        _component_name: str,
        state: ComponentState,
        params: dict[str, t.Any],
        user: AnonymousUser | AbstractBaseUser | None = None,
        channel_name: str | None = None,
        channel_layer=None,
        connection_id: str | None = None,
        session: SessionView | None = None,
        live_session: "LiveSession | None" = None,
    ) -> "Component":
        """Build a component instance from state."""
        component_class = cls._resolve(_component_name)

        instance = component_class.new(
            user=user or AnonymousUser(),
            session=session if session is not None else SessionView(),
            wire=WireviewMeta(
                params=params,
                channel_name=channel_name,
                channel_layer=channel_layer,
                connection_id=connection_id,
                live_session=live_session,
            ),
            **state,
        )
        return instance

    @classmethod
    def _get_template(cls, template_name: str | None = None) -> AnyTemplate:
        """Get the template for this component."""
        template_name = template_name or cls._meta.template_name
        if not template_name:
            raise ImproperlyConfigured(f"{cls.__qualname__} has no template: set `template_name` in its `class Meta:`.")
        # Django's cached loader already keeps compiled templates, and drops them
        # when TEMPLATES changes. A cache of our own did neither: it outlived an
        # override_settings(TEMPLATES=...), and switched on with DEBUG (#100).
        return loader.get_template(template_name)  # type: ignore[return-value]

    # State
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: AnonymousUser | AbstractBaseUser
    wire: WireviewMeta
    # Read-only view of the Django session this component was mounted from. Empty
    # when the call site had none (a component built by hand, a project without
    # the session middleware).
    session: SessionView = Field(default_factory=SessionView)

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
        - The client reports the component gone from the DOM (``leave``); nested
          LiveComponents receive it too
        - A LiveComponent's parent stops rendering it
        - The WebSocket connection is closed (browser close, navigation, network loss)

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

    # =========================================================================
    # Lifecycle Hooks
    # =========================================================================

    def attach_hook(
        self,
        name: str,
        stage: t.Literal["handle_event", "handle_params", "after_render"],
        callback: t.Callable[..., t.Any],
    ) -> None:
        """
        Attach a lifecycle hook to intercept specific stages.

        Hooks provide a mechanism to tap into key stages of the component lifecycle
        to inject common functionality. This is similar to Phoenix LiveView's attach_hook.

        Args:
            name: Unique name for this hook (used for detaching)
            stage: The lifecycle stage to hook into:
                - "handle_event": Before event handlers are called
                - "handle_params": Before params_changed is called
                - "after_render": After the component renders
            callback: The hook function to call

        Hook Signatures:
            - handle_event: async def hook(event: str, params: dict) -> dict
                Returns {"halt": True} to stop event processing, or {"cont": True}
            - handle_params: async def hook(params: dict, uri: str) -> dict
                Returns {"halt": True} to skip params_changed, or {"cont": True}
            - after_render: async def hook() -> None

        Example:
            class TrackingHook:
                @staticmethod
                async def on_mount(component, params, session):
                    # Attach event tracking hook
                    async def track_events(event, params):
                        await analytics.track(event, params)
                        return {"cont": True}

                    component.attach_hook("tracking", "handle_event", track_events)
                    return {"cont": True}

            class TrackedPage(Component):
                class Meta:
                    on_mount = [TrackingHook]
        """
        if not hasattr(self, "_lifecycle_hooks") or not isinstance(self._lifecycle_hooks, dict):
            self._lifecycle_hooks = {}

        if stage not in self._lifecycle_hooks:
            self._lifecycle_hooks[stage] = []

        self._lifecycle_hooks[stage].append({"name": name, "callback": callback})

    def detach_hook(self, name: str, stage: str | None = None) -> bool:
        """
        Detach a lifecycle hook by name.

        Args:
            name: The name of the hook to detach
            stage: Optional stage to limit detachment (if None, removes from all stages)

        Returns:
            True if a hook was removed, False otherwise
        """
        if not hasattr(self, "_lifecycle_hooks") or not isinstance(self._lifecycle_hooks, dict):
            return False

        removed = False
        stages = [stage] if stage else list(self._lifecycle_hooks.keys())

        for s in stages:
            if s in self._lifecycle_hooks:
                before = len(self._lifecycle_hooks[s])
                self._lifecycle_hooks[s] = [h for h in self._lifecycle_hooks[s] if h["name"] != name]
                if len(self._lifecycle_hooks[s]) < before:
                    removed = True

        return removed

    async def _run_hooks(
        self,
        stage: str,
        *args: t.Any,
        **kwargs: t.Any,
    ) -> dict[str, t.Any]:
        """
        Run all hooks for a given lifecycle stage.

        Returns:
            {"halt": True} if any hook halted, otherwise {"cont": True}
        """
        if not hasattr(self, "_lifecycle_hooks") or not isinstance(self._lifecycle_hooks, dict):
            return {"cont": True}

        hooks = self._lifecycle_hooks.get(stage, [])
        for hook in hooks:
            result = await hook["callback"](*args, **kwargs)
            if result and result.get("halt"):
                return {"halt": True, "hook": hook["name"]}

        return {"cont": True}

    async def _handle_params(self, params: dict[str, t.Any], uri: str) -> None:
        """Run the handle_params hooks, then ``params_changed`` unless one halted.

        Every path that tells a component its URL changed comes through here, so
        an attached hook cannot be skipped by one of them (#110).
        """
        if (await self._run_hooks("handle_params", params, uri)).get("halt"):
            return
        await self.params_changed(params, uri)

    async def _mount(
        self,
        params: dict[str, t.Any] | None = None,
        session: t.Any = None,
    ) -> bool:
        """Run the ``Meta.on_mount`` hooks for this instance, once, before ``joined()``.

        Every path that produces a component the user sees calls this: the
        WebSocket join, the LiveComponent children a parent's render named, the
        dead (HTTP) render of a ``{% component %}`` tag, and ``testing.mount()``.
        Running it in only some of them would let protected HTML out through the
        others, which is what #75 was.

        Args:
            params: URL/query parameters handed to each hook
            session: The request session, when the call site has one

        Returns:
            True when the caller should go on to ``joined()``, False when a hook
            halted the mount. A repeat call for the same instance runs nothing
            and answers the same way the first call did.

        Raises:
            Anything a hook raises, after marking the mount refused. Callers
            treat that the same way they treat ``False`` -- the component does
            not render and does not stay in the repository -- and then let the
            exception carry the traceback somewhere visible.
        """
        if self.wire.has_mounted or self.wire.has_joined:
            return not self.wire.mount_halted

        self.wire.has_mounted = True
        try:
            result = await self._run_live_session_gate(params, session)
            if not result.get("halt"):
                result = await self._run_on_mount_hooks(params, session)
        except BaseException:
            # A hook that crashes has not authorized anything. The flag is set
            # before the exception travels so that every caller's cleanup path
            # sees a refused mount rather than an unfinished one: an authorization
            # query that fails with a database error must not be the difference
            # between "refused" and "allowed through".
            self.wire.mount_halted = True
            raise
        if result.get("halt"):
            self.wire.mount_halted = True
            log.debug("on_mount halted %s (%s): %s", self._name, self.id, result.get("hook"))
            return False
        return True

    async def _run_live_session_gate(
        self,
        params: dict[str, t.Any] | None = None,
        session: t.Any = None,
    ) -> dict[str, t.Any]:
        """Apply the page's ``live_session`` before this component's own hooks (#58).

        Two things happen here, and both have to happen on *every* path that
        produces a component -- the join, a children restore, a LiveComponent a
        parent's render created, and a re-join -- which is why they sit in
        ``_mount`` rather than in the consumer.

        1. ``Meta.live_sessions``, when the class declares it, says where the
           component is allowed to live. A component declaring ``{"admin"}``
           halts on a page with no boundary, so the safe answer is the default
           one.
        2. The session's own ``on_mount`` hooks run, before the component's.

        The ``authorize`` predicate is *not* re-run here. It is answered once
        per request by the view decorator and once per connection by
        ``command_join``, which are the points where a refusal can still stop
        the first byte.
        """
        from .live_session import declaration_allows

        policy = getattr(self.wire, "live_session", None)
        if not declaration_allows(type(self), policy):
            log.debug(
                "%s (%s) is declared for %s and the page is in %r",
                self._name,
                self.id,
                sorted(self._meta.live_sessions),
                policy.name if policy is not None else "",
            )
            return {"halt": True, "hook": "Meta.live_sessions"}
        if policy is None:
            return {"cont": True}
        return await policy.run_on_mount(self, params, session)

    async def _run_on_mount_hooks(
        self,
        params: dict[str, t.Any] | None = None,
        session: t.Any = None,
    ) -> dict[str, t.Any]:
        """
        Run the hooks in ``Meta.on_mount``, in order.

        Called during component initialization, before joined().

        Returns:
            {"halt": True} if any hook halted, otherwise {"cont": True}
        """
        for hook_class in self._meta.on_mount:
            if hasattr(hook_class, "on_mount"):
                on_mount = hook_class.on_mount
                # Support both static methods and instance methods
                if isinstance(on_mount, staticmethod):
                    on_mount = on_mount.__func__
                result = await on_mount(self, params or {}, session or {})
                if result and result.get("halt"):
                    return {"halt": True, "hook": hook_class.__name__}

        return {"cont": True}

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
            from wireview import JS
            await self.push_js(JS().set_value("input[name=search]", ""))
        """
        await self.wire.push_js(self.id, js)

    async def defer(self, _f: t.Callable[P, t.Coroutine], *args: P.args, **kwargs: P.kwargs) -> None:
        """Run one of this component's handlers after the current event is done.

        The call goes through the connection like a client event, so ``_f``
        has to be a handler the client could call too (#99 renamed it from
        ``deffer``).
        """
        await self.wire.defer(self.id, _f, *args, **kwargs)

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

    # Track active async tasks by name
    _async_tasks: dict[str, "asyncio.Task[t.Any]"] = {}
    # assign_async tasks: held so a running one is not garbage collected, and
    # cancelled with the named ones when the component leaves (#95)
    _assign_tasks: set["asyncio.Task[t.Any]"] = set()

    async def start_async(
        self,
        name: str,
        coro: t.Coroutine[t.Any, t.Any, t.Any],
    ) -> None:
        """
        Start a named async operation that can be cancelled or replaced.

        When the operation completes, `handle_async` will be called with the
        result or error. If an operation with the same name is already running,
        it will be cancelled and replaced.

        Args:
            name: Unique name for this async operation
            coro: The coroutine to execute

        Example:
            class Search(Component):
                results: list[str] = []
                loading: bool = False

                async def search(self, query: str):
                    self.loading = True
                    await self.start_async("search", self.do_search(query))

                async def do_search(self, query: str) -> list[str]:
                    return await SearchService.search(query)

                async def handle_async(
                    self,
                    name: str,
                    result: tuple[Literal["ok"], Any] | tuple[Literal["exit"], Exception],
                ):
                    if name == "search":
                        self.loading = False
                        if result[0] == "ok":
                            self.results = result[1]
                        else:
                            self.results = []
        """
        import asyncio

        # Cancel existing task with same name
        await self.cancel_async(name)

        async def run_and_handle() -> None:
            try:
                result: tuple[str, t.Any]
                try:
                    result = ("ok", await coro)
                except asyncio.CancelledError:
                    # Replaced, cancel_async(), or the component left: nothing
                    # to hand over and nothing to render.
                    return
                except Exception as e:
                    result = ("exit", e)
                try:
                    await self.handle_async(name, result)  # type: ignore[arg-type]
                except Exception:
                    # Raised inside a task nobody awaits, it was never even logged,
                    # and the render was skipped. Recover as for a raising handler (#94).
                    log.exception("%s (%s) raised in handle_async(%r)", self._name, self.id, name)
                    await self.wire.send("crashed", id=self.id)
                    return
                await self.send_render()
            finally:
                # Only this task's entry: a task that replaced it under the same
                # name must stay tracked, or nothing could cancel it (#95).
                if self._async_tasks.get(name) is task:
                    del self._async_tasks[name]

        # Create and track the task
        task = asyncio.create_task(run_and_handle())
        # A task cancelled before its first step never awaits coro
        task.add_done_callback(lambda _: coro.close())
        self._async_tasks[name] = task

    async def cancel_async(self, name: str) -> bool:
        """
        Cancel an in-flight async operation by name.

        Args:
            name: The name of the async operation to cancel

        Returns:
            True if a task was cancelled, False if no task was running

        Example:
            async def cancel_search(self):
                await self.cancel_async("search")
                self.loading = False
        """
        task = self._async_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def handle_async(
        self,
        name: str,
        result: "tuple[t.Literal['ok'], t.Any] | tuple[t.Literal['exit'], Exception]",
    ) -> None:
        """
        Handle the completion of a named async operation.

        Override this method to process the results of async operations
        started with `start_async`.

        Args:
            name: The name of the completed async operation
            result: Tuple of ("ok", value) or ("exit", exception)

        Example:
            async def handle_async(self, name, result):
                if name == "load_data":
                    if result[0] == "ok":
                        self.data = result[1]
                    else:
                        self.error = str(result[1])
        """
        # Default implementation does nothing
        # Override in subclass to handle results
        pass

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
                # The result goes into the signed state: JSON values, not a model instance
                stats: AsyncResult[dict] | None = None

                async def joined(self):
                    self.stats = await self.assign_async(self._load_stats())

                async def _load_stats(self):  # underscore: not an event handler
                    return await Stats.objects.values("visits", "orders").aget()

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
            except asyncio.CancelledError:
                # The component left (#95): nobody to render for
                return
            except Exception as e:
                result.state = AsyncResult.failure(e).state
                result.error = e
                if on_error:
                    on_error(e)
            # Trigger re-render
            await self.send_render()

        # Schedule the task to run, and hold it while it does
        task = asyncio.create_task(run_and_update())
        task.add_done_callback(lambda _: coro.close())
        self._assign_tasks.add(task)
        task.add_done_callback(self._assign_tasks.discard)

        return result

    def _cancel_async_tasks(self) -> None:
        """Cancel what ``start_async`` and ``assign_async`` left running.

        The consumer calls it when the component leaves, after ``leaving()``
        (#95). A task that outlived its component kept doing its work, held the
        instance in memory, and at the end asked a session that no longer had
        the component (or no longer existed) for a render.
        """
        for task in [*self._async_tasks.values(), *self._assign_tasks]:
            if not task.done():
                task.cancel()
        self._async_tasks.clear()
        self._assign_tasks.clear()

    def freeze(self) -> None:
        """Freeze the component to prevent further rendering."""
        self.wire.freeze()

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
                await self.stream("items", [item async for item in Item.objects.all()[:100]])

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
        base = (self._meta.template_name or "").rsplit(".", 1)[0]
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
                    # Underscore: a public name would be an event handler a client can call
                    external=self._presign_s3_upload,
                )

            def _presign_s3_upload(self, entry, component):
                from wireview import ExternalUploadMeta
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
            self._upload_registry = UploadRegistry(self.id, connection_id=self.wire.connection_id or "")

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
            # The owner segment is what lets the HTTP endpoint tell two connections
            # on the same page apart (#77). "-" is the unowned case (tests, mount()).
            owner = self.wire.connection_id or "-"
            endpoint = f"/__wireview_upload__/{owner}/{self.id}/{name}/"
            op = UploadOp(
                op="config",
                upload=name,
                data=config.to_client_dict(endpoint),
                component_id=self.id,
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

        Yields ConsumedUpload objects for each completed file. Once the loop
        moves past an upload it is consumed: its temp file is deleted, it no
        longer counts against ``max_entries``, and no later call yields it
        again -- however it was read (``save_to``, ``read``, ``open``).

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
            upload = ConsumedUpload(entry)
            try:
                yield upload
            finally:
                # Only save_to() used to mark it: an upload read() left "completed"
                # held its max_entries slot and came back on the next call (#110).
                upload.finish()

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

        Resets the fields listed in ``Meta.temporary_assigns`` to their default values.
        This frees memory for large collections that are only needed during rendering.

        Called automatically by consumer.send_render() after each render cycle.
        """
        if not self._meta.temporary_assigns:
            return

        for field_name in self._meta.temporary_assigns:
            if field_name not in type(self).model_fields:
                continue

            field_info = type(self).model_fields[field_name]
            # A field with no default is left alone. The test used to be
            # ``default is not None``, which a required field's PydanticUndefined
            # passes (it was assigned) and a field defaulting to None fails (#113).
            if field_info.is_required():
                continue
            default_value = field_info.get_default(call_default_factory=True)

            # Set the field to its default value
            # Use object.__setattr__ to bypass Pydantic validation for performance
            object.__setattr__(self, field_name, default_value)
            self._temporary_defaults[field_name] = default_value

    def _stale_temporaries(self) -> frozenset[str]:
        """The temporary assigns nothing has touched since they were reset.

        The page still shows what the render before the reset drew from them,
        and the next render must not count the default as a change (#111). A
        field assigned again holds another object; one changed in place no
        longer equals its default.
        """
        stale = set()
        for field_name, default_value in self._temporary_defaults.items():
            value = self.__dict__.get(field_name)
            if value is default_value and value == type(self).model_fields[field_name].get_default(
                call_default_factory=True
            ):
                stale.add(field_name)
        return frozenset(stale)


Component._meta = ComponentOptions()


class ComponentNotFound(LookupError):
    """Raised when a component cannot be found."""

    pass
