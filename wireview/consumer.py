"""The Channels WebSocket adapter (``/__wireview__``). The session logic is ``wireview.session``."""

import logging
import typing as t

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from .core.origin import origin_refusal
from .core.rendered import protocol_version
from .core.session import load_session
from .core.transport import NO_CHANNEL_LAYER, ChannelsOutbound, Outbound
from .session import WireviewSession

log = logging.getLogger("wireview")


class WireviewConsumer(AsyncJsonWebsocketConsumer, WireviewSession):
    """The Channels WebSocket adapter of a ``WireviewSession``.

    It accepts or refuses the socket, starts and stops the session, and hands it
    each JSON message; Channels routes the channel-layer mail to the session's
    handlers by name. Everything else is the session's.
    """

    outbound: Outbound

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        # The only object that knows this session is a Channels WebSocket.
        self.outbound = ChannelsOutbound(self)
        self._init_session_state()

    @property
    def user(self):
        return self.scope.get("user") or AnonymousUser()

    async def websocket_connect(self, message):
        # Channels has no default layer: without CHANNEL_LAYERS it leaves
        # channel_layer as None and never sets channel_name, so connect() used to
        # accept the socket and then die on that attribute, which says nothing
        # about the cause (#87). Refuse before accepting, and say why.
        if self.channel_layer is None:
            raise ImproperlyConfigured(NO_CHANNEL_LAYER)
        # Before accepting: a page on another site must not get a socket that
        # carries this site's cookies (#96). Closing now is a 403 to the browser.
        if refusal := origin_refusal(self.scope):
            log.warning("Refusing a WebSocket: %s", refusal)
            self._join_rejected("origin", None, refusal)
            await self.close()
            return
        await super().websocket_connect(message)

    async def connect(self):
        await super().connect()
        # Read once, off the event loop. A component's ``self.session`` is a dict
        # lookup after this; reading the store lazily from an async handler would
        # raise SynchronousOnlyOperation instead (#68). The protocol version comes
        # from the socket URL, kept apart from the page's query string, which
        # navigation changes (GAP-030).
        await self.start(
            session=await load_session(self.scope.get("session")),
            vsn=protocol_version(self.scope.get("query_string", b"")),
        )

    async def disconnect(self, code):
        log.debug(f"<<< DISCONNECT {code}")
        if hasattr(self, "repo"):
            # Refused before connect() ran (an Origin, #96) leaves nothing to stop
            await self.stop(code)
        await super().disconnect(code)

    async def receive_json(self, content: dict, **kwargs) -> None:  # type: ignore[override]
        await self.handle_message(content)
