import logging
import secrets
import typing as t

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.signing import BadSignature, SignatureExpired
from django.utils.datastructures import MultiValueDict

from wireview.component import Component

from . import serializer
from .core.session import load_session
from .core.state import LegacyState, StateMismatch, unsign_state
from .core.transport import ChannelsOutbound, Outbound
from .features import upload_store
from .features.uploads import upload_group_name
from .live_component import LiveComponent
from .repository import ComponentRepository
from .utils import parse_request_data

log = logging.getLogger("wireview")


class ChildComponent(t.TypedDict):
    name: str
    state: str


def _reload_payload(name: str, error: BadSignature) -> dict[str, t.Any]:
    """Log a rejected root state and describe it for the client's ``reload``.

    An expiry or a pre-upgrade page is ordinary traffic (INFO); a class that
    does not match the signature, or a signature that does not verify, is not
    (WARNING).
    """
    if isinstance(error, SignatureExpired):
        log.info("JOIN %s rejected: the signed state expired", name)
        return {"id": None, "reason": "expired"}
    if isinstance(error, LegacyState):
        log.info("JOIN %s rejected: pre-v1 signed state and STATE_ACCEPT_LEGACY is off", name)
        return {"id": None, "reason": "legacy"}
    if isinstance(error, StateMismatch):
        log.warning(
            "JOIN %s rejected: state signed for %s presented as %s (component %s)",
            name,
            error.signed_name or "<unresolved>",
            error.asked_name or name,
            error.component_id or "<unknown>",
        )
        return {"id": error.component_id or None, "reason": "invalid"}
    log.warning("JOIN %s rejected: %s", name, error)
    return {"id": None, "reason": "invalid"}


class WireviewConsumer(AsyncJsonWebsocketConsumer):
    outbound: Outbound

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        # The only object that knows this session is a Channels WebSocket.
        self.outbound = ChannelsOutbound(self)
        # Minted in connect(). Unlike channel_name it is safe to put in a URL, and
        # it owns this connection's upload registries, tokens and progress group
        # (#77). Empty until connect() runs, which is what bare test consumers get.
        self.connection_id: str = ""
        # Whether this connection joined its upload progress group. Set the first
        # time a registry is registered, so a page without uploads costs no
        # group_add on the channel layer.
        self._upload_group_subscribed: bool = False

    @property
    def user(self):
        return self.scope.get("user") or AnonymousUser()

    async def connect(self):
        await super().connect()
        self.subscriptions = set()
        self.query_string: str = ""
        self.connection_id = secrets.token_urlsafe(16)
        self._upload_group_subscribed = False
        self.repo = ComponentRepository(
            is_live=True,
            user=self.user,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
            # Read once, off the event loop. A component's ``self.session`` is a
            # dict lookup after this; reading the store lazily from an async
            # handler would raise SynchronousOnlyOperation instead (#68).
            session=await load_session(self.scope.get("session")),
            connection_id=self.connection_id,
        )

    async def disconnect(self, code):
        """Handle WebSocket disconnect.

        Calls leaving() on all registered components to allow cleanup,
        then removes all channel subscriptions.
        """
        log.debug(f"<<< DISCONNECT {code}")

        await self._call_leaving(list(self.repo.components.values()))
        await self._release_connection_uploads()

        # Cleanup subscriptions
        for channel in self.subscriptions:
            log.debug(f"::: UNSUBSCRIBE {self.channel_name} from {channel}")
            await self.outbound.unsubscribe(channel)
        self.subscriptions.clear()

        await super().disconnect(code)

    # Fronted commands

    async def receive_json(self, content: dict, **kwargs) -> None:  # type: ignore[override]
        await getattr(self, f"command_{content['command']}")(**content["payload"])

    async def command_join(
        self,
        name: str,
        state: str,
        children: dict[str, ChildComponent] | None = None,
    ):
        try:
            decoded_state: dict[str, t.Any] = unsign_state(state, name)
        except BadSignature as e:
            # Nothing is mounted. A full page load is the recovery: the server
            # re-renders with the current auth context and issues fresh tokens.
            # Sending ``remove`` instead would silently empty an old page after
            # a deploy.
            await self.send_command("reload", _reload_payload(name, e))
            return
        decoded_children: dict[str, tuple[str, dict[str, t.Any]]] = {}
        for child_id, (child_name, child_state) in (children or {}).items():
            try:
                decoded_children[child_id] = (child_name, unsign_state(child_state, child_name))
            except BadSignature as e:
                # The child is rebuilt from the parent's template props instead.
                log.warning("JOIN %s: dropping child %s (%s) from the restore map: %s", name, child_id, child_name, e)
        log.debug(f"<<< JOIN {name} {decoded_state}")
        component_id = decoded_state.get("id", "")
        existing = self.repo.get(component_id)
        if isinstance(existing, LiveComponent):
            # A LiveComponent is owned by its parent: the parent's join carried its
            # state and its lifecycle runs with the parent's render. Current clients
            # do not send this; a cached older script still might.
            log.debug("Ignoring direct join for LiveComponent %s", component_id)
            return
        if existing is not None and existing.wire.has_joined:
            # The client only joins an element whose data-is-live is false, so a
            # second join for an id that already joined means new DOM arrived for
            # it (boost navigation). The old instance leaves with its children and
            # a fresh one joins; joined() stays once per instance. An instance a
            # parent's template pass created but that never joined is adopted by
            # repo.join() instead, as its own join is what completes it.
            log.debug("Re-join of %s: retiring the previous instance", component_id)
            removed = self.repo.remove(component_id)
            await self._call_leaving(removed)
            self._release_uploads(removed)
        try:
            component = await self.repo.join(
                name,
                decoded_state,
                children=decoded_children,
            )
        except Exception as e:
            log.exception(e)
            if id := decoded_state.get("id"):
                await self.component_remove(id)
        else:
            # Hear this connection's upload progress, if the component has uploads
            await self._subscribe_upload_group(component)
            await self.send_render(component)

            # Call params_changed if URL has params (initial load)
            if self.repo.params:
                uri = f"?{self.repo.get_query_string()}"
                await component.params_changed(dict(self.repo.params), uri)
                await self.send_render(component)

            # Subscriptions first, then the operations queued during joined():
            # a broadcast queued there must not go out before this connection
            # has joined the group it expects to hear it on.
            await self.after_mutation_chores()
            await component.wire.flush_pending()

    async def command_leave(self, id):
        """The client saw a component disappear from the DOM.

        The component and every LiveComponent nested under it get their
        ``leaving()`` hook, their uploads are ended, and the subscriptions the
        connection no longer needs are dropped.
        """
        log.debug(f"<<< LEAVE {id}")
        removed = self.repo.remove(id)
        await self._call_leaving(removed)
        self._release_uploads(removed)
        await self.after_mutation_chores()

    async def _call_leaving(self, components: list[Component]) -> None:
        """Run ``leaving()`` on each component, logging instead of propagating errors."""
        for component in components:
            try:
                await component.leaving()
            except Exception as e:
                log.exception(f"Error in {component._name}.leaving(): {e}")

    async def command_params_changed(self, params: dict[str, str], uri: str):
        """Handle URL parameter changes from client.

        Called when:
        - push_to() or replace_to() triggers URL change
        - Browser back/forward navigation
        - Initial page load with params
        """
        log.debug(f"<<< PARAMS-CHANGED {uri} {params}")

        # Update repository params
        self.repo.params.clear()
        self.repo.params.update(params)

        # Update query_string for send_query_string() sync
        self.query_string = self.repo.get_query_string()

        # Call params_changed on all live components and re-render
        for component in list(self.repo.components.values()):
            await component.params_changed(params, uri)
            await self.send_render(component)

        await self.after_mutation_chores()

    async def command_query_string(self, qs: str):
        """Legacy command - delegates to command_params_changed."""
        params = self.repo.extract_params(qs)
        uri = f"?{qs}" if qs else ""
        await self.command_params_changed(params, uri)

    async def command_user_event(self, id, command, implicit_args, explicit_args):
        kwargs = dict(parse_request_data(MultiValueDict(implicit_args)), **explicit_args)
        log.debug(f"<<< USER-EVENT {id} {command} {kwargs}")
        component = await self.repo.dispatch_event(id, command, [], kwargs)
        if component:
            await self.send_render(component)
            await self.after_mutation_chores()

    async def command_hook_event(
        self,
        component_id: str,
        hook_id: str,
        event: str,
        payload: dict,
        ref: str | None = None,
    ):
        """Handle hook event from client JavaScript hooks.

        Args:
            component_id: ID of the component containing the hook
            hook_id: Unique identifier of the hook instance
            event: Event name sent by the hook
            payload: Event data from the hook
            ref: Optional reference for callback response
        """
        log.debug(f"<<< HOOK-EVENT {component_id} {event} {payload}")
        component = self.repo.get(component_id)
        if not component:
            return

        # Call the component's hook event handler
        response = await component.handle_hook_event(hook_id, event, payload)

        # Send reply if ref was provided (callback expected)
        if ref is not None:
            await self.send_command("hook_reply", {"ref": ref, "response": response})

        # Re-render component if state may have changed
        await self.send_render(component)
        await self.after_mutation_chores()

    # Upload commands

    async def command_upload_register(
        self,
        id: str,
        name: str,
        entries: list[dict[str, t.Any]],
    ):
        """Handle upload registration from client."""
        from .features.uploads import UploadEntry, UploadOp, UploadStatus

        log.debug(f"<<< UPLOAD-REGISTER {id} {name} ({len(entries)} entries)")

        component = self.repo.get(id)
        if not component:
            return

        registry = getattr(component, "_upload_registry", None)
        if not registry or name not in registry.configs:
            return

        for entry_data in entries:
            entry = UploadEntry(
                ref=entry_data["ref"],
                upload_name=name,
                client_name=entry_data["name"],
                client_size=entry_data["size"],
                client_type=entry_data["type"],
            )

            try:
                token = registry.add_entry(name, entry)

                if entry.status == UploadStatus.ERROR:
                    # Validation failed
                    await self.send_command(
                        "upload_op",
                        UploadOp(
                            op="error",
                            upload=name,
                            ref=entry.ref,
                            data={"errors": entry.errors},
                        ).to_payload(),
                    )
                else:
                    config = registry.configs[name]

                    # Check for external upload
                    if config.external:
                        # Call external callback to get presigned URL
                        try:
                            meta = config.external(entry, component)
                            await self.send_command(
                                "upload_op",
                                UploadOp(
                                    op="registered",
                                    upload=name,
                                    ref=entry.ref,
                                    data={
                                        "external": meta.to_client_dict(),
                                    },
                                ).to_payload(),
                            )
                        except Exception as e:
                            log.error(f"External upload callback error: {e}")
                            await self.send_command(
                                "upload_op",
                                UploadOp(
                                    op="error",
                                    upload=name,
                                    ref=entry.ref,
                                    data={"errors": [str(e)]},
                                ).to_payload(),
                            )
                    else:
                        # Send token for HTTP upload (chunked)
                        await self.send_command(
                            "upload_op",
                            UploadOp(
                                op="registered",
                                upload=name,
                                ref=entry.ref,
                                data={
                                    "token": token,
                                    "chunk_size": config.chunk_size,
                                },
                            ).to_payload(),
                        )
            except ValueError as e:
                await self.send_command(
                    "upload_op",
                    UploadOp(
                        op="error",
                        upload=name,
                        ref=entry_data["ref"],
                        data={"errors": [str(e)]},
                    ).to_payload(),
                )

        # Re-render to update uploads property
        await self.send_render(component)

    async def command_upload_cancel(self, id: str, name: str, ref: str):
        """Handle upload cancellation from client."""
        log.debug(f"<<< UPLOAD-CANCEL {id} {name} {ref}")

        component = self.repo.get(id)
        if component:
            await component.cancel_upload(name, ref)
            await self.send_render(component)

    async def command_upload_complete(self, id: str, name: str, ref: str):
        """Handle upload completion notification from client.

        The client sends this after the chunk endpoint told it the upload was
        complete, and the endpoint may be on another worker, so this arrives
        without any ordering relative to the ``upload.completed`` the endpoint
        published. Neither is trusted on its own: the entry is promoted only if
        the bytes are actually on disk (#83).
        """
        from .features.uploads import UploadOp

        log.debug(f"<<< UPLOAD-COMPLETE {id} {name} {ref}")

        component = self.repo.get(id)
        if not component:
            return

        registry = getattr(component, "_upload_registry", None)
        if not registry:
            return

        entry = registry.get_entry(name, ref)
        if not entry:
            return

        if not self._promote_completed(entry):
            log.warning(f"Upload {name}/{ref} reported complete, but its file is not: {entry.temp_path}")
            await self.send_command(
                "upload_op",
                UploadOp(
                    op="error",
                    upload=name,
                    ref=ref,
                    data={"errors": ["Upload did not finish on the server"]},
                ).to_payload(),
            )
            await self.send_render(component)
            return

        # Notify client
        await self.send_command(
            "upload_op",
            UploadOp(
                op="complete",
                upload=name,
                ref=ref,
            ).to_payload(),
        )

        # Call optional callback on component
        if hasattr(component, "on_upload_complete"):
            callback = getattr(component, "on_upload_complete")
            await callback(name, entry)

        # Re-render
        await self.send_render(component)

    # Component commands

    async def message_from_component(self, data):
        await getattr(self, f"component_{data['command']}")(**data["kwargs"])

    async def component_dispatch_event(self, id, command, args, kwargs):
        log.debug(f"<<< EVENT {id} {command} {args} {kwargs}")
        component = await self.repo.dispatch_event(id, command, args, kwargs)
        if component is not None:
            await self.send_render(component)
        await self.after_mutation_chores()

    async def component_remove(self, id):
        log.debug(f">>> REMOVE {id}")
        await self.send_command("remove", {"id": id})

    async def component_send_render(self, id):
        log.debug(f">>> SEND-RENDER {id}")
        if component := self.repo.get(id):
            await self.send_render(component)

    async def component_dom_action(self, action, id, html):
        log.debug(f">>> DOM {action.upper()} {id}")
        await self.send_command(action, {"id": id, "html": html})

    async def component_stream_op(self, op, stream, items, at, limit=0):
        log.debug(f">>> STREAM {op.upper()} {stream}")
        payload = {"op": op, "stream": stream, "items": items, "at": at}
        if limit:
            payload["limit"] = limit
        await self.send_command("stream_op", payload)

    async def component_scroll_into_view(self, id, behavior, block, inline):
        log.debug(f">>> SCROLL-INTO-VIEW {id}")
        await self.send_command(
            "scroll_into_view",
            {"id": id, "behavior": behavior, "block": block, "inline": inline},
        )

    async def component_focus_on(self, selector):
        log.debug(f'>>> FOCUS ON "{selector}"')
        await self.send_command("focus_on", {"selector": selector})

    async def component_url_change(self, command: str, url: str):
        log.debug(f'>>> URL {command.upper()} "{url}"')
        await self.send_command("url_change", {"url": url, "command": command})

    async def component_upload_op(self, op: str, upload: str, ref: str | None = None, **data):
        """Handle upload operation from component."""
        log.debug(f">>> UPLOAD-OP {op.upper()} {upload}")
        payload = {"op": op, "upload": upload}
        if ref:
            payload["ref"] = ref
        payload.update(data)
        await self.send_command("upload_op", payload)

    async def component_exec_js(self, id: str, commands: list):
        """Execute JS commands on the client."""
        log.debug(f">>> EXEC-JS {id}")
        await self.send_command("exec_js", {"id": id, "commands": commands})

    async def component_push_event(
        self,
        component_id: str,
        event: str,
        payload: dict,
        hook_id: str | None = None,
    ):
        """Push an event from server to client-side hooks.

        Args:
            component_id: ID of the component containing the hooks
            event: Event name to dispatch
            payload: Event data
            hook_id: Target specific hook (None = broadcast to all)
        """
        log.debug(f">>> PUSH-EVENT {event} {payload}")
        await self.send_command(
            "push_event",
            {
                "component_id": component_id,
                "hook_id": hook_id,
                "event": event,
                "payload": payload,
            },
        )

    async def component_update_live_component(
        self,
        parent_id: str,
        live_component_id: str,
        assigns: dict,
    ):
        """Update a LiveComponent from its parent.

        Args:
            parent_id: ID of the parent component (for verification)
            live_component_id: ID of the target LiveComponent
            assigns: New values to update
        """
        log.debug(f">>> UPDATE-LIVE-COMPONENT {live_component_id} {assigns}")

        # Get the LiveComponent
        component = self.repo.get(live_component_id)
        if component is None:
            log.warning(f"LiveComponent {live_component_id} not found")
            return

        # Verify it's a LiveComponent
        if not isinstance(component, LiveComponent):
            log.warning(f"Component {live_component_id} is not a LiveComponent")
            return

        # Verify parent relationship
        if component._parent_id != parent_id:
            log.warning(
                f"LiveComponent {live_component_id} parent mismatch: expected {component._parent_id}, got {parent_id}"
            )
            return

        # Call update callback
        await component.update(**assigns)

        # Re-render the LiveComponent
        await self.send_render(component)

    # Channel layer messages for uploads
    #
    # The chunk endpoint holds no state (#83), so these are not only relayed to
    # the browser: they are how the entry on this worker learns what happened to
    # it. Without that, ``this.uploads`` and ``consume_uploads()`` would describe
    # an upload that never started.

    async def upload_progress(self, event: dict[str, t.Any]):
        """Handle progress from channel layer (sent by HTTP upload view)."""
        from .features.uploads import UploadStatus

        log.debug(f">>> UPLOAD-PROGRESS {event['upload']} {event['ref']} {event['progress']}%")
        entry = self._find_upload_entry(event)
        if entry is not None:
            entry.bytes_received = event.get("bytes_received", entry.bytes_received)
            entry.progress = event["progress"]
            if entry.status is UploadStatus.PENDING:
                entry.status = UploadStatus.UPLOADING
        await self.send_command(
            "upload_op",
            {
                "op": "progress",
                "upload": event["upload"],
                "ref": event["ref"],
                "progress": event["progress"],
                "bytes_received": event.get("bytes_received", 0),
            },
        )

    async def upload_completed(self, event: dict[str, t.Any]):
        """Handle the last chunk landing, wherever it landed.

        State only. The browser is told an upload finished by its own
        ``upload_complete`` command, which also runs ``on_upload_complete`` --
        firing it from here as well would run it twice.
        """
        log.debug(f">>> UPLOAD-COMPLETED {event['upload']} {event['ref']}")
        entry = self._find_upload_entry(event)
        if entry is not None:
            entry.bytes_received = event.get("bytes_received", entry.bytes_received)
            self._promote_completed(entry)

    async def upload_error(self, event: dict[str, t.Any]):
        """Handle error from channel layer (sent by HTTP upload view)."""
        from .features.uploads import UploadStatus

        log.debug(f">>> UPLOAD-ERROR {event['upload']} {event['ref']}")
        errors = event.get("errors", [])
        entry = self._find_upload_entry(event)
        if entry is not None:
            entry.status = UploadStatus.ERROR
            entry.errors.extend(e for e in errors if e not in entry.errors)
        await self.send_command(
            "upload_op",
            {
                "op": "error",
                "upload": event["upload"],
                "ref": event["ref"],
                "errors": errors,
            },
        )

    def _find_upload_entry(self, event: dict[str, t.Any]):
        """The entry a broker message is about, or None if it is not ours.

        A message names its component because one connection's group carries
        every upload it owns.
        """
        component = self.repo.get(event.get("component", ""))
        registry = getattr(component, "_upload_registry", None) if component else None
        if registry is None:
            return None
        return registry.get_entry(event["upload"], event["ref"])

    @staticmethod
    def _promote_completed(entry) -> bool:
        """Mark an entry complete, but only with the bytes to back it up.

        Both the client's ``upload_complete`` and the endpoint's
        ``upload.completed`` claim an upload finished, and they can arrive in
        either order or, for the first, without the second ever arriving. The
        file itself settles it.

        Returns:
            True if the entry is complete (now or already), False if the bytes
            are not there
        """
        from .features.uploads import UploadStatus

        if entry.status in (UploadStatus.COMPLETED, UploadStatus.CONSUMED):
            return True
        if entry.status in (UploadStatus.CANCELLED, UploadStatus.ERROR):
            return False
        if entry.external:
            # The bytes went straight to S3/GCS; no worker ever saw them, and the
            # presigned PUT succeeding is the only evidence there is.
            entry.status = UploadStatus.COMPLETED
            entry.progress = 100
            return True
        path = entry.temp_path
        if path is None:
            return False
        try:
            written = path.stat().st_size
        except OSError:
            return False
        if written < entry.client_size:
            return False
        entry.bytes_received = written
        entry.status = UploadStatus.COMPLETED
        entry.progress = 100
        return True

    # Upload lifecycle

    async def _subscribe_upload_group(self, component) -> None:
        """Join this connection's upload progress group, if the component uploads.

        Only ``allow_upload()`` creates a registry, so a page without uploads costs
        nothing here. One group per connection, joined lazily so a no-upload page
        adds no ``group_add`` on the channel layer. The group is deliberately
        outside ``self.subscriptions`` -- that set mirrors what the components ask
        for, and this one lives as long as the connection does.

        Nothing is registered anywhere: since #83 the chunk endpoint finds an
        upload by computing where it lives, not by looking it up in this process.
        """
        if getattr(component, "_upload_registry", None) and not self._upload_group_subscribed:
            await self.outbound.subscribe(upload_group_name(self.connection_id))
            self._upload_group_subscribed = True

    @staticmethod
    def _release_uploads(components: list[Component]) -> None:
        """End the uploads of components that just left, and drop their files.

        Each entry leaves a cancellation marker rather than only losing its file:
        a chunk of it may be in flight on another worker, which has no other way
        to learn that the component is gone (#83).
        """
        for component in components:
            registry = getattr(component, "_upload_registry", None)
            if registry:
                registry.cleanup_all()

    async def _release_connection_uploads(self) -> None:
        """End every upload this connection owns and drop its group.

        The single cleanup entry point for the connection going away: disconnect
        calls it, and discarding a connection on logout (#58) will reuse it. The
        connection's whole chunk directory goes, marker included, so a chunk that
        was in flight on another worker finds nowhere to write.
        """
        await sync_to_async(upload_store.discard_connection)(self.connection_id)
        if self._upload_group_subscribed:
            await self.outbound.unsubscribe(upload_group_name(self.connection_id))
            self._upload_group_subscribed = False

    # Incoming messages from subscriptions

    async def model_mutation(self, data):
        # The signature here is coupled to:
        #   `wireview.auto_broadcast.notify_mutation`
        await self._dispatch_notifications(
            "mutation",
            data["channel"],
            {
                "instance": serializer.decode(data["instance"]),
                "action": data["action"],
            },
        )

    async def notification(self, data):
        # The signature here is coupled to:
        #   `wireview.utils.send_notification`
        await self._dispatch_notifications("notification", data["channel"], data["kwargs"])

    async def _dispatch_notifications(self, receiver: str, channel: str, kwargs: dict[str, t.Any]):
        for component in self.repo.components_subscribed_to(channel):
            await getattr(component, receiver)(channel, **kwargs)
            await self.send_render(component)
        await self.after_mutation_chores()

    # Reply to front-end

    # How deep {% live_component %} may nest. Deeper than this is almost certainly a
    # template that renders a component inside itself.
    MAX_LIVE_COMPONENT_DEPTH = 8

    async def send_render(self, component: Component):
        """Render a component and the LiveComponents its template names, then send one message.

        The parent's template pass only registers its children and leaves a
        reference where each one goes. Here, after that pass, each new child
        gets ``joined()``, each child whose props changed gets ``update()``,
        each child the parent stopped naming gets ``leaving()``, and the children
        that ran a hook are rendered (recursively, for grandchildren). Their
        diffs travel in the same ``render`` frame as the parent's under
        ``children``, so the client registers them before it patches the DOM.
        """
        diff, children, settled = await self._render_tree(component)
        if diff is not None or children:
            log.debug(f">>> RENDER {component._name} {component.id} (+{len(children)} children)")
            payload: dict[str, t.Any] = {"id": component.id, "diff": diff}
            if children:
                payload["children"] = children
            await self.send_command("render", payload)
        if settled:
            # Subscriptions before the children's queued operations, for the same
            # reason as in command_join.
            await self.update_to_which_channels_im_subscribed_to()
            for child in settled:
                await child.wire.flush_pending()

    async def _render_tree(
        self, component: Component, depth: int = 0
    ) -> tuple[t.Any, dict[str, t.Any], list[LiveComponent]]:
        """Render one component and settle the children its template named.

        Returns the component's diff, a flat ``{id: diff}`` of every descendant
        rendered along the way, and those descendants in render order.
        """
        repo = self.repo
        repo.begin_render(component.id)
        diff = await component._render_diff(repo)
        # Clear temporary assigns after rendering to free memory
        # This is called regardless of whether diff was sent (skip_render case)
        component._clear_temporary_assigns()

        children: dict[str, t.Any] = {}
        settled: list[LiveComponent] = []
        if not component.wire.template_evaluated:
            return diff, children, settled
        if depth >= self.MAX_LIVE_COMPONENT_DEPTH:
            log.error("LiveComponent nesting deeper than %d under %s; not rendering further", depth, component.id)
            return diff, children, settled

        batch = repo.take_lifecycle(component.id)
        await self._call_leaving(batch.retired)
        self._release_uploads(batch.retired)
        for child in batch.new:
            child.wire.enter_pending_mode()
            try:
                # Same boundary as the parent's join: the child's _on_mount hooks
                # run first, and a halt skips joined() but still renders the child.
                if await child._mount(repo.params, repo.session):
                    await child.joined()
            except Exception as e:
                log.exception(f"Error in {child._name}.joined(): {e}")
            finally:
                child.wire.has_joined = True
            # joined() is where allow_upload() runs, so a LiveComponent's registry
            # only exists from here on. Without this a nested component's uploads
            # were never heard from (#77).
            await self._subscribe_upload_group(child)
        for child, props in batch.updates:
            child.wire.enter_pending_mode()
            try:
                await child.update(**props)
            except Exception as e:
                log.exception(f"Error in {child._name}.update(): {e}")
        for child in batch.to_render:
            child_diff, grandchildren, descendants = await self._render_tree(child, depth + 1)
            children[child.id] = child_diff
            children.update(grandchildren)
            settled.append(child)
            settled.extend(descendants)
        return diff, children, settled

    async def send_command(self, command, payload):
        await self.outbound.send_command(command, payload)

    async def after_mutation_chores(self):
        await self.update_to_which_channels_im_subscribed_to()
        await self.send_query_string()

    async def update_to_which_channels_im_subscribed_to(self):
        subscriptions = self.repo.subscriptions
        # new subscriptions
        for channel in subscriptions - self.subscriptions:
            log.debug(f"::: SUBSCRIBE {self.channel_name} to {channel}")
            await self.outbound.subscribe(channel)
        # remove subscriptions
        for channel in self.subscriptions - subscriptions:
            log.debug(f"::: UNSUBSCRIBE {self.channel_name} to {channel}")
            await self.outbound.unsubscribe(channel)
        self.subscriptions = subscriptions

    async def send_query_string(self):
        new_qs = self.repo.get_query_string()
        if self.query_string != new_qs:
            self.query_string = new_qs
            log.debug(f">>> QS {new_qs}")
            await self.send_command("set_query_string", {"qs": new_qs})
