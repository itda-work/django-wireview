import logging
import typing as t

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.utils.datastructures import MultiValueDict

from wireview.component import Component

from . import serializer
from .core.state import unsign_state
from .core.transport import ChannelsOutbound, Outbound
from .live_component import LiveComponent
from .repository import ComponentRepository
from .utils import parse_request_data

log = logging.getLogger("wireview")


class ChildComponent(t.TypedDict):
    name: str
    state: str


class WireviewConsumer(AsyncJsonWebsocketConsumer):
    outbound: Outbound

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        # The only object that knows this session is a Channels WebSocket.
        self.outbound = ChannelsOutbound(self)

    @property
    def user(self):
        return self.scope.get("user") or AnonymousUser()

    async def connect(self):
        await super().connect()
        self.subscriptions = set()
        self.query_string: str = ""
        self.repo = ComponentRepository(
            is_live=True,
            user=self.user,
            channel_name=self.channel_name,
            channel_layer=self.channel_layer,
        )

    async def disconnect(self, code):
        """Handle WebSocket disconnect.

        Calls leaving() on all registered components to allow cleanup,
        then removes all channel subscriptions.
        """
        log.debug(f"<<< DISCONNECT {code}")

        await self._call_leaving(list(self.repo.components.values()))

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
        decoded_state: dict[str, t.Any] = unsign_state(state)
        decoded_children: dict[str, tuple[str, dict[str, t.Any]]] = {
            id: (name, unsign_state(state)) for id, (name, state) in (children or {}).items()
        }
        log.debug(f"<<< JOIN {name} {decoded_state}")
        if isinstance(self.repo.get(decoded_state.get("id", "")), LiveComponent):
            # A LiveComponent is owned by its parent: the parent's join carried its
            # state and its lifecycle runs with the parent's render. Current clients
            # do not send this; a cached older script still might.
            log.debug("Ignoring direct join for LiveComponent %s", decoded_state.get("id"))
            return
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
            # Register upload registry if component has uploads
            await self._register_upload_registry(component)
            await self.send_render(component)

            # Call joined() on any LiveComponents created during render
            # This must happen after send_render() since LiveComponents are
            # created during template rendering (synchronous context)
            await self._flush_pending_live_components()

            # Call params_changed if URL has params (initial load)
            if self.repo.params:
                uri = f"?{self.repo.get_query_string()}"
                await component.params_changed(dict(self.repo.params), uri)
                await self.send_render(component)
                await self._flush_pending_live_components()

            # Flush pending operations queued during joined()
            # This ensures stream(), push_js(), etc. are sent after render
            await component.wire.flush_pending()
            await self.after_mutation_chores()

    async def command_leave(self, id):
        """The client saw a component disappear from the DOM.

        The component and every LiveComponent nested under it get their
        ``leaving()`` hook, their upload registries are released, and the
        subscriptions the connection no longer needs are dropped.
        """
        log.debug(f"<<< LEAVE {id}")
        removed = self.repo.remove(id)
        await self._call_leaving(removed)
        for component_id in [id, *(c.id for c in removed if c.id != id)]:
            await self._unregister_upload_registry(component_id)
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
            await self._flush_pending_live_components()
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
        await self._flush_pending_live_components()
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
        """Handle upload completion notification from client."""
        from .features.uploads import UploadOp, UploadStatus

        log.debug(f"<<< UPLOAD-COMPLETE {id} {name} {ref}")

        component = self.repo.get(id)
        if not component:
            return

        registry = getattr(component, "_upload_registry", None)
        if not registry:
            return

        entry = registry.get_entry(name, ref)
        if entry:
            entry.status = UploadStatus.COMPLETED
            entry.progress = 100

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
            await self._flush_pending_live_components()
        await self.after_mutation_chores()

    async def component_remove(self, id):
        log.debug(f">>> REMOVE {id}")
        await self.send_command("remove", {"id": id})

    async def component_send_render(self, id):
        log.debug(f">>> SEND-RENDER {id}")
        if component := self.repo.get(id):
            await self.send_render(component)
            await self._flush_pending_live_components()

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

    async def _flush_pending_live_components(self):
        """Call joined() on LiveComponents created during render.

        LiveComponents are created during template rendering (synchronous context),
        so we can't call joined() immediately. This method should be called after
        send_render() to initialize any newly created LiveComponents.
        """
        pending = await self.repo.flush_pending_live_components()
        for component in pending:
            # Send render for each LiveComponent after joined()
            await self.send_render(component)
            await component.wire.flush_pending()

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

    async def upload_progress(self, event: dict[str, t.Any]):
        """Handle progress from channel layer (sent by HTTP upload view)."""
        log.debug(f">>> UPLOAD-PROGRESS {event['upload']} {event['ref']} {event['progress']}%")
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

    async def upload_error(self, event: dict[str, t.Any]):
        """Handle error from channel layer (sent by HTTP upload view)."""
        log.debug(f">>> UPLOAD-ERROR {event['upload']} {event['ref']}")
        await self.send_command(
            "upload_op",
            {
                "op": "error",
                "upload": event["upload"],
                "ref": event["ref"],
                "errors": event.get("errors", []),
            },
        )

    # Upload registry management

    async def _register_upload_registry(self, component) -> None:
        """Register component's upload registry for HTTP access."""
        from .views import register_upload_registry

        registry = getattr(component, "_upload_registry", None)
        if registry and self.channel_layer and self.channel_name:
            register_upload_registry(component.id, registry)
            # Subscribe to upload progress topic
            group_name = f"wireview_upload_{component.id}"
            await self.outbound.subscribe(group_name)
            log.debug(f"Subscribed to upload group: {group_name}")

    async def _unregister_upload_registry(self, component_id: str) -> None:
        """Unregister component's upload registry."""
        from .views import unregister_upload_registry

        unregister_upload_registry(component_id)
        # Unsubscribe from upload progress topic
        group_name = f"wireview_upload_{component_id}"
        await self.outbound.unsubscribe(group_name)
        log.debug(f"Unsubscribed from upload group: {group_name}")

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

    async def send_render(self, component: Component):
        diff = await component._render_diff(self.repo)
        if diff is not None:
            log.debug(f">>> RENDER {component._name} {component.id}")
            await self.send_command(
                "render",
                {"id": component.id, "diff": diff},
            )
        # Clear temporary assigns after rendering to free memory
        # This is called regardless of whether diff was sent (skip_render case)
        component._clear_temporary_assigns()

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
