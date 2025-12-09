import json
import typing as t
from functools import reduce
from urllib.parse import parse_qsl, urlencode

from channels.db import database_sync_to_async as db
from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from .component import Component, MessagePayload
from .live_component import LiveComponent
from .utils import filter_parameters

ChildrenRepo = dict[str, tuple[str, dict[str, t.Any]]]


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

        # Check if already registered (re-render case)
        if existing := self.components.get(component_id):
            if isinstance(existing, LiveComponent):
                # Update with new state (props changed)
                for key, value in state.items():
                    if key != "id" and key in existing.model_fields:
                        setattr(existing, key, value)
                return existing

        # Resolve and build LiveComponent
        component_class = LiveComponent._resolve_live(name)

        component = component_class._build(
            name,
            state,
            params=self.params,
            user=self.user,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )

        # Set parent reference
        component._parent_id = parent_id

        # Register in components dict
        self.components[component.id] = component

        return component

    def get_live_components(self, parent_id: str) -> list[LiveComponent]:
        """Get all LiveComponents under a parent.

        Args:
            parent_id: ID of the parent Component

        Returns:
            List of LiveComponent instances
        """
        return [c for c in self.components.values() if isinstance(c, LiveComponent) and c._parent_id == parent_id]

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

    def remove(self, id):
        self.components.pop(id, None)

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
    def _is_user_defined_method(component: Component, command: str) -> bool:
        """Check if method is defined on user's subclass, not on Component base.

        This blocks access to:
        - Pydantic BaseModel methods (model_validate, model_dump, etc.)
        - Component internal methods (dom, destroy, etc.)
        - Only allows methods defined by the user in their component subclass
        """
        # Get the method resolution order (MRO) for the component
        component_class = type(component)

        # Check if the method is defined on the user's class (not inherited from Component)
        for cls in component_class.__mro__:
            if cls is Component:
                # We've reached Component base class
                # If method is defined here or below, it's not user-defined
                if command in cls.__dict__:
                    return False
                break
            if command in cls.__dict__:
                # Method is defined on a class before Component in MRO
                # This is the user's class or their parent classes
                return True

        return False

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
