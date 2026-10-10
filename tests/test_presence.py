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


class _Clock:
    """A clock the test moves by hand, for the typing timer's sleep."""

    def __init__(self):
        self.now = 0.0
        self.sleepers: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, delay: float) -> None:
        wake = asyncio.get_running_loop().create_future()
        self.sleepers.append((self.now + delay, wake))
        await wake

    async def advance(self, seconds: float) -> None:
        await asyncio.sleep(0)  # a timer just created starts, and sleeps from now
        self.now = round(self.now + seconds, 6)
        for when, wake in self.sleepers:
            if round(when, 6) <= self.now and not wake.done():
                wake.set_result(None)
        await asyncio.sleep(0)  # let the woken timers start


@pytest.fixture
def presence_clock(monkeypatch):
    """The typing timer sleeps on a hand-moved clock instead of the wall clock.

    Only the presence module sees it: its ``asyncio`` is swapped for one whose
    ``sleep`` is the clock's, so the test's own waits stay real.
    """
    from wireview.features import presence

    clock = _Clock()

    class _Asyncio:
        sleep = staticmethod(clock.sleep)

        def __getattr__(self, name):
            return getattr(asyncio, name)

    monkeypatch.setattr(presence, "asyncio", _Asyncio())
    return clock


async def hear(view, action: str, user_id: str, ref: str | None = "tab", **data) -> None:
    """The tracker hears ``action`` about ``user_id``'s connection ``ref`` (None: a payload from before #202)."""
    kwargs = {"username": user_id, "state": "online", "metadata": {}, **data}
    if ref is not None:
        kwargs["presence_ref"] = ref
    await view.component.notification("presence.room.1", action=action, user_id=user_id, **kwargs)


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
        await eventually(clear_broadcasts, task=view.component._presence_typing_task)

        # Should have auto-cleared
        assert view.component._presence_is_typing is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_typing_timeout_that_fails_is_logged_at_once(self, caplog, monkeypatch):
        """The timer's broadcast is the only thing that clears typing for the others (#151)."""
        view = await mount(XProducerComponent, room_id=1, username="erin")
        await view.component.presence_set_typing(typing=True)
        task = view.component._presence_typing_task

        async def fails(*args, **kwargs):
            raise ConnectionError("the layer is gone")

        monkeypatch.setattr(XProducerComponent, "broadcast", fails)
        with caplog.at_level("ERROR", logger="wireview"):
            await asyncio.wait({task}, timeout=5)
            await asyncio.sleep(0)

        records = [r for r in caplog.records if r.name == "wireview" and r.exc_info]
        assert len(records) == 1, caplog.text
        assert "could not clear typing" in records[0].getMessage()
        assert "XProducerComponent" in records[0].getMessage()
        assert isinstance(records[0].exc_info[1], ConnectionError)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_typing_reset_timer_on_new_input(self, presence_clock):
        """Typing again should reset the auto-timeout timer.

        The timer runs on ``presence_clock``, so the test says exactly when the
        first timeout has passed and the second has not -- however slow the runner.
        """
        view = await mount(XProducerComponent, room_id=1, username="eve")
        view.wire._mock_channel_layer.clear()

        await view.component.presence_set_typing(typing=True)  # times out at 0.10
        first = view.component._presence_typing_task
        await presence_clock.advance(0.06)
        assert presence_clock.sleepers, "the timer sleeps on the test's clock, not the wall clock"
        await view.component.presence_set_typing(typing=True)  # resets: times out at 0.16
        await presence_clock.advance(0.06)  # 0.12: past the first timeout, short of the second

        # Kept running, the first timer has now cleared typing; reset, it ended in the second call
        done, _ = await asyncio.wait({first}, timeout=5)
        assert first in done
        assert view.component._presence_is_typing is True

        await presence_clock.advance(0.04)  # 0.16
        second = view.component._presence_typing_task
        done, _ = await asyncio.wait({second}, timeout=5)
        assert second in done
        assert view.component._presence_is_typing is False

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
        assert view.component.presence_get_user("alice").username == "alice"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_users_property(self):
        """presence_users should return list of tracked users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        await hear(view, "presence_join", "user1", username="User One")
        await hear(view, "presence_join", "user2", username="User Two", state="typing")

        users = view.component.presence_users
        assert [u.username for u in users] == ["User One", "User Two"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_online_count(self):
        """presence_online_count should count online users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        await hear(view, "presence_join", "user1", state="online")
        await hear(view, "presence_join", "user2", state="typing")
        await hear(view, "presence_join", "user3", state="offline")

        assert view.component.presence_online_count == 2

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_presence_typing_users(self):
        """presence_typing_users should return only typing users."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        await hear(view, "presence_join", "user1", username="User One", state="online")
        await hear(view, "presence_join", "user2", username="User Two", state="typing")

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
            presence_ref="tab",
            username="New User",
            state="online",
            metadata={},
        )

        assert view.component.presence_get_user("new_user").username == "New User"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_presence_leave(self):
        """notification() should handle presence_leave action."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Add user first
        await hear(view, "presence_join", "leaving_user", username="Leaving User")

        # Notify leave
        await view.component.notification(
            "presence.room.1",
            action="presence_leave",
            user_id="leaving_user",
            presence_ref="tab",
            username="Leaving User",
        )

        assert view.component.presence_get_user("leaving_user") is None
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_notification_presence_typing(self):
        """notification() should handle presence_typing action."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")

        # Add user first
        await hear(view, "presence_join", "typing_user", username="Typing User")

        # Notify typing
        await view.component.notification(
            "presence.room.1",
            action="presence_typing",
            user_id="typing_user",
            presence_ref="tab",
            username="Typing User",
            state="typing",
            metadata={},
        )

        assert view.component.presence_get_user("typing_user").state == PresenceState.TYPING

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
        assert tracker.component.presence_get_user("alice").is_typing()

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

        assert tracker.component.presence_get_user("alice").is_typing()

        # Wait for the timeout's clear broadcast
        clear_broadcasts = await eventually(
            lambda: [b for b in producer.presence_broadcasts if b.get("kwargs", {}).get("state") == "online"],
            task=producer.component._presence_typing_task,
        )

        # Tracker receives clear notification
        await tracker.component.notification(
            "presence.room.1",
            **clear_broadcasts[-1]["kwargs"],
        )

        # Should no longer be typing
        assert not tracker.component.presence_get_user("alice").is_typing()


async def relay(producer, tracker) -> None:
    """Deliver what ``producer`` broadcast since the last relay to ``tracker``, in order."""
    layer = producer.wire._mock_channel_layer
    for message in [m for m in layer.sent_messages if m.get("kwargs", {}).get("action", "").startswith("presence_")]:
        await tracker.component.notification("presence.room.1", **message["kwargs"])
    layer.clear()


class TestPresencePerConnection:
    """A user is listed while any of their connections is, and types while any of them types (#202)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_user_leaves_with_their_last_connection(self):
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_join", "ann", ref="tab1")
        await hear(view, "presence_join", "ann", ref="tab2")

        await hear(view, "presence_leave", "ann", ref="tab1")
        assert [u.user_id for u in view.component.presence_users] == ["ann"]
        assert view.component.presence_online_count == 1

        await hear(view, "presence_leave", "ann", ref="tab2")
        assert view.component.presence_users == []
        assert view.component.presence_get_user("ann") is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_user_types_while_any_connection_types(self):
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_join", "ann", ref="tab1")
        await hear(view, "presence_join", "ann", ref="tab2")

        await hear(view, "presence_typing", "ann", ref="tab1", state="typing")
        assert view.component.presence_get_user("ann").is_typing()
        await hear(view, "presence_typing", "ann", ref="tab2", state="typing")
        await hear(view, "presence_typing", "ann", ref="tab2", state="online")
        assert [u.user_id for u in view.component.presence_typing_users] == ["ann"]

        await hear(view, "presence_typing", "ann", ref="tab1", state="online")
        assert view.component.presence_typing_users == []
        assert view.component.presence_get_user("ann").is_online()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_merged_entry_takes_the_latest_name_and_metadata(self):
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_join", "ann", ref="tab1", username="Ann", metadata={"tab": 1})
        await hear(view, "presence_join", "ann", ref="tab2", username="Ann B.", metadata={"tab": 2})

        user = view.component.presence_get_user("ann")
        assert (user.username, user.metadata) == ("Ann B.", {"tab": 2})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_each_connection_times_out_its_own_typing(self, presence_clock):
        """Two tabs of one user type; the first to time out does not clear the user."""
        tab1 = await mount(XProducerComponent, id="tab1", room_id=1, username="ann")
        tab2 = await mount(XProducerComponent, id="tab2", room_id=1, username="ann")
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await relay(tab1, view)
        await relay(tab2, view)

        await tab1.component.presence_set_typing(True)  # times out at 0.10
        await presence_clock.advance(0.06)
        await tab2.component.presence_set_typing(True)  # times out at 0.16
        await relay(tab1, view)
        await relay(tab2, view)
        assert view.component.presence_get_user("ann").is_typing()

        await presence_clock.advance(0.06)  # 0.12: tab1 cleared, tab2 still types
        await asyncio.wait({tab1.component._presence_typing_task}, timeout=5)
        await relay(tab1, view)
        assert view.component.presence_get_user("ann").is_typing()

        await presence_clock.advance(0.04)  # 0.16
        await asyncio.wait({tab2.component._presence_typing_task}, timeout=5)
        await relay(tab2, view)
        assert not view.component.presence_get_user("ann").is_typing()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_components_of_one_connection_share_a_ref(self):
        """A connection's producer and tracker speak for one meta; the ref is not the connection id."""
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        tracker = await mount(XTrackerComponent, room_id=1, username="ann")
        other = await mount(XProducerComponent, room_id=1, username="ann")
        for view in (producer, tracker):
            view.wire.connection_id = "conn-secret"
        other.wire.connection_id = "conn-other"
        producer.wire._mock_channel_layer.clear()
        tracker.wire._mock_channel_layer.clear()

        await producer.component.presence_join()
        await tracker.component.presence_track_self(username="ann")
        await other.component.presence_join()

        join = producer.presence_broadcasts[0]["kwargs"]
        request = tracker.presence_broadcasts[0]["kwargs"]
        assert join["presence_ref"] == request["requester_ref"]
        assert join["presence_ref"] != other.presence_broadcasts[-1]["kwargs"]["presence_ref"]
        assert "conn-secret" not in join["presence_ref"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_sync_answer_fills_only_a_connection_not_heard_from(self):
        """An answer can be older than what the connection broadcast since: it does not overwrite it."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_typing", "ann", ref="tab1", state="typing")

        await hear(view, "presence_sync_response", "ann", ref="tab1", state="online")
        await hear(view, "presence_sync_response", "ann", ref="tab2", state="online")

        assert view.component.presence_get_user("ann").is_typing()
        assert set(view.component._presence_registry["ann"]) == {"tab1", "tab2"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_tracker_answers_a_sync_for_its_own_connection_only(self):
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        await view.component.presence_track_self(username="ann")
        my_ref = view.presence_broadcasts[-1]["kwargs"]["requester_ref"]
        await hear(view, "presence_join", "ann", ref=my_ref)  # its producer announced the connection
        await hear(view, "presence_join", "bob", ref="bobs-tab")
        view.wire._mock_channel_layer.clear()

        # Its own request, heard back: no answer
        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="ann", requester_ref=my_ref
        )
        assert view.presence_broadcasts == []

        # Another tab of the same user asks: answered, for this connection
        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="ann", requester_ref="ann-tab2"
        )
        [answer] = [b["kwargs"] for b in view.presence_broadcasts]
        assert (answer["action"], answer["user_id"], answer["presence_ref"]) == (
            "presence_sync_response",
            "ann",
            my_ref,
        )

        # And that tab's own answer is kept: it is another connection of the user
        await hear(view, "presence_sync_response", "ann", ref="ann-tab2")
        assert set(view.component._presence_registry["ann"]) == {my_ref, "ann-tab2"}


class TestPresenceTrackerAlone:
    """A connection with a tracker and no producer never says it joined or left (#202)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_does_not_answer_a_sync(self):
        """Answering put it on the lists of those who came later, and nothing took it off."""
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        await view.component.presence_track_self(username="ann")
        my_ref = view.presence_broadcasts[-1]["kwargs"]["requester_ref"]
        view.wire._mock_channel_layer.clear()

        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="bob", requester_ref="bobs-tab"
        )
        assert view.presence_broadcasts == []
        # It still lists itself
        assert [u.user_id for u in view.component.presence_users] == ["ann"]

        # Once its producer's join comes back, it answers; once its leave does, it stops
        await hear(view, "presence_join", "ann", ref=my_ref)
        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="bob", requester_ref="bobs-tab"
        )
        assert [b["kwargs"]["action"] for b in view.presence_broadcasts] == ["presence_sync_response"]

        view.wire._mock_channel_layer.clear()
        await hear(view, "presence_leave", "ann", ref=my_ref)
        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="bob", requester_ref="bobs-tab"
        )
        assert view.presence_broadcasts == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_tracking_self_keeps_what_the_producer_announced(self):
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        view.wire.connection_id = "conn"
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        producer.wire.connection_id = "conn"
        producer.wire._mock_channel_layer.clear()
        await producer.component.presence_join()
        await relay(producer, view)

        await view.component.presence_track_self(username="Ann (tracker)")

        assert view.component.presence_get_user("ann").username == "ann"


class TestPresenceLegacyPayload:
    """A message without presence_ref comes from a process older than #202 (a rolling deploy).

    Such a process keeps one meta per user, so its messages land on one meta of
    the user: the user's connections on newer processes are left alone.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_legacy_leave_keeps_the_users_other_connections(self):
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_join", "ann", ref=None)
        await hear(view, "presence_join", "ann", ref="new-tab")

        await hear(view, "presence_leave", "ann", ref=None)
        assert [u.user_id for u in view.component.presence_users] == ["ann"]

        await hear(view, "presence_leave", "ann", ref="new-tab")
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_legacy_messages_of_one_user_share_one_meta(self):
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_join", "ann", ref=None)
        await hear(view, "presence_typing", "ann", ref=None, state="typing")
        assert view.component.presence_get_user("ann").is_typing()
        assert list(view.component._presence_registry["ann"]) == [""]

        await hear(view, "presence_leave", "ann", ref=None)
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_legacy_sync_request_is_answered(self):
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        await view.component.presence_track_self(username="ann")
        await hear(view, "presence_join", "ann", ref=view.presence_broadcasts[-1]["kwargs"]["requester_ref"])
        view.wire._mock_channel_layer.clear()

        await view.component.notification("presence.room.1", action="presence_sync_request", requester_id="bob")
        [answer] = [b["kwargs"] for b in view.presence_broadcasts]
        assert answer["action"] == "presence_sync_response"
        assert answer["user_id"] == "ann"

        await hear(view, "presence_sync_response", "bob", ref=None)
        assert view.component.presence_get_user("bob") is not None
