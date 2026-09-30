"""
Tests for the presence module.

Tests cover:
- PresenceMixin: typing indicators with auto-timeout
- PresenceTrackerMixin: tracking online users
- Integration between producer and consumer components
"""

import asyncio

import pytest
from testproj.waiting import eventually

from wireview import Component
from wireview.features.presence import (
    PresenceConfig,
    PresenceMixin,
    PresenceState,
    PresenceTrackerMixin,
    PresenceUser,
)
from wireview.testing import MockChannelLayer, mount


@pytest.fixture(params=["prompt", "slow"])
def broadcast_pace(request, monkeypatch):
    """A channel layer that answers at once, and one that takes its time (#143).

    "slow" holds every group_send for longer than the typing timeout, as a busy
    runner may: the clear the timeout broadcasts lands well after the timeout.
    """
    if request.param == "slow":
        send = MockChannelLayer.group_send

        async def slow(self, group, message):
            await asyncio.sleep(0.2)
            await send(self, group, message)

        monkeypatch.setattr(MockChannelLayer, "group_send", slow)
    return request.param


# Test Components (prefixed with X to avoid pytest collection)
class XProducerComponent(PresenceMixin, Component):
    """Test component that produces presence updates."""

    class Meta:
        template_name = "test_presence.html"
        presence = PresenceConfig(typing_timeout=0.1)  # Fast timeout for tests

    room_id: int = 1
    username: str = "test_user"

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_user_id(self) -> str:
        return self.username

    def _presence_username(self) -> str:
        return self.username

    async def joined(self):
        """Call presence_join on mount."""
        await self.presence_join()


class XTrackerComponent(PresenceTrackerMixin, Component):
    """Test component that tracks presence of other users."""

    class Meta:
        template_name = "test_presence.html"

    room_id: int = 1
    username: str = "tracker_user"

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_my_user_id(self) -> str:
        return self.username

    def get_subscriptions(self) -> set[str]:
        return {self._presence_channel()}


# Unit Tests for PresenceUser
class TestPresenceUser:
    """Tests for PresenceUser dataclass."""

    @pytest.mark.unit
    def test_is_typing_when_typing(self):
        """is_typing() returns True when state is TYPING."""
        user = PresenceUser(
            user_id="1",
            username="alice",
            state=PresenceState.TYPING,
        )
        assert user.is_typing() is True

    @pytest.mark.unit
    def test_is_typing_when_online(self):
        """is_typing() returns False when state is ONLINE."""
        user = PresenceUser(
            user_id="1",
            username="alice",
            state=PresenceState.ONLINE,
        )
        assert user.is_typing() is False

    @pytest.mark.unit
    def test_is_online_when_online(self):
        """is_online() returns True for non-offline states."""
        user = PresenceUser(user_id="1", username="alice")
        assert user.is_online() is True

        user.state = PresenceState.TYPING
        assert user.is_online() is True

    @pytest.mark.unit
    def test_is_online_when_offline(self):
        """is_online() returns False when state is OFFLINE."""
        user = PresenceUser(
            user_id="1",
            username="alice",
            state=PresenceState.OFFLINE,
        )
        assert user.is_online() is False


# Unit Tests for PresenceMixin
class TestPresenceMixin:
    """Tests for PresenceMixin."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_join_broadcasts(self):
        """presence_join() should broadcast join action."""
        view = await mount(XProducerComponent, room_id=1, username="alice")

        # Find the join broadcast
        join_broadcasts = [b for b in view.presence_broadcasts if b.get("kwargs", {}).get("action") == "presence_join"]
        assert len(join_broadcasts) == 1
        assert join_broadcasts[0]["kwargs"]["username"] == "alice"
        assert join_broadcasts[0]["kwargs"]["state"] == "online"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_set_typing_broadcasts(self):
        """presence_set_typing() should broadcast typing state."""
        view = await mount(XProducerComponent, room_id=1, username="bob")

        # Clear initial broadcasts from joined()
        view.wire._mock_channel_layer.clear()

        # Set typing
        await view.component.presence_set_typing(typing=True)

        typing_broadcasts = [
            b for b in view.presence_broadcasts if b.get("kwargs", {}).get("action") == "presence_typing"
        ]
        assert len(typing_broadcasts) == 1
        assert typing_broadcasts[0]["kwargs"]["state"] == "typing"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_typing_state_change_only_broadcasts_once(self):
        """Setting same typing state shouldn't broadcast again."""
        view = await mount(XProducerComponent, room_id=1, username="charlie")
        view.wire._mock_channel_layer.clear()

        # Set typing twice
        await view.component.presence_set_typing(typing=True)
        await view.component.presence_set_typing(typing=True)

        # Should only broadcast once (state didn't change second time)
        typing_broadcasts = [
            b for b in view.presence_broadcasts if b.get("kwargs", {}).get("action") == "presence_typing"
        ]
        assert len(typing_broadcasts) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_typing_auto_timeout(self, broadcast_pace):
        """Typing should auto-clear after timeout."""
        view = await mount(XProducerComponent, room_id=1, username="dave")
        view.wire._mock_channel_layer.clear()

        # Set typing
        await view.component.presence_set_typing(typing=True)
        assert view.component._presence_is_typing is True

        def clear_broadcasts():
            return [
                b
                for b in view.presence_broadcasts
                if b.get("kwargs", {}).get("action") == "presence_typing"
                and b.get("kwargs", {}).get("state") == "online"
            ]

        # The timeout broadcasts the clear, whenever the runner gets to it
        await eventually(clear_broadcasts)

        # Should have auto-cleared
        assert view.component._presence_is_typing is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_typing_reset_timer_on_new_input(self):
        """Typing again should reset the auto-timeout timer."""
        view = await mount(XProducerComponent, room_id=1, username="eve")
        view.wire._mock_channel_layer.clear()

        # Set typing
        await view.component.presence_set_typing(typing=True)

        # Wait partial timeout
        await asyncio.sleep(0.05)

        # Type again (should reset timer)
        await view.component.presence_set_typing(typing=True)

        # Wait another partial timeout
        await asyncio.sleep(0.05)

        # Should still be typing (timer was reset)
        assert view.component._presence_is_typing is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_leave_clears_typing(self):
        """presence_leave() should cancel typing timeout."""
        view = await mount(XProducerComponent, room_id=1, username="frank")

        # Set typing
        await view.component.presence_set_typing(typing=True)
        assert view.component._presence_typing_task is not None

        # Leave
        await view.component.presence_leave()

        # Task should be cancelled
        assert view.component._presence_typing_task is None or view.component._presence_typing_task.done()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_channel_format(self):
        """Channel should be formatted correctly."""
        view = await mount(XProducerComponent, room_id=42, username="test")
        assert view.component._presence_channel() == "presence.room.42"


# Unit Tests for PresenceTrackerMixin
class TestPresenceTrackerMixin:
    """Tests for PresenceTrackerMixin."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_track_self(self):
        """presence_track_self() should add self to registry."""
        view = await mount(XTrackerComponent, room_id=1, username="alice")

        # Call track_self
        await view.component.presence_track_self(username="alice")

        # Should be in registry
        assert "alice" in view.component._presence_registry
        assert view.component._presence_registry["alice"].username == "alice"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_users_property(self):
        """presence_users should return list of tracked users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Add some users manually
        view.component._presence_registry["user1"] = PresenceUser(
            user_id="user1",
            username="User One",
        )
        view.component._presence_registry["user2"] = PresenceUser(
            user_id="user2",
            username="User Two",
            state=PresenceState.TYPING,
        )

        users = view.component.presence_users
        assert len(users) == 2

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_online_count(self):
        """presence_online_count should count online users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        view.component._presence_registry["user1"] = PresenceUser(
            user_id="user1",
            username="User One",
            state=PresenceState.ONLINE,
        )
        view.component._presence_registry["user2"] = PresenceUser(
            user_id="user2",
            username="User Two",
            state=PresenceState.TYPING,
        )
        view.component._presence_registry["user3"] = PresenceUser(
            user_id="user3",
            username="User Three",
            state=PresenceState.OFFLINE,
        )

        assert view.component.presence_online_count == 2

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_typing_users(self):
        """presence_typing_users should return only typing users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        view.component._presence_registry["user1"] = PresenceUser(
            user_id="user1",
            username="User One",
            state=PresenceState.ONLINE,
        )
        view.component._presence_registry["user2"] = PresenceUser(
            user_id="user2",
            username="User Two",
            state=PresenceState.TYPING,
        )

        typing_users = view.component.presence_typing_users
        assert len(typing_users) == 1
        assert typing_users[0].username == "User Two"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_presence_join(self):
        """notification() should handle presence_join action."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        await view.component.notification(
            "presence.room.1",
            action="presence_join",
            user_id="new_user",
            username="New User",
            state="online",
            metadata={},
        )

        assert "new_user" in view.component._presence_registry
        assert view.component._presence_registry["new_user"].username == "New User"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_presence_leave(self):
        """notification() should handle presence_leave action."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Add user first
        view.component._presence_registry["leaving_user"] = PresenceUser(
            user_id="leaving_user",
            username="Leaving User",
        )

        # Notify leave
        await view.component.notification(
            "presence.room.1",
            action="presence_leave",
            user_id="leaving_user",
            username="Leaving User",
        )

        assert "leaving_user" not in view.component._presence_registry

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_presence_typing(self):
        """notification() should handle presence_typing action."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Add user first
        view.component._presence_registry["typing_user"] = PresenceUser(
            user_id="typing_user",
            username="Typing User",
        )

        # Notify typing
        await view.component.notification(
            "presence.room.1",
            action="presence_typing",
            user_id="typing_user",
            username="Typing User",
            state="typing",
            metadata={},
        )

        assert view.component._presence_registry["typing_user"].state == PresenceState.TYPING

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_non_presence_action(self):
        """notification() should pass non-presence actions to parent."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Non-presence action should not cause error
        await view.component.notification(
            "some.channel",
            action="other_action",
            data="test",
        )

        # Should not affect registry
        # (No assertion needed - just verify no exception)


# Integration Tests
class TestPresenceIntegration:
    """Integration tests for presence system."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_producer_to_tracker_flow(self):
        """Test presence flow from producer to tracker."""
        # Create producer
        producer = await mount(XProducerComponent, room_id=1, username="alice")

        # Create tracker
        tracker = await mount(XTrackerComponent, room_id=1, username="bob")
        await tracker.component.presence_track_self(username="bob")

        # Simulate producer typing by getting the broadcast data
        producer.wire._mock_channel_layer.clear()
        await producer.component.presence_set_typing(typing=True)

        # Get the broadcast
        broadcasts = producer.presence_broadcasts
        assert len(broadcasts) == 1

        # Simulate tracker receiving the notification
        broadcast_kwargs = broadcasts[0]["kwargs"]
        await tracker.component.notification(
            "presence.room.1",
            **broadcast_kwargs,
        )

        # Tracker should now show alice as typing
        assert "alice" in tracker.component._presence_registry
        assert tracker.component._presence_registry["alice"].is_typing()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_typing_timeout_clears_on_tracker(self, broadcast_pace):
        """Test that typing auto-timeout is reflected on tracker."""
        # Create producer with very short timeout
        producer = await mount(XProducerComponent, room_id=1, username="alice")

        # Create tracker
        tracker = await mount(XTrackerComponent, room_id=1, username="bob")

        # Producer starts typing
        producer.wire._mock_channel_layer.clear()
        await producer.component.presence_set_typing(typing=True)

        # Tracker receives typing notification
        broadcast = producer.presence_broadcasts[0]
        await tracker.component.notification("presence.room.1", **broadcast["kwargs"])

        assert tracker.component._presence_registry["alice"].is_typing()

        # Wait for the timeout's clear broadcast
        clear_broadcasts = await eventually(
            lambda: [b for b in producer.presence_broadcasts if b.get("kwargs", {}).get("state") == "online"]
        )

        # Tracker receives clear notification
        await tracker.component.notification(
            "presence.room.1",
            **clear_broadcasts[-1]["kwargs"],
        )

        # Should no longer be typing
        assert not tracker.component._presence_registry["alice"].is_typing()
