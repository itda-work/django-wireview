import json
import logging
import typing as t
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import reduce
from typing import cast
from urllib.parse import parse_qsl, urlencode

from channels.db import database_sync_to_async as db
from channels.layers import BaseChannelLayer
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from . import telemetry
from .core import handlers
from .core.component import Component, MessagePayload
from .core.session import SessionView
from .core.state import StateMismatch
from .live_component import LiveComponent
from .utils import filter_parameters

if t.TYPE_CHECKING:
    from .core.live_session import LiveSession
    from .slots import SlotContainer

ChildrenRepo = dict[str, tuple[str, dict[str, t.Any]]]

log = logging.getLogger("wireview")


class InvalidEvent(ValueError):
    """An event names something that is not one of the component's handlers.

    No handler ran, so the component is as it was: the connection answers with
    a log line, not the crash recovery a raising handler gets (#94).
    """


@dataclass
class LifecycleBatch:
    """The lifecycle work one parent's render left behind (``ComponentRepository.take_lifecycle``)."""

    new: list[LiveComponent] = field(default_factory=list)
    updates: list[tuple[LiveComponent, dict[str, t.Any]]] = field(default_factory=list)
    retired: list[Component] = field(default_factory=list)
    # Existing children whose slot content changed: no hook, just a render
    rerender: list[LiveComponent] = field(default_factory=list)

    @property
    def to_render(self) -> list[LiveComponent]:
        """Children that need their own render (a hook ran or their slots changed), each once."""
        seen: dict[str, LiveComponent] = {c.id: c for c in self.new}
        for component, _props in self.updates:
            seen.setdefault(component.id, component)
        for component in self.rerender:
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
        session: t.Any = None,
        connection_id: str | None = None,
        live_session: "LiveSession | None" = None,
        vsn: int = 0,
    ):
        # The caller's dict itself, even an empty one: whoever handed it over may
        # update it in place and expect the repository to see the change.
        self.params = params if params is not None else {}
        # The diff protocol version this connection's client speaks. Zero, the
        # oldest, unless the client said otherwise when it connected (GAP-030).
        self.vsn = vsn
        # The request/connection session. Handed to every component as
        # ``self.session`` and to the ``Meta.on_mount`` hooks as their third argument,
        # read-only in both places (#68).
        self.session: SessionView = SessionView.wrap(session)
        self.channel_name = channel_name
        self.channel_layer = channel_layer
        # Handed to every component's WireviewMeta so upload artefacts can be
        # scoped to the connection that owns them (#77).
        self.connection_id = connection_id
        # The page boundary every component built here belongs to (#58). A page
        # has one: the view decorator sets it on the HTTP side, and on a socket
        # the first join's envelope names it and later joins have to agree.
        self.live_session = live_session
        self.user = user or AnonymousUser()
        self.components: dict[str, Component] = {}
        # The restore map: signed states a join carried for the components under
        # its root, taken by the instance built under each id. An entry lives as
        # long as the root whose join carried it, as a component under it may be
        # drawn only by a later render (an async result, an {% if %}).
        self.children: ChildrenRepo = {}
        self._carried_by: dict[str, str] = {}
        # Entries no build takes while a join runs (``joining``): another root
        # still here carried them, and its later render restores from them.
        self._withheld: set[str] = set()
        # The components whose join failed on this connection, each with the ids
        # its failure removed: itself and the LiveComponents it owned. A parent's
        # later pass builds new instances under those ids that nothing joins.
        self._join_failures: dict[str, set[str]] = {}
        self.is_live = is_live
        # Track LiveComponents that need joined() called after parent renders
        self._pending_live_components: list[LiveComponent] = []
        # Track LiveComponents that need update() called after parent renders
        self._pending_updates: list[tuple[LiveComponent, dict[str, t.Any]]] = []
        # Instances replaced under a reused id; they owe a leaving() call
        self._pending_leaving: list[Component] = []
        # Child ids each parent named in its current template pass
        self._rendered_children: dict[str, set[str]] = {}
        # Existing children whose slot content changed on the parent's re-render
        self._pending_rerender: list[LiveComponent] = []
        # Slots of instances retired so the page can join their id again, with the
        # component that filled them: (class, slots, filler) by id (``retire``)
        self._slots_to_rejoin: dict[str, tuple[type[Component], "SlotContainer", Component]] = {}

    @staticmethod
    def decode_params(params: t.Mapping[str, t.Any]) -> dict[str, t.Any]:
        """Apply the ``.json`` suffix convention to already-parsed pairs.

        Split out of :meth:`extract_params` because params reach the repository
        two ways -- parsed here from a query string on the first load, and handed
        over by the client after a navigation -- and a key that decoded to a dict
        on one path must not arrive as a string on the other. ``get_query_string``
        re-encodes with the same rule, so the round trip closes.
        """
        return {
            key: json.loads(value) if key.endswith(".json") and isinstance(value, str) else value
            for key, value in params.items()
        }

    @staticmethod
    def extract_params(qs: str):
        return ComponentRepository.decode_params(dict(parse_qsl(qs)))

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
                # The id names an instance this connection already holds. Reusing
                # it for another class would let a state signed for one component
                # steer another (#76), so the id has to belong to the same class.
                if type(component) is not Component._resolve(name):
                    raise StateMismatch(
                        f"Component id '{component_id}' is held by {type(component)._fqn}, not by '{name}'",
                        component_id=component_id,
                        signed_name=type(component)._fqn,
                        asked_name=name,
                    )
                # override with the passed state but preserve the rest of the state
                for key, value in state.items():
                    # Call the model validator to convert Django models
                    validator = component.__class__._load_django_models
                    converted = validator({key: value})  # type: ignore[operator]
                    setattr(component, key, converted.get(key, value))
                return component
            elif component_id not in self._withheld and (child := self.children.get(component_id)):
                child_name, child_state = child
                if child_name == name:
                    state = child_state | state
                    self._take_restored(component_id)

        component = Component._build(
            name,
            state,
            params=self.params,
            user=self.user,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
            connection_id=self.connection_id,
            session=self.session,
            live_session=self.live_session,
        )
        return self.register_component(component)

    def build_live_component(
        self,
        name: str,
        state: MessagePayload,
        parent_id: str,
        slots: "SlotContainer | None" = None,
    ) -> LiveComponent:
        """Build a LiveComponent and register it under a parent.

        Args:
            name: LiveComponent class name
            state: Initial state including 'id'
            parent_id: ID of the parent Component
            slots: Slot content from ``{% live_component_block %}``, if any

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
        slot_key = slots.content_key() if slots is not None else None
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
                if slot_key != existing._last_slot_key:
                    existing._last_slot_key = slot_key
                    existing.wire.slots = slots.without_markers() if slots is not None else None
                    if not changed_props:
                        self._pending_rerender.append(existing)
                return existing
            # Same id, different class: the old instance leaves and a new one takes the slot.
            log.debug("Component id %s reused by %s, replacing %s", component_id, name, type(existing).__name__)
            self._pending_leaving.extend(self.remove(component_id))

        # A reconnect carries the child's signed state in the parent's join. The
        # parent's props win, everything else is the child's own and comes back.
        if component_id not in self._withheld and (restored := self._take_restored(component_id)) is not None:
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
                connection_id=self.connection_id,
                session=self.session,
                live_session=self.live_session,
            ),
        )

        # Set parent reference
        live_component._parent_id = parent_id
        live_component._last_props = props
        live_component._last_slot_key = slot_key
        if slots is not None:
            live_component.wire.slots = slots.without_markers()

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
        - ``rerender``: existing children whose slot content changed; no hook, just a render

        Only call this after a render that evaluated the template. A skipped render
        names no children, and treating that as "every child disappeared" would be wrong.
        """
        rendered = self._rendered_children.pop(parent_id, set())

        new = [c for c in self._pending_live_components if c._parent_id == parent_id]
        self._pending_live_components = [c for c in self._pending_live_components if c._parent_id != parent_id]

        updates = [(c, p) for c, p in self._pending_updates if c._parent_id == parent_id]
        self._pending_updates = [(c, p) for c, p in self._pending_updates if c._parent_id != parent_id]

        rerender = [c for c in self._pending_rerender if c._parent_id == parent_id]
        self._pending_rerender = [c for c in self._pending_rerender if c._parent_id != parent_id]

        retired = self._pending_leaving
        self._pending_leaving = []
        for child in self.get_live_components(parent_id):
            if child.id not in rendered:
                retired.extend(self.remove(child.id))

        return LifecycleBatch(new=new, updates=updates, retired=retired, rerender=rerender)

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

        from .live_component import run_updates

        def _raise(cls, component, error):
            raise error

        await run_updates(updates, _raise)
        for component, _props in updates:
            if component not in result:
                result.append(component)

        return result

    async def join(
        self,
        name: str,
        state: MessagePayload,
        children: ChildrenRepo | None = None,
    ) -> Component:
        # Kept for this join alone, whatever it turns out to be
        kept = self._slots_to_rejoin.pop(state.get("id") or "", None)
        component = await db(self.build)(
            name,
            state,
        )
        self._carry(component.id, children or {})
        if kept is not None and component.wire.slots is None and type(component) is kept[0]:
            component.wire.slots, component.wire.slots_from = kept[1], kept[2]
        # Enter pending mode before joined() to queue stream/push_js operations
        # These will be flushed after send_render() in consumer
        component.wire.enter_pending_mode()
        try:
            # The mount hooks are the boundary: a halt skips joined() and gives
            # up the instance. The component still comes back so the caller can
            # flush whatever the hook queued (a redirect), but it is gone from
            # the repository -- ``component_remove()`` only tells the client to
            # drop the element, and an instance left here would keep answering
            # user_event, hook_event, params_changed and uploads (#58, AC3).
            try:
                mounted = await component._mount(self.params, self.session)
            except Exception:
                # A crashing hook gets the same treatment as a refusing one, and
                # then the exception goes on to the caller. Letting it travel
                # without this would leave the instance registered and reachable
                # by events -- an authorization query that fails would be safer
                # for the caller than one that says no.
                self.abandon(component)
                raise
            if mounted:
                await component.joined()
            else:
                self.abandon(component)
        finally:
            component.wire.has_joined = True
        return component

    def _carry(self, root_id: str, children: ChildrenRepo) -> None:
        """Keep the states a join carried for the components under ``root_id``.

        An id already held was built by another pass -- the outer join's, when a
        nested component joins with the states inside it -- and nothing would
        take its entry but the next instance built under the id, once an
        ``{% if %}`` shows it again: that one starts anew. An entry the join
        replaces is the page's word now; one ``joining`` withholds stays the
        root's that carried it.
        """
        for child_id, entry in children.items():
            if child_id not in self.components and child_id not in self._withheld:
                self.children[child_id] = entry
                self._carried_by[child_id] = root_id

    def _take_restored(self, component_id: str) -> tuple[str, dict[str, t.Any]] | None:
        self._carried_by.pop(component_id, None)
        return self.children.pop(component_id, None)

    def _carried_elsewhere(self, component_id: str, root_id: str) -> bool:
        """Whether a root other than ``root_id``, still here, carried the entry of ``component_id``."""
        carrier = self._carried_by.get(component_id)
        return carrier is not None and carrier != root_id and carrier in self.components

    @contextmanager
    def joining(self, root_id: str, children: t.Iterable[str] = ()) -> Iterator[None]:
        """While the join of ``root_id`` runs, leave what another root carried to that root.

        A root that joined carried the entries of the components under it, and
        one it has yet to draw -- the work joined() starts again has not landed --
        keeps its entry for the render that does. The page may join that id
        meanwhile: a nested component right behind its root, before the root's
        render that leaves it out is patched in, or a sticky root's id the next
        page draws as a root of its own. That join goes ahead with the state the
        page sent, and neither it nor the renders that answer it take the other
        root's entries for the id or the ones its join carried; the nested
        component's element goes, and the root's later render restores both.

        Only the id the join is for says so. A root of another id that draws a
        component the other root carried has the page's state for it, and its
        join carries that in place of the other root's (``_carry``).
        """
        self._withheld = (
            {id for id in (root_id, *children) if self._carried_elsewhere(id, root_id)}
            if self._carried_elsewhere(root_id, root_id)
            else set()
        )
        try:
            yield
        finally:
            self._withheld = set()

    def join_failed(self, id: str, removed: list[Component]) -> None:
        """Remember that the join of ``id`` failed, until a join under the id tries again."""
        self._join_failures[id] = {id, *(component.id for component in removed)}

    def retry_join(self, id: str) -> None:
        """A join under ``id`` came: the page tries the component again."""
        self._join_failures.pop(id, None)

    def refused(self, id: str) -> bool:
        """Whether ``id`` names what a failed join left: nothing reaches it.

        A component its parent's pass built again under the id of one whose join
        failed never ran ``joined()``, nor did the LiveComponents it owns -- the
        ones whose parent chain leads to it, not one in its slot, which the
        component filling the slot owns. Before the pass builds them again, the
        ids the failure removed name nothing, and are refused too.
        """
        component = self.components.get(id)
        if component is None:
            return any(id in removed for removed in self._join_failures.values())
        return self.root_of(component).id in self._join_failures

    def reachable(self, id: str) -> Component | None:
        """The instance under ``id`` that server code may run, or ``None``: unknown, or ``refused``.

        Every path that calls a component's code -- a hook, an upload, a
        broadcast, ``params_changed``, ``wire.defer``, a parent's
        ``update_live_component`` -- finds its instance here, so what a failed
        join left runs nothing until the page joins it again. An event asks
        ``refused`` first, as it answers even then, and a render of it on its
        own is refused in ``WireviewSession.send_render``.
        """
        component = self.components.get(id)
        return None if component is None or self.refused(id) else component

    def reachable_components(self) -> list[Component]:
        """Every instance ``reachable`` would return, in registration order."""
        return [component for component in list(self.components.values()) if not self.refused(component.id)]

    def root_of(self, component: Component) -> Component:
        """The component whose join made ``component``'s instance: itself, or a LiveComponent's owner."""
        root = component
        while isinstance(root, LiveComponent) and (parent := self.components.get(root._parent_id or "")) is not None:
            root = parent
        return root

    def abandon(self, component: Component) -> None:
        """Give up on a component the boundary refused: no render, no event target."""
        component.wire.freeze()
        self.remove(component.id)

    def register_component(self, component: Component):
        self.components[component.id] = component
        return component

    def retire(self, id: str, *, failed: bool = False, keep_carried: bool = False) -> list[Component]:
        """Remove ``id`` as :meth:`remove` does, for the page to join it again.

        Slots are not part of the signed state: only the component that filled
        them knows them, and its pass gave them to this instance. Without them
        the instance joined next renders its slots empty -- text, LiveComponents
        and nested components gone from the page. So they are kept for that
        join, when it is the same element joining again:

        - ``failed``: the page joins the element again to recover from an error.
        - Otherwise new DOM arrived under the id (a boosted visit). It is the
          same element only if a component that came after this instance -- the
          new page's -- filled it in its pass. Another page can draw a component
          of the same class and id with no fill, or a different one, and then
          the old filler is from before it, or gone.

        Either way the filler has to be on the page still, and they are dropped
        with it, so nothing kept outlives the page it belongs to.
        """
        component = self.components.get(id)
        removed = self.remove(id, keep_carried=keep_carried)
        if component is not None and (slots := component.wire.slots) is not None:
            filler = component.wire.slots_from
            if filler is not None and self._holds(filler) and (failed or filler.wire.born > component.wire.born):
                self._slots_to_rejoin[id] = (type(component), slots, filler)
        return removed

    def _holds(self, component: Component) -> bool:
        return self.components.get(component.id) is component

    def remove(self, id: str, *, keep_carried: bool = False) -> list[Component]:
        """Remove a component and every LiveComponent nested under it.

        Returns the removed instances, parent first, so the caller can run
        ``leaving()`` on each. Removing an unknown id returns an empty list.

        The restore map entries the component's join carried go with it: what
        it did not draw, no later instance under those ids should take up.
        ``keep_carried`` keeps them for the join that comes under the id next --
        the rollback after a crash, which joins with the element as the page
        has it, without what only a later render was to draw. Its leave, or
        its next removal, takes them.
        """
        if not keep_carried:
            for child_id, root_id in list(self._carried_by.items()):
                if root_id == id:
                    self._take_restored(child_id)
        self._slots_to_rejoin.pop(id, None)
        component = self.components.pop(id, None)
        if component is None:
            return []
        for kept_id in [kept_id for kept_id, kept in self._slots_to_rejoin.items() if kept[2] is component]:
            del self._slots_to_rejoin[kept_id]
        removed = [component]
        for child in self.get_live_components(id):
            removed.extend(self.remove(child.id))
        return removed

    async def dispatch_event(self, id, command, args, kwargs):
        # Security: Validate command name to prevent unauthorized method access
        # This replaces `assert` which can be disabled with `python -O`
        if not self._is_valid_event_handler(command):
            raise InvalidEvent(f"Invalid event handler: {command}")

        component = self.reachable(id)
        if component is None:
            return None

        # Security: Verify the method exists and is callable
        if not hasattr(component, command):
            raise InvalidEvent(f"Unknown event handler: {command}")

        handler = getattr(component, command)
        if not callable(handler):
            raise InvalidEvent(f"Event handler is not callable: {command}")

        # Security: Block methods defined on Component base class (Pydantic methods, etc.)
        if not self._is_user_defined_method(component, command):
            raise InvalidEvent(f"Cannot call base class method: {command}")

        # An attached handle_event hook sees the event first and may stop it (#110).
        if (await component._run_hooks("handle_event", command, kwargs)).get("halt"):
            return component

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
        """Whether ``command`` can name an event handler (``wireview.core.handlers``)."""
        return handlers.is_valid_event_handler(command)

    @staticmethod
    def _is_user_defined_method(component: Component | type[Component], command: str) -> bool:
        """Whether ``command`` belongs to the user's own component code (``wireview.core.handlers``).

        Accepts an instance or a class, so tooling (``wireview.checks``) can ask
        the same question without building a component.
        """
        return handlers.is_user_defined_method(component, command)

    def components_subscribed_to(self, channel):
        for component in self.reachable_components():
            if channel in component.get_subscriptions():
                yield component

    @property
    def subscriptions(self):
        return reduce(
            lambda a, b: a.union(b),
            (component.get_subscriptions() for component in self.reachable_components()),
            set(),
        )
