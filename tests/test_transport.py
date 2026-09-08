"""The transport seam: everything that reaches the connection layer goes through it."""

import typing as t
from unittest.mock import AsyncMock

import pytest

from wireview import Component
from wireview.core import transport
from wireview.core.meta import WireviewMeta
from wireview.core.transport import ChannelsBroker, ChannelsOutbound, NullBroker, get_broker, set_broker
from wireview.testing import mount


class RecordingBroker:
    """Broker double that records instead of touching a channel layer."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, t.Any]]] = []
        self.direct: list[tuple[str, dict[str, t.Any]]] = []

    async def publish(self, topic: str, message: dict[str, t.Any]) -> None:
        self.published.append((topic, message))

    async def send_to_session(self, session_id: str, message: dict[str, t.Any]) -> None:
        self.direct.append((session_id, message))


class TransportProbe(Component):
    _template_name = "transport_probe.html"
    value: int = 0


@pytest.fixture
def broker():
    recording = RecordingBroker()
    set_broker(recording)
    try:
        yield recording
    finally:
        set_broker(None)


@pytest.mark.unit
def test_default_broker_is_channels_and_can_be_overridden():
    set_broker(None)
    assert isinstance(get_broker(), ChannelsBroker)

    override = RecordingBroker()
    set_broker(override)
    try:
        assert get_broker() is override
    finally:
        set_broker(None)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_channels_broker_maps_topics_to_groups_and_sessions_to_channels():
    layer = AsyncMock()
    broker = ChannelsBroker(layer)

    await broker.publish("orders", {"type": "notification"})
    await broker.send_to_session("specific.abc", {"type": "message_from_component"})

    layer.group_send.assert_awaited_once_with("orders", {"type": "notification"})
    layer.send.assert_awaited_once_with("specific.abc", {"type": "message_from_component"})


@pytest.mark.asyncio
@pytest.mark.unit
async def test_channels_outbound_sends_json_frames_and_group_membership():
    consumer = AsyncMock()
    consumer.channel_name = "specific.abc"
    outbound = ChannelsOutbound(consumer)

    await outbound.send_command("render", {"id": "x", "diff": {"0": "1"}})
    await outbound.subscribe("orders")
    await outbound.unsubscribe("orders")

    consumer.send_json.assert_awaited_once_with({"command": "render", "payload": {"id": "x", "diff": {"0": "1"}}})
    consumer.channel_layer.group_add.assert_awaited_once_with("orders", "specific.abc")
    consumer.channel_layer.group_discard.assert_awaited_once_with("orders", "specific.abc")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_channels_outbound_is_a_no_op_without_a_channel_layer():
    consumer = AsyncMock()
    consumer.channel_layer = None
    consumer.channel_name = None

    await ChannelsOutbound(consumer).subscribe("orders")  # must not raise


@pytest.mark.asyncio
@pytest.mark.unit
async def test_meta_without_channel_layer_drops_traffic():
    meta = WireviewMeta(params={})

    assert isinstance(meta.broker, NullBroker)
    await meta.queue_broadcast("orders", action="x")  # must not raise
    await meta.send_to("specific.abc", "render", id="x")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_meta_publishes_and_sends_through_its_broker():
    recording = RecordingBroker()
    meta = WireviewMeta(params={}, channel_name="specific.abc", broker=recording)

    await meta.queue_broadcast("orders", action="joined")
    await meta.send("remove", id="cmp-1")

    assert recording.published == [
        ("orders", {"type": "notification", "channel": "orders", "kwargs": {"action": "joined"}})
    ]
    assert recording.direct == [
        ("specific.abc", {"type": "message_from_component", "command": "remove", "kwargs": {"id": "cmp-1"}})
    ]
    assert meta.clone().broker is recording


@pytest.mark.asyncio
@pytest.mark.unit
async def test_abroadcast_and_notifications_use_the_process_broker(broker):
    from wireview import abroadcast
    from wireview.utils import asend_notification

    await abroadcast("orders", action="created")
    await asend_notification("orders", action="updated")

    assert broker.published == [
        ("orders", {"type": "notification", "channel": "orders", "kwargs": {"action": "created"}}),
        ("orders", {"type": "notification", "channel": "orders", "kwargs": {"action": "updated"}}),
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_channel_layer_is_only_touched_by_the_transport_module():
    """Guard against new direct channel-layer calls sneaking back in."""
    import pathlib
    import re

    root = pathlib.Path(transport.__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in {"transport.py", "testing.py"}:
            continue
        text = path.read_text()
        if re.search(r"get_channel_layer\(|\.group_add\(|\.group_discard\(|\.group_send\(", text):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mounted_component_keeps_working_with_recording_broker():
    view = await mount(TransportProbe, value=1)
    assert view.component.value == 1
