import json
import logging
import typing as t

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.signing import Signer
from django.utils.datastructures import MultiValueDict

from wireview.component import Component

from . import serializer
from .repository import ComponentRepository
from .utils import parse_request_data

log = logging.getLogger("wireview")


class ChildComponent(t.TypedDict):
    name: str
    state: str


class WireviewConsumer(AsyncJsonWebsocketConsumer):
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

        # Call leaving() on all registered components
        for component in list(self.repo.components.values()):
            try:
                await component.leaving()
            except Exception as e:
                log.exception(f"Error in {component._name}.leaving(): {e}")

        # Cleanup subscriptions
        if self.channel_layer is not None and self.channel_name is not None:
            for channel in self.subscriptions:
                log.debug(f"::: UNSUBSCRIBE {self.channel_name} from {channel}")
                await self.channel_layer.group_discard(channel, self.channel_name)
            self.subscriptions.clear()

        await super().disconnect(code)

    # Fronted commands

    async def receive_json(self, content):
        await getattr(self, f'command_{content["command"]}')(**content["payload"])

    async def command_join(
        self,
        name: str,
        state: str,
        children: dict[str, ChildComponent] | None = None,
    ):
        signer = Signer()
        decoded_state: dict[str, t.Any] = json.loads(signer.unsign(state))
        decoded_children: dict[str, tuple[str, dict[str, t.Any]]] = {
            id: (name, json.loads(signer.unsign(state))) for id, (name, state) in (children or {}).items()
        }
        log.debug(f"<<< JOIN {name} {decoded_state}")
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
            # Flush pending operations queued during joined()
            # This ensures stream(), push_js(), etc. are sent after render
            await component.wire.flush_pending()
            await self.after_mutation_chores()

    async def command_leave(self, id):
        log.debug(f"<<< LEAVE {id}")
        # Unregister upload registry
        await self._unregister_upload_registry(id)
        self.repo.remove(id)

    async def command_query_string(self, qs: str):
        self.query_string = qs
        self.repo.set_query_string(qs)

    async def command_user_event(self, id, command, implicit_args, explicit_args):
        kwargs = dict(parse_request_data(MultiValueDict(implicit_args)), **explicit_args)
        log.debug(f"<<< USER-EVENT {id} {command} {kwargs}")
        component = await self.repo.dispatch_event(id, command, [], kwargs)
        if component:
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
                    # Send token for HTTP upload
                    config = registry.configs[name]
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
                await component.on_upload_complete(name, entry)

            # Re-render
            await self.send_render(component)

    # Component commands

    async def message_from_component(self, data):
        await getattr(self, f"component_{data['command']}")(**data["kwargs"])

    async def component_dispatch_event(self, id, command, args, kwargs):
        log.debug(f"<<< EVENT {id} {command} {args} {kwargs}")
        component = await self.repo.dispatch_event(id, command, args, kwargs)
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

    async def component_stream_op(self, op, stream, items, at):
        log.debug(f">>> STREAM {op.upper()} {stream}")
        await self.send_command("stream_op", {"op": op, "stream": stream, "items": items, "at": at})

    async def component_scroll_into_view(self, id, behavoir, block, inline):
        log.debug(f">>> SCROLL-INTO-VIEW {id}")
        await self.send_command(
            "scroll_into_view",
            {"id": id, "behavoir": behavoir, "block": block, "inline": inline},
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
            # Subscribe to upload progress group
            group_name = f"wireview_upload_{component.id}"
            await self.channel_layer.group_add(group_name, self.channel_name)
            log.debug(f"Subscribed to upload group: {group_name}")

    async def _unregister_upload_registry(self, component_id: str) -> None:
        """Unregister component's upload registry."""
        from .views import unregister_upload_registry

        unregister_upload_registry(component_id)
        if self.channel_layer and self.channel_name:
            # Unsubscribe from upload progress group
            group_name = f"wireview_upload_{component_id}"
            await self.channel_layer.group_discard(group_name, self.channel_name)
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

    async def send_command(self, command, payload):
        await self.send_json({"command": command, "payload": payload})

    async def after_mutation_chores(self):
        await self.update_to_which_channels_im_subscribed_to()
        await self.send_query_string()

    async def update_to_which_channels_im_subscribed_to(self):
        if self.channel_layer is not None and self.channel_name is not None:
            subscriptions = self.repo.subscriptions

            # new subscriptions
            for channel in subscriptions - self.subscriptions:
                log.debug(f"::: SUBSCRIBE {self.channel_name} to {channel}")
                await self.channel_layer.group_add(channel, self.channel_name)

            # remove subscriptions
            for channel in self.subscriptions - subscriptions:
                log.debug(f"::: UNSUBSCRIBE {self.channel_name} to {channel}")
                await self.channel_layer.group_discard(channel, self.channel_name)

            self.subscriptions = subscriptions

    async def send_query_string(self):
        new_qs = self.repo.get_query_string()
        if self.query_string != new_qs:
            self.query_string = new_qs
            log.debug(f">>> QS {new_qs}")
            await self.send_command("set_query_string", {"qs": new_qs})
