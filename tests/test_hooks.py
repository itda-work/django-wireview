"""Tests for JavaScript Hooks feature (GAP-001)."""

import pytest
from django.contrib.auth.models import AnonymousUser

from wireview.core.component import Component
from wireview.core.meta import WireviewMeta


class MockChannelLayer:
    """Mock channel layer for testing."""

    def __init__(self):
        self.sent_messages = []

    async def send(self, channel, message):
        self.sent_messages.append((channel, message))


class HookTestComponent(Component):
    """Test component with hook event handling."""

    _template_name = "test_hooks.html"
    count: int = 0
    last_hook_event: dict | None = None

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict):
        """Store received event and return response."""
        self.last_hook_event = {
            "hook_id": hook_id,
            "event": event,
            "payload": payload,
        }
        if event == "increment":
            self.count += payload.get("amount", 1)
            return {"new_count": self.count}
        elif event == "validate":
            return {"valid": payload.get("value", 0) > 0}
        return None


@pytest.mark.unit
class TestHandleHookEvent:
    """Tests for Component.handle_hook_event()."""

    @pytest.fixture
    def component(self):
        """Create a test component."""
        return HookTestComponent(
            user=AnonymousUser(),
            wire=WireviewMeta(params={}),
        )

    @pytest.mark.asyncio
    async def test_default_returns_none(self):
        """Base Component.handle_hook_event() returns None."""

        class BaseTestComponent(Component):
            _template_name = "test.html"

        component = BaseTestComponent(
            user=AnonymousUser(),
            wire=WireviewMeta(params={}),
        )
        result = await component.handle_hook_event(
            hook_id="test:Hook:123",
            event="test_event",
            payload={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_handler_receives_params(self, component):
        """Custom handler receives hook_id, event, and payload."""
        await component.handle_hook_event(
            hook_id="comp:MyHook:abc123",
            event="test_event",
            payload={"key": "value"},
        )

        assert component.last_hook_event is not None
        assert component.last_hook_event["hook_id"] == "comp:MyHook:abc123"
        assert component.last_hook_event["event"] == "test_event"
        assert component.last_hook_event["payload"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_custom_handler_returns_response(self, component):
        """Custom handler can return response data."""
        result = await component.handle_hook_event(
            hook_id="comp:Counter:123",
            event="increment",
            payload={"amount": 5},
        )

        assert result == {"new_count": 5}
        assert component.count == 5

    @pytest.mark.asyncio
    async def test_validate_event(self, component):
        """Validate event returns validation result."""
        valid_result = await component.handle_hook_event(
            hook_id="comp:Form:123",
            event="validate",
            payload={"value": 42},
        )
        assert valid_result == {"valid": True}

        invalid_result = await component.handle_hook_event(
            hook_id="comp:Form:123",
            event="validate",
            payload={"value": -1},
        )
        assert invalid_result == {"valid": False}

    @pytest.mark.asyncio
    async def test_unknown_event_returns_none(self, component):
        """Unknown event returns None."""
        result = await component.handle_hook_event(
            hook_id="comp:Hook:123",
            event="unknown_event",
            payload={},
        )
        assert result is None


@pytest.mark.unit
class TestPushEvent:
    """Tests for Component.push_event()."""

    @pytest.fixture
    def channel_layer(self):
        """Create mock channel layer."""
        return MockChannelLayer()

    @pytest.fixture
    def component(self, channel_layer):
        """Create test component with mock channel layer."""
        component = HookTestComponent(
            user=AnonymousUser(),
            wire=WireviewMeta(
                params={},
                channel_name="test-channel",
                channel_layer=channel_layer,
            ),
        )
        return component

    @pytest.mark.asyncio
    async def test_push_event_sends_message(self, component, channel_layer):
        """push_event sends message via channel layer."""
        await component.push_event("highlight", {"color": "yellow"})

        assert len(channel_layer.sent_messages) == 1
        channel, message = channel_layer.sent_messages[0]
        assert channel == "test-channel"
        assert message["type"] == "message_from_component"
        assert message["command"] == "push_event"
        assert message["kwargs"]["event"] == "highlight"
        assert message["kwargs"]["payload"] == {"color": "yellow"}
        assert message["kwargs"]["hook_id"] is None

    @pytest.mark.asyncio
    async def test_push_event_to_specific_hook(self, component, channel_layer):
        """push_event can target specific hook."""
        await component.push_event(
            "update_data",
            {"values": [1, 2, 3]},
            hook_id="comp:Chart:xyz",
        )

        assert len(channel_layer.sent_messages) == 1
        _, message = channel_layer.sent_messages[0]
        assert message["kwargs"]["hook_id"] == "comp:Chart:xyz"

    @pytest.mark.asyncio
    async def test_push_event_empty_payload(self, component, channel_layer):
        """push_event with no payload sends empty dict."""
        await component.push_event("notify")

        assert len(channel_layer.sent_messages) == 1
        _, message = channel_layer.sent_messages[0]
        assert message["kwargs"]["payload"] == {}


@pytest.mark.unit
class TestWireviewMetaSendPushEvent:
    """Tests for WireviewMeta._send_push_event()."""

    @pytest.fixture
    def channel_layer(self):
        """Create mock channel layer."""
        return MockChannelLayer()

    @pytest.fixture
    def meta(self, channel_layer):
        """Create WireviewMeta with mock channel layer."""
        return WireviewMeta(
            params={},
            channel_name="test-channel",
            channel_layer=channel_layer,
        )

    @pytest.mark.asyncio
    async def test_send_push_event_format(self, meta, channel_layer):
        """_send_push_event sends correctly formatted message."""
        await meta._send_push_event(
            component_id="test-comp-id",
            event="refresh",
            payload={"force": True},
            hook_id=None,
        )

        assert len(channel_layer.sent_messages) == 1
        _, message = channel_layer.sent_messages[0]
        assert message["command"] == "push_event"
        assert message["kwargs"]["component_id"] == "test-comp-id"
        assert message["kwargs"]["event"] == "refresh"
        assert message["kwargs"]["payload"] == {"force": True}
        assert message["kwargs"]["hook_id"] is None

    @pytest.mark.asyncio
    async def test_send_push_event_with_hook_id(self, meta, channel_layer):
        """_send_push_event includes hook_id when provided."""
        await meta._send_push_event(
            component_id="test-comp-id",
            event="update",
            payload={},
            hook_id="test-comp-id:Tooltip:123",
        )

        _, message = channel_layer.sent_messages[0]
        assert message["kwargs"]["hook_id"] == "test-comp-id:Tooltip:123"

    @pytest.mark.asyncio
    async def test_send_push_event_in_pending_mode(self, meta, channel_layer):
        """_send_push_event queues in pending mode."""
        meta.enter_pending_mode()

        await meta._send_push_event(
            component_id="test-comp-id",
            event="init",
            payload={"ready": True},
        )

        # Should be queued, not sent yet
        assert len(channel_layer.sent_messages) == 0
        assert len(meta._pending_operations) == 1

        # Flush sends the message
        await meta.flush_pending()
        assert len(channel_layer.sent_messages) == 1
