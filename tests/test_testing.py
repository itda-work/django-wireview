"""Tests for the testing utilities module."""

import pytest

from wireview import Component
from wireview.testing import ComponentTestCase, MountedComponent, mount


class SimpleCounter(Component):
    """A simple counter component for testing."""

    class Meta:
        template_name = "todo/counter.html"  # Use existing template

    count: int = 0

    async def increment(self, amount: int = 1):
        self.count += amount

    async def decrement(self):
        self.count -= 1

    async def reset(self):
        self.count = 0


class RedirectComponent(Component):
    """Component that tests redirect functionality."""

    class Meta:
        template_name = "todo/counter.html"

    async def do_redirect(self, url: str = "/home"):
        await self.wire.redirect_to(url)

    async def do_replace(self, url: str = "/dashboard"):
        await self.wire.replace_to(url)


class TestMount:
    """Test the mount() function."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_creates_component(self):
        """mount() should create a component instance."""
        view = await mount(SimpleCounter)
        assert view.component is not None
        assert isinstance(view.component, SimpleCounter)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_with_initial_state(self):
        """mount() should accept initial state."""
        view = await mount(SimpleCounter, count=10)
        assert view.component.count == 10

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_returns_mounted_component(self):
        """mount() should return a MountedComponent wrapper."""
        view = await mount(SimpleCounter)
        assert isinstance(view, MountedComponent)


class TestMountedComponent:
    """Test the MountedComponent class."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_handler(self):
        """call() should invoke the handler and update state."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment")
        assert view.component.count == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_handler_with_args(self):
        """call() should pass arguments to the handler."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment", amount=5)
        assert view.component.count == 5

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_multiple_handlers(self):
        """Multiple handler calls should accumulate changes."""
        view = await mount(SimpleCounter, count=0)
        await view.call("increment", amount=3)
        await view.call("increment", amount=2)
        await view.call("decrement")
        assert view.component.count == 4

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_call_nonexistent_handler(self):
        """call() should raise AttributeError for missing handlers."""
        view = await mount(SimpleCounter)
        with pytest.raises(AttributeError, match="has no handler"):
            await view.call("nonexistent")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_sent_messages(self):
        """sent_messages should track messages sent to client."""
        view = await mount(RedirectComponent)
        await view.call("do_redirect", url="/test")
        assert len(view.sent_messages) > 0
        redirect_msg = view.sent_messages[-1]
        assert redirect_msg["type"] == "url_change"
        assert redirect_msg["command"] == "redirect"
        assert redirect_msg["url"] == "/test"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_redirected_to(self):
        """redirected_to should track redirect URL."""
        view = await mount(RedirectComponent)
        assert view.redirected_to is None
        await view.call("do_redirect", url="/home")
        assert view.redirected_to == "/home"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_is_frozen_after_redirect(self):
        """Component should be frozen after redirect."""
        view = await mount(RedirectComponent)
        assert not view.is_frozen
        await view.call("do_redirect")
        assert view.is_frozen

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_clear_messages(self):
        """clear_messages() should empty the message list."""
        view = await mount(RedirectComponent)
        await view.call("do_replace")
        assert len(view.sent_messages) > 0
        view.clear_messages()
        assert len(view.sent_messages) == 0


class TestComponentTestCase(ComponentTestCase):
    """Test the ComponentTestCase mixin."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mount_via_mixin(self):
        """ComponentTestCase.mount() should work like standalone mount()."""
        view = await self.mount(SimpleCounter, count=5)
        assert view.component.count == 5
        await view.call("increment")
        assert view.component.count == 6

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_components(self):
        """Should be able to mount multiple components in one test."""
        view1 = await self.mount(SimpleCounter, count=10)
        view2 = await self.mount(SimpleCounter, count=20)

        await view1.call("increment")
        await view2.call("decrement")

        assert view1.component.count == 11
        assert view2.component.count == 19


class TestCallMeetsTheEventChecks:
    """``call()`` refuses what a browser's event cannot reach, and drops extra arguments (#110)."""

    class CallProbe(Component):
        count: int = 0

        async def add(self, by: int = 1):
            self.count += by

        async def _secret(self):
            self.count = -1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_a_private_or_framework_method_is_refused(self):
        view = await mount(self.CallProbe)
        for name in ("_secret", "joined", "model_dump"):
            with pytest.raises(AssertionError, match="not an event handler"):
                await view.call(name)
        assert view.component.count == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_arguments_the_handler_does_not_take_are_dropped(self):
        # A form's other fields ride along with every event
        view = await mount(self.CallProbe)
        await view.call("add", by=2, title="unrelated")
        assert view.component.count == 2


class TestBroadcastsAreOnTheMountedComponent:
    """The testing surface is MountedComponent's; ``view.wire`` is not public beyond navigation (#114)."""

    class Shouter(Component):
        async def shout(self):
            await self.broadcast("room", text="hi")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_view_broadcasts_records_what_the_component_broadcast(self):
        view = await mount(self.Shouter)
        await view.call("shout")
        assert [(b["channel"], b["kwargs"]) for b in view.broadcasts] == [("room", {"text": "hi"})]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_the_old_path_still_works_and_warns(self):
        from wireview import WireviewDeprecationWarning

        view = await mount(self.Shouter)
        await view.call("shout")
        with pytest.warns(WireviewDeprecationWarning, match=r"view\.broadcasts"):
            assert view.wire.broadcasts == view.broadcasts
        with pytest.warns(WireviewDeprecationWarning, match=r"view\.presence_broadcasts"):
            assert view.wire.presence_broadcasts == []


class QuietCounter(SimpleCounter):
    async def quietly(self):
        self.count += 1
        self.skip_render()

    async def nothing(self):
        pass

    @classmethod
    def _get_template(cls, template_name=None):
        from django.template import Template

        return Template("{% load wireview %}<p {% tag_header %}><b>{{ count }}</b></p>")


@pytest.mark.django_db
class TestRenderDiff:
    """``render_diff()``: what the next live render sends the client (#117).

    It renders the way the consumer does, through ``database_sync_to_async``,
    so pytest-django wants the database mark.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_first_is_the_full_render(self):
        view = await mount(QuietCounter)

        diff = await view.render_diff()

        assert diff is not None and "s" in diff

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_change_goes_out_and_only_the_change(self):
        view = await mount(QuietCounter)
        await view.render_diff()

        await view.call("increment")
        diff = await view.render_diff()

        assert diff is not None and "s" not in diff
        assert "1" in diff.values()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_skipped_render_sends_nothing_and_the_next_catches_up(self):
        view = await mount(QuietCounter)
        await view.render_diff()

        await view.call("quietly")
        assert await view.render_diff() is None

        await view.call("increment")
        assert "2" in (await view.render_diff() or {}).values()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_handler_that_changes_nothing_sends_nothing(self):
        view = await mount(QuietCounter)
        await view.render_diff()

        await view.call("nothing")

        assert await view.render_diff() is None
