"""Transport seams between wireview sessions and the connection layer.

wireview moves three kinds of traffic:

- **Outbound**: one session (a browser tab) receives commands such as
  ``render`` or ``exec_js``.
- **Session mail**: a component sends a command to a session, usually its
  own. Today this rides on the channel layer as ``message_from_component``.
- **Fan-out**: a message is published to every session subscribed to a topic
  (model mutations, notifications, upload progress).

``Outbound`` and ``Broker`` are the only places that touch Django Channels.
Consumer handlers, ``WireviewMeta`` and the broadcast helpers go through
them, so another connection layer (an Elixir or Go front, or a test double)
only has to implement these two interfaces. The message shapes are fixed in
``docs/implementation/wire-protocol.md``.
"""

from __future__ import annotations

import typing as t

from channels.layers import get_channel_layer

if t.TYPE_CHECKING:
    from channels.layers import BaseChannelLayer

Message = dict[str, t.Any]


class Outbound(t.Protocol):
    """Per-session channel back to one browser tab."""

    async def send_command(self, command: str, payload: Message) -> None:
        """Deliver a server-to-client command to this session."""
        ...

    async def subscribe(self, topic: str) -> None:
        """Start receiving ``Broker.publish`` messages for ``topic``."""
        ...

    async def unsubscribe(self, topic: str) -> None:
        """Stop receiving messages for ``topic``."""
        ...


class Broker(t.Protocol):
    """Process-wide message routing between sessions."""

    async def publish(self, topic: str, message: Message) -> None:
        """Deliver ``message`` to every session subscribed to ``topic``."""
        ...

    async def send_to_session(self, session_id: str, message: Message) -> None:
        """Deliver ``message`` to a single session."""
        ...


class ChannelsBroker:
    """``Broker`` on top of the Django Channels channel layer.

    Topics map to channel-layer groups and session ids to channel names.
    """

    def __init__(self, channel_layer: BaseChannelLayer | None = None) -> None:
        self._channel_layer = channel_layer

    @property
    def channel_layer(self) -> BaseChannelLayer | None:
        if self._channel_layer is not None:
            return self._channel_layer
        return get_channel_layer()

    async def publish(self, topic: str, message: Message) -> None:
        layer = self.channel_layer
        if layer is not None:
            await layer.group_send(topic, message)

    async def send_to_session(self, session_id: str, message: Message) -> None:
        layer = self.channel_layer
        if layer is not None:
            await layer.send(session_id, message)


class NullBroker:
    """``Broker`` that drops everything. Used for HTTP renders without a channel layer."""

    async def publish(self, topic: str, message: Message) -> None:
        return None

    async def send_to_session(self, session_id: str, message: Message) -> None:
        return None


class ChannelsOutbound:
    """``Outbound`` for a Channels WebSocket consumer.

    Commands go out as JSON frames; subscriptions map to channel-layer groups.
    """

    def __init__(self, consumer: t.Any) -> None:
        self._consumer = consumer

    async def send_command(self, command: str, payload: Message) -> None:
        await self._consumer.send_json({"command": command, "payload": payload})

    async def subscribe(self, topic: str) -> None:
        consumer = self._consumer
        if consumer.channel_layer is not None and consumer.channel_name is not None:
            await consumer.channel_layer.group_add(topic, consumer.channel_name)

    async def unsubscribe(self, topic: str) -> None:
        consumer = self._consumer
        if consumer.channel_layer is not None and consumer.channel_name is not None:
            await consumer.channel_layer.group_discard(topic, consumer.channel_name)


_broker: Broker | None = None


def get_broker() -> Broker:
    """Return the process-wide broker (Channels unless overridden)."""
    global _broker
    if _broker is None:
        _broker = ChannelsBroker()
    return _broker


def set_broker(broker: Broker | None) -> None:
    """Override the process-wide broker. ``None`` restores the Channels default."""
    global _broker
    _broker = broker
