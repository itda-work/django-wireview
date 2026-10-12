"""The Channels WebSocket adapter (``/__wireview__``). The session logic is ``wireview.session``."""

import asyncio
import logging
import typing as t

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from .core.csrf import cookies_of
from .core.origin import origin_refusal
from .core.rendered import protocol_version, socket_tab
from .core.session import load_session
from .core.transport import NO_CHANNEL_LAYER, ChannelsOutbound, ConnectionGone, Mailbox, Outbound, open_mailbox
from .session import WireviewSession

log = logging.getLogger("wireview")


def _hearing_the_end(receive: t.Any, socket: ChannelsOutbound) -> t.Any:
    """``receive``, marking ``socket`` gone as the server's ``websocket.disconnect`` comes (#216).

    Channels dispatches it only after the message being handled, which may be
    writing to the socket meanwhile -- a mail, while the next receive waits.
    Uvicorn, having seen the close there, refuses the frame with a
    ``RuntimeError`` that says nothing of a disconnect: this tells it apart.
    """

    async def hearing() -> dict:
        message = await receive()
        if message.get("type") == "websocket.disconnect":
            socket.gone = True
        return message

    return hearing


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
        self._socket = ChannelsOutbound(self)
        self.outbound = self._socket
        # One message handled at a time: those Channels dispatches, and the mail
        # the session takes back when the layer dropped it (#211)
        self._turn = asyncio.Lock()
        # Whether it runs as the ASGI app (__call__) whose dispatch loop takes its mail
        self._serving = False
        self._init_session_state()

    async def __call__(self, scope, receive, send):  # type: ignore[override]
        self._serving = True
        try:
            await super().__call__(scope, _hearing_the_end(receive, self._socket), send)
        finally:
            # Cancelled by the server -- a disconnect a handler held up past its close
            # timeout, a shutdown -- or raised, it never ran disconnect(): the session
            # ends here, once (#211). After a disconnect this waits on that ending.
            try:
                await self.stop()
            except Exception:
                log.exception("Ending the session of connection %s raised", self.connection_id)

    async def close(self, code=None, reason=None):
        # Once: a socket the page left or this closed takes no close frame either (#216)
        if self._socket.gone:
            return
        self._socket.gone = True
        try:
            await super().close(code=code, reason=reason)
        except OSError as error:
            log.debug("Connection %s was gone before it closed: %r", self.connection_id, error)

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
        if self._serving and self.channel_name:
            # The mail it sends itself is kept until the layer brings it back (#211)
            self._mailbox = Mailbox(self.channel_name, self.hand_over, lambda: self.handling > 0, self.mail_overflowed)
            open_mailbox(self._mailbox)
        await super().connect()
        # Read once, off the event loop. A component's ``self.session`` is a dict
        # lookup after this; reading the store lazily from an async handler would
        # raise SynchronousOnlyOperation instead (#68). The protocol version comes
        # from the socket URL, kept apart from the page's query string, which
        # navigation changes (GAP-030).
        await self.start(
            session=await load_session(self.scope.get("session")),
            vsn=protocol_version(self.scope.get("query_string", b"")),
            tab=socket_tab(self.scope.get("query_string", b"")),
            # Where a component's {% csrf_token %} comes from on this socket (#206)
            csrf_cookies=cookies_of(self.scope),
            # Without Channels' SessionMiddleware the handshake says nobody: a join
            # cannot be told from another identity's (#209, wireview.W020)
            session_known="session" in self.scope,
        )

    async def disconnect(self, code):
        log.debug(f"<<< DISCONNECT {code}")
        # Refused before connect() ran (an Origin, #96), it has nothing but its mailbox to end
        await self.stop(code)
        await super().disconnect(code)

    async def dispatch(self, message):
        # One at a time with the mail the session takes back from a layer that dropped it (#211)
        async with self._turn:
            # A mail may bring the ones the layer dropped before it: each is a message of its own
            for each in self.in_turn(message):
                # Where one message ends is the adapter's to know (#178)
                with self.handling_message():
                    try:
                        await super().dispatch(each)
                    except ConnectionGone as gone:
                        # The page left while this was answered: how a connection ends, not an error (#216)
                        log.debug(
                            "Connection %s left before %s was answered: %s", self.connection_id, each.get("type"), gone
                        )

    async def receive_json(self, content: dict, **kwargs) -> None:  # type: ignore[override]
        await self.handle_message(content)
