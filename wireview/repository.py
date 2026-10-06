import builtins
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
from .core import handlers
from .core.component import Component, MessagePayload
from .core.session import SessionView
from .core.state import StateMismatch, state_of
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
        # The restore maps: for each root, the signed states its join carried for
        # the components under it, taken by the instance built under each id. A
        # map lives as long as its root, as a component under it may be drawn
        # only by a later render (an async result, an {% if %}). Only a pass of
        # that root's draws one from it (``_take_restored``): two roots can carry
        # the same id, and each has its own word for it.
        self._restore: dict[str, ChildrenRepo] = {}
        # For an id a template pass draws with {% component %}, the component
        # whose latest pass drew it -- the way to its root's map. It outlives the
        # instance: the page joins the element that pass drew under the id.
        self._built_by: dict[str, str] = {}
        # For each component, the ids in ``_built_by`` that name it: what the end
        # of its next pass looks at (``end_pass``), rather than all of them
        self._drew: dict[str, set[str]] = {}
        # For each component, the ones its pass drew within its own whose
        # LiveComponents its batch settles (``end_inline_pass``), in pass order;
        # and for each of those, what that pass named
        self._inline: dict[str, list[str]] = {}
        self._inline_rendered: dict[str, set[str]] = {}
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
        drawer: Component | None = None,
    ) -> Component:
        """The instance under the state's id, built or reused.

        ``drawer`` is the component whose template pass draws it
        (``{% component %}``), and none for a join's root. A new instance takes
        up what the drawer's root carried for the id (``_take_restored``).
        """
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
                if drawer is not None:
                    self._drawn(component_id, drawer.id)
                return component
            elif drawer is not None:
                if child := self._take_restored(component_id, drawer.id, name):
                    state = child[1] | state
                self._drawn(component_id, drawer.id)

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

    def _drawn(self, component_id: str, drawer_id: str) -> None:
        """Record that a pass of ``drawer_id`` drew ``component_id``, an instance it built or one already here.

        What the drawer's join carried for the id is spent: the page has the
        component now. Further out, an entry stays for the root that carried it
        -- the page may let the element go before that root's render draws it.
        """
        self._built_by[component_id] = drawer_id
        self._drew.setdefault(drawer_id, set()).add(component_id)
        self._rendered_children.setdefault(drawer_id, set()).add(component_id)
        self._restore.get(drawer_id, {}).pop(component_id, None)

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
                self._restore.get(parent_id, {}).pop(component_id, None)
                return existing
            # Same id, different class: the old instance leaves and a new one takes the slot.
            log.debug("Component id %s reused by %s, replacing %s", component_id, name, type(existing).__name__)
            self._pending_leaving.extend(self.remove(component_id))

        # A reconnect carries the child's signed state in the parent's join. The
        # parent's props win, everything else is the child's own and comes back.
        if (restored := self._take_restored(component_id, parent_id)) is not None:
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
        """Forget which children ``parent_id`` named; its template pass records them again.

        A pass of it another's ran before is over: what that one named is no
        longer what the other's batch is to settle (``end_inline_pass``). Its
        own render can come in between: a background task of it renders while
        the other's ``after_render`` hooks await.
        """
        self._rendered_children[parent_id] = set()
        self._inline_rendered.pop(parent_id, None)

    def end_pass(self, drawer_id: str, shown: t.Iterable[str] = ()) -> set[str]:
        """The ids the template pass of ``drawer_id`` that just ran named.

        A component it drew before and not in this pass is the page's to let go:
        ``_built_by`` forgets that the drawer drew it. Every pass ends here, the
        one a parent's pass runs within its own (``{% component %}``) too. That
        one left a nested component's former pass on record, and the leave for
        a component it no longer drew took it for one drawn again.
        """
        rendered = self._rendered_children.pop(drawer_id, set()).union(shown)
        drew = self._drew.pop(drawer_id, set())
        for child_id in drew - rendered:
            if self._built_by.get(child_id) == drawer_id:
                del self._built_by[child_id]
        if drew := {child_id for child_id in drew & rendered if self._built_by.get(child_id) == drawer_id}:
            self._drew[drawer_id] = drew
        return rendered

    def end_inline_pass(self, component_id: str, drawer_id: str | None, shown: t.Iterable[str] = ()) -> None:
        """End the pass of ``component_id`` that ran within the pass of ``drawer_id`` (``{% component %}``).

        A component the page has joined is drawn again only within its drawer's
        pass when the drawer passes it new props: what it draws or stops drawing
        then is known only there. Its LiveComponents' lifecycle -- ``leaving()``
        for one it hid, ``joined()`` for one it shows, ``update()`` for new props
        -- goes into the drawer's batch, and their renders into the drawer's
        frame (``take_lifecycle``). Left for its own next render, the one it hid
        lived on and came back as it was.

        One the page has yet to join is left to that join, as ever: it is what
        completes the instance (docs/design/live-component-ownership.md §3-2),
        and a failed one refuses what it owns. The instance a pass builds again
        after a failed join is a new one, never joined.

        So is everything the drawer's first render draws, even a component the
        connection has joined before. Nothing of that render is on the page yet:
        a boosted visit (or the rejoin after a crash) brings the drawer and the
        component as new elements, and the page joins each. Recorded, the
        drawer's batch would join a LiveComponent that the component's own join,
        right behind, retires and joins anew.

        ``shown`` names the LiveComponents its output shows, as for
        ``take_lifecycle``: one a part kept for a reset temporary assign names
        stays (#111). Settled from what the template named alone, the drawer's
        batch retired it while the page kept it.
        """
        rendered = self.end_pass(component_id, shown)
        component = self.components.get(component_id)
        drawer = self.components.get(drawer_id) if drawer_id is not None else None
        if (
            not self.is_live
            or drawer is None
            or not drawer.wire.instance_announced
            or component is None
            or not component.wire.has_joined
        ):
            return
        inline = self._inline.setdefault(drawer.id, [])
        if component_id not in inline:
            inline.append(component_id)
        self._inline_rendered[component_id] = rendered

    def take_lifecycle(self, parent_id: str, shown: t.Iterable[str] = ()) -> "LifecycleBatch":
        """What the consumer owes the children of ``parent_id`` after its template ran.

        ``shown`` names the LiveComponents the parent's render shows. Those its
        template did not name are in a part kept for a reset temporary assign
        (#111): the page keeps them, so they stay.

        - ``new``: created in this pass, waiting for ``joined()``
        - ``updates``: existing children whose props changed, with only the changed props
        - ``retired``: children the parent no longer names (and instances displaced by an
          id reuse), already removed from the repository and waiting for ``leaving()``
        - ``rerender``: existing children whose slot content changed; no hook, just a render

        The same for the children of each component whose pass ran within this
        one (``end_inline_pass``), and within those, in pass order.

        Only call this after a render that evaluated the template. A skipped render
        names no children, and treating that as "every child disappeared" would be wrong.
        """
        batch = LifecycleBatch(retired=self._pending_leaving)
        self._pending_leaving = []
        self._settle(parent_id, self.end_pass(parent_id, shown), batch)
        return batch

    def _settle(self, parent_id: str, rendered: set[str], batch: "LifecycleBatch") -> None:
        """Put in ``batch`` what the children of ``parent_id`` are owed after a pass that named ``rendered``."""
        batch.new += [c for c in self._pending_live_components if c._parent_id == parent_id]
        self._pending_live_components = [c for c in self._pending_live_components if c._parent_id != parent_id]

        batch.updates += [(c, p) for c, p in self._pending_updates if c._parent_id == parent_id]
        self._pending_updates = [(c, p) for c, p in self._pending_updates if c._parent_id != parent_id]

        batch.rerender += [c for c in self._pending_rerender if c._parent_id == parent_id]
        self._pending_rerender = [c for c in self._pending_rerender if c._parent_id != parent_id]

        for child in self.get_live_components(parent_id):
            if child.id not in rendered:
                batch.retired.extend(self.remove(child.id))

        for inner_id in self._inline.pop(parent_id, []):
            if (inner_rendered := self._inline_rendered.pop(inner_id, None)) is not None:
                self._settle(inner_id, inner_rendered, batch)

    async def flush_pending_live_components(self) -> list[LiveComponent]:
        """Call joined()/update() on all pending LiveComponents (tests only).

        The library settles them in ``WireviewSession.send_render``. This helper
        does not set ``has_joined``, so ``leaving()`` skips what it joined.

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
        before_joined: t.Callable[[Component], t.Awaitable[None]] | None = None,
    ) -> Component:
        """Build the component a join names, mount it and run its ``joined()``.

        ``before_joined`` runs once the mount passed and before ``joined()``:
        the session starts receiving the component's patches there (#178).
        """
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
                if before_joined is not None:
                    await before_joined(component)
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
        replaces in the root's map is the page's word now.
        """
        carried = {child_id: entry for child_id, entry in children.items() if child_id not in self.components}
        if carried:
            self._restore.setdefault(root_id, {}).update(carried)

    def _take_restored(
        self, component_id: str, drawer_id: str, name: str | None = None
    ) -> tuple[str, dict[str, t.Any]] | None:
        """What a pass of ``drawer_id`` draws ``component_id`` from: the first of its roots' entries.

        The roots are the drawer and the ones it was drawn under: a
        LiveComponent's parent, and the component whose pass drew a nested one
        (``_built_by``). The drawn id's entries along that way are spent, the
        one taken and any further out; a map of another root keeps its own.
        That root may still draw the id: a nested component's join right behind
        its root after a reconnect builds what the root has yet to draw, from
        the page's states, and the page lets it go before the root's later
        render draws it again. ``name`` keeps an entry of another class.
        """
        taken = None
        seen: set[str] = set()
        at: str | None = drawer_id
        while at is not None and at not in seen:
            seen.add(at)
            entries = self._restore.get(at, {})
            entry = entries.get(component_id)
            if entry is not None and (name is None or entry[0] == name):
                entries.pop(component_id)
                taken = taken or entry
            component = self.components.get(at)
            at = component._parent_id if isinstance(component, LiveComponent) else self._built_by.get(at)
        return taken

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

    def retire(
        self, id: str, *, failed: bool = False, keep_carried: bool = False, leaves_first: bool = True
    ) -> list[Component]:
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
          the old filler is from before it, or gone. That holds only when the
          old page left before the new one joined: ``leaves_first=False`` says
          the client may join first (``LEAVES_FIRST_SINCE``), and nothing is kept.

        Either way the filler has to be on the page still, and they are dropped
        with it, so nothing kept outlives the page it belongs to.
        """
        component = self.components.get(id)
        removed = self.remove(id, keep_carried=keep_carried)
        if component is not None and (slots := component.wire.slots) is not None:
            filler = component.wire.slots_from
            refilled = leaves_first and filler is not None and filler.wire.born > component.wire.born
            if filler is not None and self._holds(filler) and (failed or refilled):
                self._slots_to_rejoin[id] = (type(component), slots, filler)
        return removed

    def _holds(self, component: Component) -> bool:
        return self.components.get(component.id) is component

    def remove(self, id: str, *, keep_carried: bool = False) -> list[Component]:
        """Remove a component and every LiveComponent nested under it.

        Returns the removed instances, parent first, so the caller can run
        ``leaving()`` on each. Removing an unknown id returns an empty list.
        The removed instances owe no ``joined()``, ``update()`` or render any
        more: a LiveComponent a parent's pass built is no longer pending.

        The restore map the component's join carried goes with it: what it did
        not draw, no later instance under those ids should take up.
        ``keep_carried`` keeps it for the join that comes under the id next --
        the rollback after a crash, which joins with the element as the page
        has it, without what only a later render was to draw. Its leave, or
        its next removal, takes it.

        What the component's passes drew goes too: no pass of it ends any more
        to forget it (``end_pass``). The rollback keeps that as well, as the
        way from what it drew to the map it keeps.
        """
        if not keep_carried:
            self._restore.pop(id, None)
            for child_id in self._drew.pop(id, set()):
                if self._built_by.get(child_id) == id:
                    del self._built_by[child_id]
        self._slots_to_rejoin.pop(id, None)
        # A pass of it that drew others within its own, with no batch to settle
        # them any more (a render that raised): the others settle in their own
        for inner_id in self._inline.pop(id, []):
            self._inline_rendered.pop(inner_id, None)
        component = self.components.pop(id, None)
        if component is None:
            return []
        for kept_id in [kept_id for kept_id, kept in self._slots_to_rejoin.items() if kept[2] is component]:
            del self._slots_to_rejoin[kept_id]
        removed = [component]
        for child in self.get_live_components(id):
            removed.extend(self.remove(child.id))
        gone = {builtins.id(instance) for instance in removed}
        self._pending_live_components = [c for c in self._pending_live_components if builtins.id(c) not in gone]
        self._pending_updates = [(c, p) for c, p in self._pending_updates if builtins.id(c) not in gone]
        self._pending_rerender = [c for c in self._pending_rerender if builtins.id(c) not in gone]
        return removed

    def let_go(self, id: str) -> list[Component]:
        """The page let the element of ``id`` go: ``remove`` it.

        A component that the latest pass of a component still here drew is one
        the page is to be handed again: its leave is for the element an earlier
        render took away, and came after the render that drew the id anew -- a
        reconnect's join, and the work its root's joined() starts again landing
        before the page has patched the join's render in. The page joins the
        new element without the LiveComponents in it, which it let go with the
        old one, and their states go into the drawer's restore map for the
        pass that answers that join.
        """
        drawer_id = self._built_by.get(id)
        removed = self.remove(id)
        if removed and drawer_id is not None and drawer_id in self.components:
            left = {c.id: (type(c)._fqn, state_of(c)) for c in removed if isinstance(c, LiveComponent)}
            self._carry(drawer_id, left)
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
