"""Transport seams between wireview sessions and the connection layer.

wireview moves three kinds of traffic:

- **Outbound**: one session (a browser tab) receives commands such as
  ``render`` or ``exec_js``.
- **Session mail**: a component sends a command to a session, usually its
  own. It rides on the channel layer as ``message_from_component``; a
  session's ``Mailbox`` hands over what the layer drops (#211).
- **Fan-out**: a message is published to every session subscribed to a topic
  (model mutations, notifications, upload progress).
- **Patches**: frames rendered and serialized once by a ``Broadcast`` (#178),
  published to the topic's patch group (``patch_group``), received once per
  process (``PatchHub``) and written as they are to every session holding a
  target component.

``Outbound`` and ``Broker`` are the only places that touch Django Channels.
Consumer handlers, ``WireviewMeta`` and the broadcast helpers go through
them, so another connection layer (an Elixir or Go front, or a test double)
only has to implement these two interfaces. The message shapes are fixed in
``docs/implementation/wire-protocol.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import itertools
import logging
import sys
import typing as t
import weakref

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

    async def send_to_session(self, session_id: str, message: Message) -> bool | None:
        """Deliver ``message`` to a single session.

        Returns ``False`` when nothing was sent: no layer, or the layer refused
        it and it was dropped (a full channel). A caller that waits on its own
        mail -- a session coalescing template changes (#180) -- would wait for
        good otherwise. ``None`` from a broker that does not say.
        """
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
    """The channel-layer group that carries ``topic``'s patches. Its member is each process's ``PatchHub``."""
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

    async def send_to_session(self, session_id: str, message: Message) -> bool:
        layer = self.channel_layer
        if layer is None:
            return False
        try:
            await layer.send(session_id, message)
        except Exception as error:
            if not self._dropped("send_to_session", session_id, message, error):
                raise
            return False
        return True

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


#: How long a session's mail to itself may stay away while the session handles
#: nothing before it is taken for dropped and handled here (``Mailbox``, #211).
#: Under ``patches.HOLD_SECONDS``, so a hold its mail releases is let go before
#: that deadline closes the connection.
MAIL_SECONDS = 5.0

#: How many of a session's mails to itself may be away at once, and how many
#: bytes of copies they may hold (``Mailbox``, #211). Past either the
#: connection closes (1013) rather than keep more: its channel takes none of
#: them, and the page joins again with everything read afresh.
MAIL_LIMIT = 1000
MAIL_BYTES = 16 * 1024 * 1024

#: The message a session dispatches to itself for a turn that takes the mail
#: its ``Mailbox`` found dropped (``Mailbox.due``). It reaches no handler.
MAIL_DUE = "wireview.mail_due"


class Mailbox:
    """A session's mail to itself, kept until the channel layer brings it back (#211).

    A session mails itself to have something done in its turn: after the
    message being handled, in line with the notifications its topics fan out
    to the same channel, in the order they were all sent -- the layer's channel
    is that line. But the line has a capacity, and the layer drops what does not
    fit: ``send`` raises ``ChannelFull`` (the broker drops it and says so), or
    channels_redis drops the oldest message of a full receive buffer without a
    word. A channel fills when its room is busy, the time a mail matters most,
    and a dropped one could leave a Broadcast hold shut, a list short or a
    component stale for good.

    So the sender and the session, which share a process and a loop, keep a
    copy of each mail until it is handled, numbered in the order sent. Mail is
    handled in that order and only in the session's turn: what comes off the
    channel there (``came``) brings every mail sent before it that is still
    away, and the ones found dropped are only marked due (``wake``) for a turn
    of the session's own to take (``due``):

    - the layer refused it: due, behind the mails sent before it;
    - it stayed away ``MAIL_SECONDS`` while the session handled nothing: it was
      dropped, or is very late. Due.

    Until a turn takes it a due mail stays where a later one finds it: a mail
    the layer brings first handles it ahead of itself. A copy the layer brings
    after it was handled here is dropped. So mail keeps the order it was sent
    in; what a line that overflowed loses is the order against the notifications
    -- a mail handled from its copy comes ahead of the ones queued in front of
    it, or after the ones sent behind it.

    The copies are bounded (``MAIL_LIMIT``, ``MAIL_BYTES``): past them the box
    closes and tells the session (``overflow``), which closes the connection.
    """

    def __init__(
        self,
        session_id: str,
        wake: t.Callable[[], None],
        busy: t.Callable[[], bool],
        overflow: t.Callable[[], None] = lambda: None,
    ) -> None:
        self.session_id = session_id
        self._wake = wake
        self._busy = busy
        self._overflow = overflow
        self._loop = asyncio.get_running_loop()
        self._numbers = itertools.count(1)
        # Sent and not handled yet, in the order sent: number -> (a copy, when it was sent, its size)
        self._away: dict[int, tuple[Message, float, int]] = {}
        self._size = 0
        # The mails up to this number are due: the next turn handles them from their copies
        self._due = 0
        self._timer: asyncio.TimerHandle | None = None
        self.closed = False

    async def send(self, broker: Broker, message: Message) -> bool | None:
        """Send ``message`` through ``broker``, keeping a copy to handle if the layer drops it."""
        if self.closed:
            return await broker.send_to_session(self.session_id, message)
        number = next(self._numbers)
        message = {**message, "mail": number}
        # As the layers deliver one: the sender may change what it sent
        kept, size = _kept(message)
        if len(self._away) >= MAIL_LIMIT or self._size + size > MAIL_BYTES:
            self._overflowed(size)
            return await broker.send_to_session(self.session_id, message)
        self._away[number] = (kept, self._loop.time(), size)
        self._size += size
        try:
            taken = await broker.send_to_session(self.session_id, message)
        except BaseException:
            # Raised to the sender, as before: it handles that
            self._drop(number)
            raise
        if taken is False:
            self._mark(number)
        else:
            self._arm()
        return True

    def came(self, message: Message) -> list[Message]:
        """The mail the channel brought, in the session's turn: what it handles now, in order.

        The mails sent before it and still away come first, then it. Empty for
        one handled already.
        """
        number = message.get("mail")
        if not isinstance(number, int):
            return [message]
        if self.closed or number not in self._away:
            log.debug("Dropping mail %d to %s: it was handled when it did not come", number, self.session_id)
            return []
        mails = self._take(number)
        if len(mails) > 1:
            log.warning(
                "%d mail(s) to %s did not come before a later one; handling them first (#211)",
                len(mails) - 1,
                self.session_id,
            )
        return mails

    def due(self) -> list[Message]:
        """In the session's turn: the mails found dropped, in order, from their copies."""
        if self.closed:
            return []
        return self._take(self._due)

    @property
    def has_due(self) -> bool:
        """Whether a mail found dropped waits for a turn."""
        return not self.closed and bool(self._away) and next(iter(self._away)) <= self._due

    def _take(self, number: int) -> list[Message]:
        """The mails up to ``number`` still away, in order: handled now."""
        taken = []
        while self._away and (each := next(iter(self._away))) <= number:
            kept, _, size = self._away.pop(each)
            self._size -= size
            taken.append({**kept, "here": True})
        return taken

    def _drop(self, number: int) -> None:
        if (away := self._away.pop(number, None)) is not None:
            self._size -= away[2]

    def _mark(self, number: int) -> None:
        """The mails up to ``number`` are due: the session takes them in a turn of its own."""
        self._due = max(self._due, number)
        self._wake()
        self._arm()

    def _arm(self) -> None:
        if self._timer is None and not self.closed and self._away and next(reversed(self._away)) > self._due:
            self._timer = self._loop.call_later(MAIL_SECONDS, self._overdue)

    def _overdue(self) -> None:
        self._timer = None
        if self.closed:
            return
        if not self._busy():
            now = self._loop.time()
            # Marked only: the turn that takes them may come after a later mail's, which then takes them first
            due = [each for each, (_, sent, _) in self._away.items() if each > self._due and now - sent >= MAIL_SECONDS]
            if due:
                log.warning(
                    "%d mail(s) to %s did not come in %ss with nothing being handled; handling them here (#211)",
                    len(due),
                    self.session_id,
                    MAIL_SECONDS,
                )
                self._mark(due[-1])
                return
        self._arm()

    def _overflowed(self, size: int) -> None:
        log.warning(
            "%d mail(s) to %s holding %d bytes are away, and one of %d bytes more is past MAIL_LIMIT (%d) "
            "or MAIL_BYTES (%d): closing the connection (#211)",
            len(self._away),
            self.session_id,
            self._size,
            size,
            MAIL_LIMIT,
            MAIL_BYTES,
        )
        self.close()
        self._overflow()

    def close(self) -> None:
        """The session ended: nothing more is kept or handled from a copy."""
        self.closed = True
        self._away.clear()
        self._size = 0
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _kept(value: t.Any) -> tuple[t.Any, int]:
    """A copy of mail as the layers deliver it, and roughly how many bytes it holds."""
    kind = type(value)
    if kind is dict:
        copied = {}
        size = 64
        for key, item in value.items():
            item, item_size = _kept(item)
            copied[key] = item
            size += item_size + (len(key) if type(key) is str else 8)
        return copied, size
    if kind is list or kind is tuple:
        items = [_kept(item) for item in value]
        return kind(item for item, _ in items), 56 + sum(item_size for _, item_size in items)
    if kind is str or kind is bytes:
        return value, 48 + len(value)
    if value is None or kind is bool or kind is int or kind is float:
        return value, 8
    copied = copy.deepcopy(value)
    return copied, sys.getsizeof(copied)


# Session id (channel name) -> the mailbox of the session serving it in this process (#211)
_mailboxes: dict[str, Mailbox] = {}


def open_mailbox(mailbox: Mailbox) -> None:
    """Keep the mail sent to ``mailbox.session_id`` from this process until it comes back."""
    _mailboxes[mailbox.session_id] = mailbox


def close_mailbox(mailbox: Mailbox) -> None:
    """Its session ended: mail to it goes through the broker alone, as to any session not here."""
    mailbox.close()
    if _mailboxes.get(mailbox.session_id) is mailbox:
        del _mailboxes[mailbox.session_id]


async def send_mail(broker: Broker, session_id: str, message: Message) -> bool | None:
    """Mail one session: through its ``Mailbox`` when it is served on this loop, through ``broker`` otherwise.

    What ``Broker.send_to_session`` returns: ``False`` when it is known not to have gone.
    """
    mailbox = _mailboxes.get(session_id)
    if mailbox is not None and not mailbox.closed and _running_loop() is mailbox._loop:
        return await mailbox.send(broker, message)
    return await broker.send_to_session(session_id, message)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class NullBroker:
    """``Broker`` that drops everything. Used for HTTP renders without a channel layer."""

    async def publish(self, topic: str, message: Message) -> None:
        return None

    async def send_to_session(self, session_id: str, message: Message) -> bool:
        return False

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


class PatchHub:
    """Receives each topic's ``Broadcast`` patches once per process and hands them to its sessions (#178).

    One channel per event loop sits in the patch group of every topic a
    session of the process hears (``patch_group``): a ``Broadcast`` costs the
    channel layer one message per process, not one per connection, and none of
    Channels' per-message dispatch. Its loop takes each message off the layer
    and calls every receiver registered for the topic, in turn and without
    awaiting: a receiver queues what it writes (``WireviewSession._take_patch``),
    so a slow socket holds up only its own frames.

    The group is joined when the process's first receiver for a topic comes and
    left with the last. Joining and leaving one topic are serialized, and a
    ``join`` returns once the channel is in the group. Membership expires in
    channels_redis and the in-memory layer (``group_expiry``, a day by default)
    while this channel lives as long as the process, so it is renewed every
    ``RENEW_SECONDS``; a renewal that fails is logged and the next one tried.
    """

    RENEW_SECONDS = 3600.0

    def __init__(self, layer: BaseChannelLayer) -> None:
        self._layer = layer
        self._channel: str | None = None
        self._receivers: dict[str, dict[t.Any, t.Callable[[Message], None]]] = {}
        self._joined: set[str] = set()
        # One lock per topic someone is joining, leaving or renewing, with how
        # many hold or wait for it: let go with the last, so a process that went
        # through many topics (a room each) keeps none of theirs
        self._locks: dict[str, tuple[asyncio.Lock, list[int]]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        # The process's channel being opened, or opened (``_start``)
        self._opening: asyncio.Future[str] | None = None

    @property
    def topics(self) -> set[str]:
        """The topics whose patch group this process's channel is in."""
        return set(self._joined)

    async def join(self, topic: str, key: t.Any, receiver: t.Callable[[Message], None]) -> None:
        """Hand ``topic``'s patches to ``receiver`` (one per ``key``) from when this returns.

        One join of a ``key`` at a time: a session settles its topics in turn.
        """
        receivers = self._receivers.setdefault(topic, {})
        previous = receivers.get(key)
        receivers[key] = receiver
        try:
            await self._settle(topic)
        except BaseException:
            # Not joined (the layer raised, the caller was cancelled): the caller
            # does not count it as heard and will not leave it. A receiver ``key``
            # had before this join stays, as its caller counts it.
            receivers = self._receivers.get(topic)
            if receivers is not None and receivers.get(key) is receiver:
                if previous is not None:
                    receivers[key] = previous
                else:
                    del receivers[key]
                    if not receivers:
                        del self._receivers[topic]
            raise

    async def leave(self, topic: str, key: t.Any) -> None:
        receivers = self._receivers.get(topic)
        if receivers is None or receivers.pop(key, None) is None:
            return
        if not receivers:
            del self._receivers[topic]
        await self._settle(topic)

    @contextlib.asynccontextmanager
    async def _lock(self, topic: str) -> t.AsyncIterator[None]:
        """Joining and leaving ``topic``'s group, one at a time."""
        entry = self._locks.get(topic)
        if entry is None:
            entry = self._locks[topic] = (asyncio.Lock(), [0])
        lock, users = entry
        users[0] += 1
        try:
            async with lock:
                yield
        finally:
            users[0] -= 1
            if not users[0]:
                del self._locks[topic]

    async def _settle(self, topic: str) -> None:
        """Be in ``topic``'s patch group exactly while someone hears it."""
        async with self._lock(topic):
            wanted = topic in self._receivers
            if wanted and topic not in self._joined:
                await self._start()
                await self._layer.group_add(patch_group(topic), self._channel)  # type: ignore[arg-type]
                self._joined.add(topic)
            elif not wanted and topic in self._joined:
                self._joined.discard(topic)
                await self._layer.group_discard(patch_group(topic), self._channel)  # type: ignore[arg-type]
            if not self._joined and not self._receivers:
                self._stop()

    async def _start(self) -> None:
        """Have the process's channel, and the tasks that receive on it and renew it.

        Two topics joined at once open one channel. The opening is the
        process's, not the join's: a join that gives up while it waits does
        not cancel it for the others (``shield``), and one that failed is tried
        again by the next join rather than kept as every join's answer (#185).
        """
        if self._channel is not None:
            return
        opening = self._opening
        if opening is None or (opening.done() and (opening.cancelled() or opening.exception() is not None)):
            opening = self._opening = asyncio.ensure_future(self._layer.new_channel())
            # A failure no join is left to await is not "never retrieved"
            opening.add_done_callback(lambda done: done.cancelled() or done.exception())
        channel = await asyncio.shield(opening)
        if self._channel is None:
            self._channel = channel
            self._tasks = [asyncio.ensure_future(self._receive()), asyncio.ensure_future(self._renew())]

    def _stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        self._channel = None
        self._opening = None

    async def _receive(self) -> None:
        channel = self._channel
        while True:
            try:
                message = await self._layer.receive(channel)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Could not receive Broadcast patches on %s", channel)
                await asyncio.sleep(1)
                continue
            self.dispatch(message)

    def dispatch(self, message: Message) -> None:
        """Hand one patch message to every receiver of its topic."""
        for receiver in list(self._receivers.get(message.get("topic", ""), {}).values()):
            try:
                receiver(message)
            except Exception:
                log.exception("A receiver of %r patches raised", message.get("topic"))

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self.RENEW_SECONDS)
            await self.renew()

    async def renew(self) -> None:
        """Join every topic's group again, before the layer's ``group_expiry`` drops this channel.

        A topic that fails is logged and left to the next renewal; the others
        are renewed all the same. Raising out of here would end the task that
        renews, and a day later the process would hear no topic, without a word.
        """
        for topic in list(self._joined):
            async with self._lock(topic):
                if topic not in self._joined:
                    continue
                try:
                    await self._layer.group_add(patch_group(topic), self._channel)  # type: ignore[arg-type]
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Could not renew this process's Broadcast patch group of %r", topic)


class NullPatchHub:
    """``PatchHub`` for a process with no channel layer: nothing is ever received."""

    topics: set[str] = set()

    async def join(self, topic: str, key: t.Any, receiver: t.Callable[[Message], None]) -> None:
        return None

    async def leave(self, topic: str, key: t.Any) -> None:
        return None


_hubs: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, PatchHub | NullPatchHub] = weakref.WeakKeyDictionary()
_hub_override: t.Any = None


def get_patch_hub() -> PatchHub | NullPatchHub:
    """The running event loop's ``PatchHub`` (one per process under an ASGI server)."""
    if _hub_override is not None:
        return _hub_override
    loop = asyncio.get_running_loop()
    hub = _hubs.get(loop)
    if hub is None:
        layer = get_channel_layer()
        hub = _hubs[loop] = NullPatchHub() if layer is None else PatchHub(layer)
    return hub


def set_patch_hub(hub: t.Any) -> None:
    """Override the patch hub of every loop. ``None`` restores one per loop on the Channels layer."""
    global _hub_override
    _hub_override = hub


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
