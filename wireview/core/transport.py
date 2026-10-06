"""Transport seams between wireview sessions and the connection layer.

wireview moves three kinds of traffic:

- **Outbound**: one session (a browser tab) receives commands such as
  ``render`` or ``exec_js``.
- **Session mail**: a component sends a command to a session, usually its
  own. Today this rides on the channel layer as ``message_from_component``.
- **Fan-out**: a message is published to every session subscribed to a topic
  (model mutations, notifications, upload progress).
- **Patches**: frames rendered and serialized once by a ``Broadcast`` (#178),
  published to the topic's patch group (``patch_group``) and written as they
  are to every session holding a target component.

``Outbound`` and ``Broker`` are the only places that touch Django Channels.
Consumer handlers, ``WireviewMeta`` and the broadcast helpers go through
them, so another connection layer (an Elixir or Go front, or a test double)
only has to implement these two interfaces. The message shapes are fixed in
``docs/implementation/wire-protocol.md``.
"""

from __future__ import annotations

import logging
import typing as t

from channels.exceptions import ChannelFull
from channels.layers import BaseChannelLayer as _BaseChannelLayer
from channels.layers import get_channel_layer

from .. import telemetry

if t.TYPE_CHECKING:
    from channels.layers import BaseChannelLayer

log = logging.getLogger("wireview")

Message = dict[str, t.Any]


class Outbound(t.Protocol):
    """Per-session channel back to one browser tab."""

    async def send_command(self, command: str, payload: Message) -> None:
        """Deliver a server-to-client command to this session."""
        ...

    async def send_text(self, text: str) -> None:
        """Deliver a frame serialized already: what ``send_command`` would have written, byte for byte."""
        ...

    async def subscribe(self, topic: str) -> None:
        """Start receiving ``Broker.publish`` messages for ``topic``."""
        ...

    async def unsubscribe(self, topic: str) -> None:
        """Stop receiving messages for ``topic``."""
        ...

    async def close(self, code: int | None = None) -> None:
        """End the connection: a logout retired it (4001), or an old client after a crash (1011)."""
        ...


class Broker(t.Protocol):
    """Process-wide message routing between sessions."""

    async def publish(self, topic: str, message: Message) -> None:
        """Deliver ``message`` to every session subscribed to ``topic``."""
        ...

    async def send_to_session(self, session_id: str, message: Message) -> None:
        """Deliver ``message`` to a single session."""
        ...

    async def publish_patch(self, topic: str, message: Message) -> None:
        """Deliver a ``wireview.patch`` message to every session receiving patches for ``topic``."""
        ...


#: Where a topic's patches go. Apart from the topic's own group, which carries
#: notifications: a process from before patches existed has its consumers in
#: that one, and Channels raises on a message type a consumer has no handler
#: for. Those consumers are in no patch group, so a rolling deploy costs their
#: pages the patches until they reconnect, not the socket (#178).
PATCH_GROUP_PREFIX = "wireview.patch."

#: The longest topic whose patch group is a valid group name: Channels takes
#: names under 100 characters.
PATCH_TOPIC_MAX = 99 - len(PATCH_GROUP_PREFIX)


def require_patch_topic(topic: t.Any) -> None:
    """Raise ``ValueError`` unless ``topic``'s patch group is a name every channel layer takes."""
    if not isinstance(topic, str) or not topic:
        raise ValueError(f"A Broadcast topic is a non-empty string, not {topic!r}")
    if len(topic) > PATCH_TOPIC_MAX:
        raise ValueError(
            f"A Broadcast topic is at most {PATCH_TOPIC_MAX} characters, so that its patch group "
            f"{patch_group('')!r}<topic> stays under the channel layers' 100; {topic!r} has {len(topic)}"
        )
    if not _BaseChannelLayer.group_name_regex.match(topic):
        raise ValueError(
            f"A Broadcast topic holds only ASCII letters, digits, '-', '_' and '.', as a channel layer group "
            f"name does: {topic!r}"
        )


def patch_group(topic: str) -> str:
    """The topic that carries ``topic``'s patches: a session receives them by ``Outbound.subscribe`` to it."""
    return PATCH_GROUP_PREFIX + topic


#: What a project without a channel layer is told. The consumer refuses the
#: connection with it and the W012 system check reports it, so the two cannot drift.
NO_CHANNEL_LAYER = (
    "CHANNEL_LAYERS has no 'default' layer, and Channels has no built-in default. "
    "wireview cannot subscribe, broadcast or reach a session without one. For a single "
    "process, set CHANNEL_LAYERS = {'default': {'BACKEND': "
    "'channels.layers.InMemoryChannelLayer'}}; across processes use channels-nats or "
    "channels_redis."
)


class ChannelsBroker:
    """``Broker`` on top of the Django Channels channel layer.

    Topics map to channel-layer groups and session ids to channel names.

    A layer that refuses a message is not silent here (#124). ``ChannelFull``
    -- the receiver is not keeping up -- drops that one message: it is logged,
    reported as ``telemetry.publish_failed`` and the caller carries on, as a
    layer's own ``group_send`` already does for each full member. Any other
    error (the broker is down) is reported the same way and re-raised, so the
    handler that published fails as it did before and the session isolates it.

    Only what the layer raises can be seen, and ``group_send`` raises nothing
    for a full member: channels_redis drops it and logs at INFO, the in-memory
    layer drops it without a word, and channels-nats drops on the receiving
    side with a WARNING. ``send`` to one channel is what raises ``ChannelFull``.
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
            try:
                await layer.group_send(topic, message)
            except Exception as error:
                if not self._dropped("publish", topic, message, error):
                    raise

    async def send_to_session(self, session_id: str, message: Message) -> None:
        layer = self.channel_layer
        if layer is not None:
            try:
                await layer.send(session_id, message)
            except Exception as error:
                if not self._dropped("send_to_session", session_id, message, error):
                    raise

    async def publish_patch(self, topic: str, message: Message) -> None:
        await self.publish(patch_group(topic), message)

    def _dropped(self, kind: str, target: str, message: Message, error: Exception) -> bool:
        """Report a refused message. True when it is dropped (a full channel); False when the caller re-raises."""
        dropped = isinstance(error, ChannelFull)
        telemetry.emit(telemetry.publish_failed, type(self), kind=kind, target=target, error=error, dropped=dropped)
        if dropped:
            log.warning(
                "Dropped a %r message: the channel layer reports %s full (%s)", message.get("type"), target, kind
            )
        return dropped


class NullBroker:
    """``Broker`` that drops everything. Used for HTTP renders without a channel layer."""

    async def publish(self, topic: str, message: Message) -> None:
        return None

    async def send_to_session(self, session_id: str, message: Message) -> None:
        return None

    async def publish_patch(self, topic: str, message: Message) -> None:
        return None


class ChannelsOutbound:
    """``Outbound`` for a Channels WebSocket consumer.

    Commands go out as JSON frames; subscriptions map to channel-layer groups.
    """

    def __init__(self, consumer: t.Any) -> None:
        self._consumer = consumer

    async def send_command(self, command: str, payload: Message) -> None:
        await self._consumer.send_json({"command": command, "payload": payload})

    async def send_text(self, text: str) -> None:
        await self._consumer.send(text_data=text)

    async def subscribe(self, topic: str) -> None:
        consumer = self._consumer
        if consumer.channel_layer is not None and consumer.channel_name is not None:
            await consumer.channel_layer.group_add(topic, consumer.channel_name)

    async def unsubscribe(self, topic: str) -> None:
        consumer = self._consumer
        if consumer.channel_layer is not None and consumer.channel_name is not None:
            await consumer.channel_layer.group_discard(topic, consumer.channel_name)

    async def close(self, code: int | None = None) -> None:
        await self._consumer.close(code=code)


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
