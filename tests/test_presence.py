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
from wireview.core import notifications
from wireview.features import presence
from wireview.features.presence import (
    PresenceConfig,
    PresenceMixin,
    PresenceState,
    PresenceTrackerMixin,
    PresenceUser,
    presence_channels,
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


async def deliver(view, channel: str = "presence.room.1", **kwargs) -> None:
    """A notification reaches ``view`` alone, the way a session hands it over (#202).

    Through the rule sessions and mounts share (``notifications.deliver``): the
    framework hears a joined producer's presence channel (``presence.hear``),
    and the component's ``notification()`` gets only the channels it subscribes
    to. The socket path is in tests/test_connections.py.
    """
    await notifications.deliver(view._host, "notification", {"channel": channel, "kwargs": kwargs})


def _presence_template(cls, template_name=None):
    """What both components draw: a mounted one renders after each notification it hears (#201)."""
    from django.template import Template

    return Template("{% load wireview %}<div {% tag_header %}>{{ username }}</div>")


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

    _get_template = classmethod(_presence_template)


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

    _get_template = classmethod(_presence_template)


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
# A tracker renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
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
# A tracker renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
class TestPresenceIntegration:
    """Integration tests for presence system."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_producer_to_tracker_flow(self):
        """Test presence flow from producer to tracker: the tracker hears it as a page would (#201)."""
        # Create producer
        producer = await mount(XProducerComponent, room_id=1, username="alice")

        # Create tracker
        tracker = await mount(XTrackerComponent, room_id=1, username="bob")
        await tracker.component.presence_track_self(username="bob")

        producer.wire._mock_channel_layer.clear()
        await producer.component.presence_set_typing(typing=True)

        # Get the broadcast
        broadcasts = producer.presence_broadcasts
        assert len(broadcasts) == 1

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

        # Producer starts typing, and the tracker hears it
        await producer.component.presence_set_typing(typing=True)

        assert tracker.component.presence_get_user("alice").is_typing()

        # The timeout's clear broadcast reaches the tracker the same way
        await eventually(
            lambda: not tracker.component.presence_get_user("alice").is_typing(),
            task=producer.component._presence_typing_task,
        )


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
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
        """Two tabs of one user type; the first to time out does not clear the user.

        The tracker hears the producers as a page would (#201): mounted first, it hears their joins.
        """
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        tab1 = await mount(XProducerComponent, id="tab1", room_id=1, username="ann")
        tab2 = await mount(XProducerComponent, id="tab2", room_id=1, username="ann")

        await tab1.component.presence_set_typing(True)  # times out at 0.10
        await presence_clock.advance(0.06)
        await tab2.component.presence_set_typing(True)  # times out at 0.16
        assert view.component.presence_get_user("ann").is_typing()

        await presence_clock.advance(0.06)  # 0.12: tab1 cleared, tab2 still types
        await asyncio.wait({tab1.component._presence_typing_task}, timeout=5)
        assert view.component.presence_get_user("ann").is_typing()

        await presence_clock.advance(0.04)  # 0.16
        await asyncio.wait({tab2.component._presence_typing_task}, timeout=5)
        assert not view.component.presence_get_user("ann").is_typing()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_components_of_one_connection_share_a_tab(self):
        """A connection's tracker names the tab its producers are of; no key is the connection id.

        Each producer speaks for a meta of its own (its tab and its id), and
        does not answer the sync request of its own tab's tracker.
        """
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
        assert request["requester_ref"] == presence._presence_tab(producer.component)
        assert join["presence_ref"] != request["requester_ref"]
        assert join["presence_ref"] != other.presence_broadcasts[-1]["kwargs"]["presence_ref"]
        assert "conn-secret" not in join["presence_ref"] + request["requester_ref"]
        producer.wire._mock_channel_layer.clear()
        await deliver(
            producer, action="presence_sync_request", requester_id="ann", requester_ref=request["requester_ref"]
        )
        assert producer.presence_broadcasts == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_sync_answer_fills_only_a_connection_not_heard_from(self):
        """An answer can be older than what the connection broadcast since: it does not overwrite it."""
        view = await mount(XTrackerComponent, room_id=1, username="tracker")
        await hear(view, "presence_typing", "ann", ref="tab1", state="typing")

        await hear(view, "presence_sync_response", "ann", ref="tab1", state="online")
        await hear(view, "presence_sync_response", "ann", ref="tab2", state="online")

        assert view.component.presence_get_user("ann").is_typing()
        assert set(view.component._presence_registry["presence.room.1"]["ann"]) == {"tab1", "tab2"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_producer_answers_a_sync_for_its_own_tab_only(self):
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        tracker = await mount(XTrackerComponent, room_id=1, username="ann")
        for view in (producer, tracker):
            view.wire.tab_id = "ann-tab-one-page-load"
        await tracker.component.presence_track_self(username="ann")
        my_ref = tracker.presence_broadcasts[-1]["kwargs"]["requester_ref"]
        producer.wire._mock_channel_layer.clear()

        # Its own tab's request: no answer, that tab's tracker lists it itself
        assert await ask(producer, requester_ref=my_ref) == []

        # Another tab of the same user asks: answered, for this producer
        [answer] = await ask(producer, requester_ref="ann-tab2")
        assert (answer["action"], answer["user_id"], answer["presence_ref"]) == (
            "presence_sync_response",
            "ann",
            presence._presence_ref(producer.component),
        )

        # And that tab's own answer is kept: it is another tab of the user. The
        # tracker heard this producer's answer too, as a page hears it (#201)
        await hear(tracker, "presence_sync_response", "ann", ref="ann-tab2")
        assert set(tracker.component._presence_registry["presence.room.1"]["ann"]) == {
            my_ref,
            "ann-tab2",
            presence._presence_ref(producer.component),
        }


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
class TestPresenceTrackerAlone:
    """A connection with a tracker and no producer never says it joined or left (#202)."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_does_not_answer_a_sync(self):
        """Answering put it on the lists of those who came later, and nothing took it off.

        Not even once a producer of its tab announced it: that producer answers.
        """
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

        await hear(view, "presence_join", "ann", ref=my_ref)
        await view.component.notification(
            "presence.room.1", action="presence_sync_request", requester_id="bob", requester_ref="bobs-tab"
        )
        assert view.presence_broadcasts == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_its_own_entry_merges_with_its_tabs_producer(self):
        """The tracker's own entry is its tab's, beside the meta of the tab's producer.

        The list shows the user once, typing while the producer types, and the
        producer's leave (a hidden widget) does not take the tracker's user off
        its own list.
        """
        # The producer first: what it said at its mount, before it is given the
        # tracker's connection, the tracker never hears (#201)
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        for each in (view, producer):
            each.wire.connection_id = "conn"
        await producer.component.presence_join()
        await view.component.presence_track_self(username="ann")
        assert len(view.component._presence_registry["presence.room.1"]["ann"]) == 2
        assert [u.user_id for u in view.component.presence_users] == ["ann"]

        await producer.component.presence_set_typing(True)
        assert view.component.presence_get_user("ann").is_typing()

        await producer.component.presence_leave()
        user = view.component.presence_get_user("ann")
        assert user is not None and not user.is_typing()


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
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
        assert list(view.component._presence_registry["presence.room.1"]["ann"]) == [""]

        await hear(view, "presence_leave", "ann", ref=None)
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_legacy_sync_request_is_answered(self):
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        view = await mount(XTrackerComponent, room_id=1, username="ann")
        producer.wire._mock_channel_layer.clear()

        await deliver(producer, action="presence_sync_request", requester_id="bob")
        [answer] = [b["kwargs"] for b in producer.presence_broadcasts]
        assert answer["action"] == "presence_sync_response"
        assert answer["user_id"] == "ann"

        await hear(view, "presence_sync_response", "bob", ref=None)
        assert view.component.presence_get_user("bob") is not None


class XNarrowProducer(XProducerComponent):
    """A producer naming its own topics without super(), as components usually do."""

    def get_subscriptions(self) -> set[str]:
        return {"chat.room.1"}


class XProducerThenTracker(PresenceMixin, PresenceTrackerMixin, Component):
    class Meta:
        template_name = "test_presence.html"

    username: str = "ann"

    def _presence_topic(self) -> str:
        return "room.1"

    def _presence_user_id(self) -> str:
        return self.username

    def _presence_my_user_id(self) -> str:
        return self.username

    def _presence_username(self) -> str:
        return self.username

    def get_subscriptions(self) -> set[str]:
        # A tracker hears its channel because it subscribes to it
        return super().get_subscriptions() | {self._presence_channel()}

    _get_template = classmethod(_presence_template)


class XSubclassedTab(XProducerThenTracker):
    """Its own receiver over a class that produces and tracks, passing everything on."""

    async def notification(self, channel: str, **kwargs):
        await super().notification(channel, **kwargs)


class XTrackerThenProducer(PresenceTrackerMixin, PresenceMixin, Component):
    class Meta:
        template_name = "test_presence.html"

    username: str = "ann"

    _presence_topic = XProducerThenTracker._presence_topic
    _presence_user_id = XProducerThenTracker._presence_user_id
    _presence_my_user_id = XProducerThenTracker._presence_my_user_id
    _presence_username = XProducerThenTracker._presence_username

    def get_subscriptions(self) -> set[str]:
        return super().get_subscriptions() | {self._presence_channel()}

    _get_template = classmethod(_presence_template)


async def ask(view, channel: str = "presence.room.1", requester_ref: str = "bobs-tab") -> list[dict]:
    """A sync request from another tab reaches ``view``; what it broadcast in answer."""
    view.wire._mock_channel_layer.clear()
    await deliver(view, channel, action="presence_sync_request", requester_id="bob", requester_ref=requester_ref)
    return [b["kwargs"] for b in view.presence_broadcasts]


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
class TestPresenceProducerAnswers:
    """The producer answers sync requests: it is what other people's lists show (#202).

    A tracker answered once it heard its page's join, which it never did when the
    page's producer was a root joined before it, or when the tracker was made
    after the producer joined (joined again after an exception, a page a sticky
    producer moved to).
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_answers_between_its_join_and_its_leave(self):
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        [answer] = await ask(producer)
        assert (answer["action"], answer["user_id"], answer["state"]) == ("presence_sync_response", "ann", "online")
        assert answer["presence_ref"] == producer.presence_broadcasts[0]["kwargs"]["presence_ref"]

        await producer.component.presence_set_typing(True)
        [answer] = await ask(producer)
        assert answer["state"] == "typing"

        await producer.component.presence_leave()
        assert await ask(producer) == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_does_not_answer_before_it_joins(self):
        class XQuietProducer(XProducerComponent):
            async def joined(self):
                pass

        producer = await mount(XQuietProducer, room_id=1, username="ann")
        assert await ask(producer) == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_answers_on_its_own_channel_only(self):
        producer = await mount(XProducerComponent, room_id=1, username="ann")
        assert await ask(producer, channel="presence.room.2") == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_its_connection_hears_its_channel_whatever_its_subscriptions_say(self):
        """The framework's channel is apart from the app's: get_subscriptions() is the app's own."""
        producer = await mount(XNarrowProducer, room_id=1, username="ann")
        assert producer.component.get_subscriptions() == {"chat.room.1"}
        assert presence_channels(producer.component) == {"presence.room.1"}
        assert presence_channels((await mount(XProducerComponent, room_id=2)).component) == {"presence.room.2"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_hears_its_channel_while_it_is_joined(self):
        """Not before presence_join(): a topic joined() decides is not known yet, and there is nothing to answer."""

        class XLateProducer(XProducerComponent):
            async def joined(self):
                pass

        producer = await mount(XLateProducer, room_id=1, username="ann")
        assert presence_channels(producer.component) == set()
        await producer.component.presence_join()
        assert presence_channels(producer.component) == {"presence.room.1"}
        await producer.component.presence_leave()
        assert presence_channels(producer.component) == set()
        assert producer.component.get_subscriptions() == set()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_hears_its_channel_whatever_an_app_mixin_returns(self):
        class RoomMixin:
            def get_subscriptions(self) -> set[str]:
                return {"chat.room.1"}

        class XMixedProducer(RoomMixin, XProducerComponent):
            pass

        producer = await mount(XMixedProducer, room_id=1, username="ann")
        assert producer.component.get_subscriptions() == {"chat.room.1"}
        assert presence_channels(producer.component) == {"presence.room.1"}

    @pytest.mark.unit
    def test_the_mixin_leaves_the_app_methods_alone(self):
        """A producer base combined with an app base keeps the app base's methods (#202, R1).

        Python's MRO puts the app base before Component. A mixin that replaced the
        methods on the class it was mixed into hid them from every subclass.
        """
        from django.contrib.auth.models import AnonymousUser

        from wireview.core.component import Component as Base
        from wireview.core.meta import WireviewMeta

        class XProducerBase(PresenceMixin, Component):
            pass

        class XRoomBase(Component):
            def get_subscriptions(self) -> set[str]:
                return super().get_subscriptions() | {"chat.room.1"}

            async def notification(self, channel: str, **kwargs):
                await super().notification(channel, **kwargs)

        class XDiamond(XProducerBase, XRoomBase):
            pass

        assert XDiamond.get_subscriptions is XRoomBase.get_subscriptions
        assert XDiamond.notification is XRoomBase.notification
        assert XProducerComponent.get_subscriptions is Base.get_subscriptions
        assert XProducerComponent.notification is Base.notification
        component = XDiamond(user=AnonymousUser(), wire=WireviewMeta(params={}), id="d")
        assert component.get_subscriptions() == {"chat.room.1"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_mounted_producer_answers_a_mounted_trackers_sync(self):
        """Mounted, it listens on its presence channel as its connection would (#201, #202).

        It does not subscribe to that channel: only the framework hears it there,
        and the tracker mounted after its join learns of it from the answer.
        """
        heard: list[str] = []

        class XUnsubscribedProducer(XNarrowProducer):
            async def notification(self, channel: str, **kwargs):
                heard.append(channel)

        producer = await mount(XUnsubscribedProducer, id="ann-widget", room_id=1, username="ann")
        tracker = await mount(XTrackerComponent, room_id=1, username="bob")
        await tracker.component.presence_track_self(username="bob")

        assert [u.user_id for u in tracker.component.presence_users] == ["bob", "ann"]
        assert heard == []
        assert not [m for m in producer.sent_messages if m["type"] == "render"]

        # Gone from the channel with its leave: a later tracker hears no answer
        await producer.component.presence_leave()
        later = await mount(XTrackerComponent, room_id=1, username="cid")
        await later.component.presence_track_self(username="cid")
        assert [u.user_id for u in later.component.presence_users] == ["cid"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_its_receiver_does_not_hear_what_only_the_framework_hears(self):
        """Its notification() hears its own channels only, as before #202; the sync is still answered."""
        heard: list[str] = []

        class XListeningProducer(XNarrowProducer):
            async def notification(self, channel: str, **kwargs):
                heard.append(kwargs["message"])

        producer = await mount(XListeningProducer, room_id=1, username="ann")
        await deliver(producer, action="presence_join", user_id="bob", presence_ref="bobs-tab")
        [answer] = await ask(producer)
        assert answer["action"] == "presence_sync_response"
        await deliver(producer, "chat.room.1", message="hi")
        assert heard == ["hi"]

    @pytest.mark.unit
    def test_a_broadcast_to_a_topic_it_does_not_subscribe_to_is_refused(self):
        from wireview import Broadcast

        class XTopicProducer(XProducerComponent):
            class Meta:
                subscriptions = {"chat.room.1"}

        Broadcast(XTopicProducer, "chat.room.1")
        with pytest.raises(ValueError, match="does not subscribe"):
            Broadcast(XTopicProducer, "chat.room.2")

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize("component", [XProducerThenTracker, XTrackerThenProducer, XSubclassedTab])
    async def test_a_class_that_produces_and_tracks_answers_once(self, component):
        view = await mount(component)
        await view.component.presence_join()
        [answer] = await ask(view)
        assert answer["action"] == "presence_sync_response"
        # And it tracks what it hears, rendering it: its own join and answer
        # too, which it hears on its channel as a page does (#201)
        view.wire._skip_render = False
        await hear(view, "presence_join", "bob", ref="bobs-tab")
        assert [u.user_id for u in view.component.presence_users] == ["ann", "bob"]
        assert not view.wire._skip_render


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
class TestPresenceTabs:
    """A meta is a browser tab's, across its reconnects (#202).

    The page names its load in the socket URL (``wire.tab_id``). Keyed by the
    connection, a reconnect made a new meta, and the old one stayed for good
    when the process that held it died without its leave.
    """

    @staticmethod
    def ref(
        tab: str = "", connection: str = "conn", user=None, id: str = "producer", key=None, id_given: bool = True
    ) -> str:
        """A producer's ref (or, with ``key``, another key of it); ``id_given=False`` is an id the framework minted."""
        from django.contrib.auth.models import AnonymousUser

        from wireview.core.meta import WireviewMeta

        wire = WireviewMeta(params={}, connection_id=connection, tab_id=tab)
        wire.id_given = id_given
        component = XProducerComponent(id=id, user=user or AnonymousUser(), wire=wire)
        return (key or presence._presence_ref)(component)

    @pytest.mark.unit
    def test_a_tab_keeps_its_ref_across_connections(self):
        tab = "abcdefghijklmnopqrstuv"
        assert self.ref(tab, "conn-1") == self.ref(tab, "conn-2")
        assert self.ref(tab, "conn-1") != self.ref("vutsrqponmlkjihgfedcba", "conn-1")
        assert tab not in self.ref(tab)

    @pytest.mark.unit
    def test_each_producer_of_a_tab_has_a_ref_of_its_own(self):
        """A meta per producer, as Phoenix keeps one per track: one leaving keeps the other's (E1)."""
        tab = "abcdefghijklmnopqrstuv"
        assert self.ref(tab, id="composer") != self.ref(tab, id="sidebar")
        assert self.ref(tab, "conn-1", id="composer") == self.ref(tab, "conn-2", id="composer")
        tab_key = presence._presence_tab
        assert self.ref(tab, id="composer", key=tab_key) == self.ref(tab, id="sidebar", key=tab_key)
        assert self.ref(tab, id="composer", key=tab_key) != self.ref(tab, id="composer")

    @pytest.mark.unit
    def test_a_producer_whose_id_was_minted_is_named_by_its_class(self):
        """An id-less ``{% component %}`` gets a new ``rx-`` id on every pass that draws it, a reconnect's too."""
        tab = "abcdefghijklmnopqrstuv"
        assert self.ref(tab, "conn-1", id="rx-1", id_given=False) == self.ref(tab, "conn-2", id="rx-2", id_given=False)
        assert self.ref(tab, id="rx-1", id_given=False) != self.ref(tab, id="rx-1")
        assert self.ref(tab, id="rx-1", id_given=False) != self.ref("vutsrqponmlkjihgfedcba", id="rx-1", id_given=False)

    @pytest.mark.unit
    def test_without_a_tab_the_connection_is_the_key(self):
        assert self.ref("", "conn-1") != self.ref("", "conn-2")
        assert self.ref("", "conn-1") == self.ref("", "conn-1")
        assert "conn-1" not in self.ref("", "conn-1")

    @pytest.mark.unit
    def test_another_users_page_naming_the_same_tab_gets_another_ref(self):
        """The name comes from the client: it counts only under the user the server knows."""
        from django.contrib.auth.models import User

        tab = "abcdefghijklmnopqrstuv"
        ann, mallory = User(pk=1, username="ann"), User(pk=2, username="mallory")
        assert self.ref(tab, user=ann) != self.ref(tab, user=mallory)
        assert self.ref(tab, user=ann) != self.ref(tab)
        assert self.ref(tab, "conn-1", user=ann) == self.ref(tab, "conn-2", user=ann)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_tab_joining_again_replaces_what_a_lost_leave_left(self):
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c1")
        # The process holding c1 died without the leave; the tab reconnects
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c2")
        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c2")
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_socket_a_tab_left_says_nothing_for_it(self):
        """Its late typing and leave come after the tab joined on a new socket."""
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c1")
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c2")

        await hear(view, "presence_typing", "ann", ref="ann-tab", presence_conn="c1", state="typing")
        assert view.component.presence_get_user("ann").state == PresenceState.ONLINE
        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c1")
        assert view.component.presence_get_user("ann") is not None

        await hear(view, "presence_typing", "ann", ref="ann-tab", presence_conn="c2", state="typing")
        assert view.component.presence_get_user("ann").is_typing()
        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c2")
        assert view.component.presence_get_user("ann") is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_tab_that_answers_on_two_sockets_stays_until_both_leave(self):
        """Both sockets of a reconnected tab answer a new tracker's sync; the old one's leave keeps the tab."""
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await hear(view, "presence_sync_response", "ann", ref="ann-tab", presence_conn="c-old")
        await hear(view, "presence_sync_response", "ann", ref="ann-tab", presence_conn="c-new")

        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c-old")
        assert view.component.presence_get_user("ann") is not None
        await hear(view, "presence_typing", "ann", ref="ann-tab", presence_conn="c-new", state="typing")
        assert view.component.presence_get_user("ann").is_typing()
        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c-new")
        assert view.component.presence_get_user("ann") is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_join_says_which_socket_the_tab_speaks_on_now(self):
        """Joined again after two sockets answered: the one it left says nothing for it."""
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await hear(view, "presence_sync_response", "ann", ref="ann-tab", presence_conn="c1")
        await hear(view, "presence_sync_response", "ann", ref="ann-tab", presence_conn="c2")
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c3")

        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c2")
        assert view.component.presence_get_user("ann") is not None
        await hear(view, "presence_leave", "ann", ref="ann-tab", presence_conn="c3")
        assert view.component.presence_get_user("ann") is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_message_that_names_no_connection_is_heard(self):
        """From a process before presence_conn: as before, by the ref alone."""
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await hear(view, "presence_join", "ann", ref="ann-tab", presence_conn="c1")
        await hear(view, "presence_leave", "ann", ref="ann-tab")
        assert view.component.presence_get_user("ann") is None

    @pytest.mark.unit
    def test_the_socket_url_names_the_tab(self):
        from wireview.core.rendered import socket_tab

        assert socket_tab(b"vsn=8&tab=abcdefghijklmnopqrstuv") == "abcdefghijklmnopqrstuv"
        assert socket_tab("tab=" + "a" * 64) == "a" * 64
        for query in (
            b"vsn=8",  # an older bundle
            b"tab=short",
            b"tab=" + b"a" * 65,
            b"tab=abcdefghijklmnop%2Fqrstuv",
            b"tab=abcdefghijklmnopqrstuv&tab=abcdefghijklmnopqrstuw",
            b"tab=%C3%A9bcdefghijklmnopqrstuv",
        ):
            assert socket_tab(query) == "", query


# A mounted component renders after each presence notification it hears, as a page does (#201)
@pytest.mark.django_db
class TestPresenceTopics:
    """A tracker on several topics keeps each one's metas apart (#202).

    Keyed by user and tab alone, leaving one room removed the user from the
    tracker though they were still in another.
    """

    @staticmethod
    async def hear_in(view, room: int, action: str, user_id: str = "ann") -> None:
        await view.component.notification(
            f"presence.room.{room}",
            action=action,
            user_id=user_id,
            presence_ref=f"{user_id}-tab",
            username=user_id,
            state="online",
            metadata={},
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_leaving_one_room_keeps_the_user_of_another(self):
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await self.hear_in(view, 1, "presence_join")
        await self.hear_in(view, 2, "presence_join")
        await self.hear_in(view, 2, "presence_leave")
        assert [u.user_id for u in view.component.presence_users] == ["ann"]
        await self.hear_in(view, 1, "presence_leave")
        assert view.component.presence_users == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_each_topic_lists_its_own_users(self):
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await view.component.presence_track_self(username="bob")
        await self.hear_in(view, 1, "presence_join")
        await self.hear_in(view, 2, "presence_join", "carol")
        await self.hear_in(view, 2, "presence_join")

        assert [u.user_id for u in view.component.presence_users_in("room.1")] == ["bob", "ann"]
        assert [u.user_id for u in view.component.presence_users_in("room.2")] == ["carol", "ann"]
        assert view.component.presence_users_in("room.3") == []
        # Across them, once each
        assert [u.user_id for u in view.component.presence_users] == ["bob", "ann", "carol"]
        assert view.component.presence_online_count == 3

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_user_merges_across_topics(self):
        view = await mount(XTrackerComponent, room_id=1, username="bob")
        await self.hear_in(view, 1, "presence_join")
        await view.component.notification(
            "presence.room.2", action="presence_typing", user_id="ann", presence_ref="ann-tab", state="typing"
        )
        assert view.component.presence_get_user("ann").is_typing()
        assert not view.component.presence_users_in("room.1")[0].is_typing()
