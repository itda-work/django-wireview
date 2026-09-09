import json
import logging
import typing as t
from dataclasses import dataclass, field
from functools import reduce
from typing import cast
from urllib.parse import parse_qsl, urlencode

from channels.db import database_sync_to_async as db
from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from . import telemetry
from .component import Component, MessagePayload
from .live_component import LiveComponent
from .utils import filter_parameters

ChildrenRepo = dict[str, tuple[str, dict[str, t.Any]]]

log = logging.getLogger("wireview")

# Packages whose classes are framework surface, never client-callable handlers.
_FRAMEWORK_ROOTS = ("wireview", "pydantic")


def _is_framework_class(cls: type) -> bool:
    """Whether a class comes from the framework rather than from user code."""
    if cls is object:
        return True
    module = getattr(cls, "__module__", "") or ""
    root = module.partition(".")[0]
    return root in _FRAMEWORK_ROOTS


@dataclass
class LifecycleBatch:
    """The lifecycle work one parent's render left behind (``ComponentRepository.take_lifecycle``)."""

    new: list[LiveComponent] = field(default_factory=list)
    updates: list[tuple[LiveComponent, dict[str, t.Any]]] = field(default_factory=list)
    retired: list[Component] = field(default_factory=list)

    @property
    def to_render(self) -> list[LiveComponent]:
        """Children that ran a lifecycle hook and need their own render, each once."""
        seen: dict[str, LiveComponent] = {c.id: c for c in self.new}
        for component, _props in self.updates:
            seen.setdefault(component.id, component)
        return list(seen.values())


class ComponentRepository:
    user: AnonymousUser | AbstractBaseUser

    def __init__(
        self,
        *,
        is_live: bool,
        user: AnonymousUser | AbstractBaseUser | None = None,
        params: dict[str, t.Any] | None = None,
        channel_name: str | None = None,
        channel_layer: BaseChannelLayer | None = None,
    ):
        self.params = params or {}
        self.channel_name = channel_name
        self.channel_layer = channel_layer
        self.user = user or AnonymousUser()
        self.components: dict[str, Component] = {}
        self.children: ChildrenRepo = {}
        self.is_live = is_live
        # Track LiveComponents that need joined() called after parent renders
        self._pending_live_components: list[LiveComponent] = []
        # Track LiveComponents that need update() called after parent renders
        self._pending_updates: list[tuple[LiveComponent, dict[str, t.Any]]] = []
        # Instances replaced under a reused id; they owe a leaving() call
        self._pending_leaving: list[Component] = []
        # Child ids each parent named in its current template pass
        self._rendered_children: dict[str, set[str]] = {}

    @staticmethod
    def extract_params(qs: str):
        return {key: json.loads(value) if key.endswith(".json") else value for key, value in parse_qsl(qs)}

    def set_query_string(self, qs: str):
        params = self.extract_params(qs)

        # remove old keys
        for key in list(self.params.keys()):
            if key not in params:
                self.params.pop(key)

        self.params.update(params)

    def get_query_string(self) -> str:
        return urlencode(
            {key: json.dumps(value) if key.endswith(".json") else value for key, value in self.params.items()}
        )

    def get(self, component_id: str) -> Component | None:
        return self.components.get(component_id)

    def build(
        self,
        name: str,
        state: MessagePayload,
    ) -> Component:
        if component_id := state.get("id"):
            if component := self.components.get(component_id):
                # override with the passed state but preserve the rest of the state
                for key, value in state.items():
                    # Call the model validator to convert Django models
                    validator = component.__class__._load_django_models
                    converted = validator({key: value})  # type: ignore[operator]
                    setattr(component, key, converted.get(key, value))
                return component
            elif child := self.children.get(component_id):
                child_name, child_state = child
                if child_name == name:
                    state = child_state | state
                    self.children.pop(component_id)

        component = Component._build(
            name,
            state,
            params=self.params,
            user=self.user,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )
        return self.register_component(component)

    def build_live_component(
        self,
        name: str,
        state: MessagePayload,
        parent_id: str,
    ) -> LiveComponent:
        """Build a LiveComponent and register it under a parent.

        Args:
            name: LiveComponent class name
            state: Initial state including 'id'
            parent_id: ID of the parent Component

        Returns:
            Built and registered LiveComponent instance

        Raises:
            LookupError: If LiveComponent class not found
            ValueError: If 'id' not provided in state
        """
        if "id" not in state:
            raise ValueError("LiveComponent requires an 'id' in state")

        component_id = state["id"]
        component_class = LiveComponent._resolve_live(name)
        props = {key: value for key, value in state.items() if key != "id"}
        self._rendered_children.setdefault(parent_id, set()).add(component_id)

        # Re-render case: the parent template names an id we already hold.
        if existing := self.components.get(component_id):
            if type(existing) is component_class:
                existing = cast(LiveComponent, existing)
                if existing._parent_id != parent_id:
                    log.debug("LiveComponent %s moved from %s to %s", component_id, existing._parent_id, parent_id)
                    existing._parent_id = parent_id
                # update() only for props whose value differs from what the
                # parent passed last time. Comparing against the child's current
                # state would reset whatever the child changed on its own.
                changed_props = {
                    key: value
                    for key, value in props.items()
                    if key in type(existing).model_fields
                    and (key not in existing._last_props or existing._last_props[key] != value)
                }
                existing._last_props = props
                if changed_props:
                    self._pending_updates.append((existing, changed_props))
                return existing
            # Same id, different class: the old instance leaves and a new one takes the slot.
            log.debug("Component id %s reused by %s, replacing %s", component_id, name, type(existing).__name__)
            self._pending_leaving.extend(self.remove(component_id))

        # A reconnect carries the child's signed state in the parent's join. The
        # parent's props win, everything else is the child's own and comes back.
        if (restored := self.children.pop(component_id, None)) is not None:
            restored_name, restored_state = restored
            if self._same_live_class(restored_name, component_class):
                state = restored_state | state

        live_component = cast(
            LiveComponent,
            component_class._build(
                name,
                state,
                params=self.params,
                user=self.user,
                channel_name=self.channel_name,
                channel_layer=self.channel_layer,
            ),
        )

        # Set parent reference
        live_component._parent_id = parent_id
        live_component._last_props = props

        # Register in components dict
        self.components[live_component.id] = live_component

        # Queue for joined() call after parent render completes
        self._pending_live_components.append(live_component)

        return live_component

    @staticmethod
    def _same_live_class(name: str, component_class: type[LiveComponent]) -> bool:
        try:
            return LiveComponent._resolve_live(name) is component_class
        except LookupError:
            return False

    def get_live_components(self, parent_id: str) -> list[LiveComponent]:
        """Get all LiveComponents under a parent.

        Args:
            parent_id: ID of the parent Component

        Returns:
            List of LiveComponent instances
        """
        return [c for c in self.components.values() if isinstance(c, LiveComponent) and c._parent_id == parent_id]

    def begin_render(self, parent_id: str) -> None:
        """Forget which children ``parent_id`` named; its template pass records them again."""
        self._rendered_children[parent_id] = set()

    def take_lifecycle(self, parent_id: str) -> "LifecycleBatch":
        """What the consumer owes the children of ``parent_id`` after its template ran.

        - ``new``: created in this pass, waiting for ``joined()``
        - ``updates``: existing children whose props changed, with only the changed props
        - ``retired``: children the parent no longer names (and instances displaced by an
          id reuse), already removed from the repository and waiting for ``leaving()``

        Only call this after a render that evaluated the template. A skipped render
        names no children, and treating that as "every child disappeared" would be wrong.
        """
        rendered = self._rendered_children.pop(parent_id, set())

        new = [c for c in self._pending_live_components if c._parent_id == parent_id]
        self._pending_live_components = [c for c in self._pending_live_components if c._parent_id != parent_id]

        updates = [(c, p) for c, p in self._pending_updates if c._parent_id == parent_id]
        self._pending_updates = [(c, p) for c, p in self._pending_updates if c._parent_id != parent_id]

        retired = self._pending_leaving
        self._pending_leaving = []
        for child in self.get_live_components(parent_id):
            if child.id not in rendered:
                retired.extend(self.remove(child.id))

        return LifecycleBatch(new=new, updates=updates, retired=retired)

    async def flush_pending_live_components(self) -> list[LiveComponent]:
        """Call joined()/update() on all pending LiveComponents.

        This should be called after the parent component renders,
        as LiveComponents are created during template rendering (sync context).

        For new components: calls joined()
        For existing components with changed props: calls update()

        Returns:
            List of LiveComponents that had lifecycle methods called
        """
        result: list[LiveComponent] = []

        # Handle new LiveComponents (call joined)
        pending = self._pending_live_components
        self._pending_live_components = []

        for component in pending:
            component.wire.enter_pending_mode()
            await component.joined()
            result.append(component)

        # Handle existing LiveComponents with changed props (call update)
        updates = self._pending_updates
        self._pending_updates = []

        for component, props in updates:
            await component.update(**props)
            if component not in result:
                result.append(component)

        return result

    async def join(
        self,
        name: str,
        state: MessagePayload,
        children: ChildrenRepo | None = None,
    ) -> Component:
        self.children.update(children or {})
        component = await db(self.build)(
            name,
            state,
        )
        # Enter pending mode before joined() to queue stream/push_js operations
        # These will be flushed after send_render() in consumer
        component.wire.enter_pending_mode()
        await component.joined()
        return component

    def register_component(self, component: Component):
        self.components[component.id] = component
        return component

    def remove(self, id: str) -> list[Component]:
        """Remove a component and every LiveComponent nested under it.

        Returns the removed instances, parent first, so the caller can run
        ``leaving()`` on each. Removing an unknown id returns an empty list.
        """
        component = self.components.pop(id, None)
        if component is None:
            return []
        removed = [component]
        for child in self.get_live_components(id):
            removed.extend(self.remove(child.id))
        return removed

    async def dispatch_event(self, id, command, args, kwargs):
        # Security: Validate command name to prevent unauthorized method access
        # This replaces `assert` which can be disabled with `python -O`
        if not self._is_valid_event_handler(command):
            raise ValueError(f"Invalid event handler: {command}")

        component = self.components.get(id)
        if component is None:
            return None

        # Security: Verify the method exists and is callable
        if not hasattr(component, command):
            raise ValueError(f"Unknown event handler: {command}")

        handler = getattr(component, command)
        if not callable(handler):
            raise ValueError(f"Event handler is not callable: {command}")

        # Security: Block methods defined on Component base class (Pydantic methods, etc.)
        if not self._is_user_defined_method(component, command):
            raise ValueError(f"Cannot call base class method: {command}")

        # Handler methods are async (defined in Component subclasses)
        with telemetry.span(
            telemetry.event_handled,
            sender=type(component),
            component_id=component.id,
            component_name=component._name,
            event=command,
        ) as span:
            span.measure(kwargs)
            await handler(*args, **filter_parameters(handler, kwargs))  # type: ignore[misc]
        return component

    @staticmethod
    def _is_valid_event_handler(command: str) -> bool:
        """Check if command name is valid for an event handler.

        Security checks:
        - Must not start with underscore (private/protected methods)
        - Must not be empty
        - Must be a valid Python identifier
        """
        if not command:
            return False
        if command.startswith("_"):
            return False
        if not command.isidentifier():
            return False
        return True

    @staticmethod
    def _is_user_defined_method(component: Component | type[Component], command: str) -> bool:
        """Check if a method name belongs to the user's own component code.

        Accepts an instance or a class, so tooling (``wireview.checks``) can ask
        the same question without building a component.

        A name is exposed only when every class in the MRO that defines it is a
        user class. Any name owned by a framework class blocks the call, even if
        a subclass overrides it, because framework names are API and lifecycle
        surface rather than client events:

        - Pydantic BaseModel methods (model_validate, model_dump, model_post_init)
        - Component internals and lifecycle (dom, destroy, mount, joined)
        - LiveComponent API and lifecycle (send_to_parent, update)
        """
        found_on_user_class = False
        component_class = component if isinstance(component, type) else type(component)

        for cls in component_class.__mro__:
            if command not in cls.__dict__:
                continue
            if _is_framework_class(cls):
                # The name is framework surface, wherever it is also overridden.
                # Pydantic-generated methods (model_post_init) land here too:
                # the metaclass injects them into the user class, but BaseModel
                # owns the name.
                return False
            found_on_user_class = True

        return found_on_user_class

    def components_subscribed_to(self, channel):
        # XXX: There is a list() here because the dict can change size during
        # iteration
        for component in list(self.components.values()):
            if channel in component._subscriptions:
                yield component

    @property
    def subscriptions(self):
        return reduce(
            lambda a, b: a.union(b),
            (component._subscriptions for component in list(self.components.values())),
            set(),
        )
