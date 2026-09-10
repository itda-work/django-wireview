"""Navigation and stream assertions in ``wireview.testing`` (GAP-031, #70).

What these defend is narrower than "the helpers work": a test helper that gives a
*different* answer than the server is worse than no helper, because it turns a
green suite into evidence for something that is not true. So every case here is
paired -- a refusal next to the admission on the same path, a boundary crossing
next to the move that stays inside one -- and the destination's boundary is read
from the URLconf rather than inherited, because that is what the server does.

The fixture pages are ``testproj.livesession``: ``/livesession/public/`` declares
no boundary, ``/livesession/members/`` and ``/livesession/members2/`` are inside
``ls-members`` (any logged-in user), ``/livesession/staff/`` is inside
``ls-staff`` (staff only).
"""

import pytest
from django.contrib.auth.models import AnonymousUser, User

from wireview import Component
from wireview.features.streams import StreamItem, StreamOp
from wireview.testing import ComponentTestCase, mount

pytestmark = pytest.mark.unit


MEMBERS = "ls-members"
STAFF = "ls-staff"


class Navigator(Component):
    """Navigates on demand, and records what it was told about the URL."""

    _template_name = "todo/counter.html"

    count: int = 0
    seen_params: dict = {}
    seen_uri: str = ""

    async def go_push(self, url: str = "?page=2"):
        await self.wire.push_to(url)

    async def go_replace(self, url: str = "?tab=open"):
        await self.wire.replace_to(url)

    async def go_redirect(self, url: str = "/livesession/public/"):
        await self.wire.redirect_to(url)

    async def params_changed(self, params, uri):
        self.seen_params = dict(params)
        self.seen_uri = uri


class Destination(Component):
    """What a redirect lands on."""

    _template_name = "todo/counter.html"

    count: int = 0


class MembersOnly(Component):
    """Refuses to exist outside ``ls-members``. Proves the boundary really carried."""

    _template_name = "todo/counter.html"
    _live_sessions = {MEMBERS}

    count: int = 0


class Streamer(Component):
    """Sends stream operations without needing an item template.

    The helpers under test read the messages; rendering items is
    ``tests/test_streams.py``'s subject, not this one's.
    """

    _template_name = "todo/counter.html"

    count: int = 0

    async def emit(self):
        await self.wire.send_stream_op(
            StreamOp(op="reset", stream="posts", items=[StreamItem("posts-1", "<li>first</li>")])
        )
        await self.wire.send_stream_op(
            StreamOp(op="insert", stream="posts", items=[StreamItem("posts-2", "<li>second</li>")], at=0)
        )
        await self.wire.send_stream_op(
            StreamOp(op="reset", stream="notes", items=[StreamItem("notes-1", "<li>note</li>")])
        )


def member() -> User:
    """A logged-in user. Unsaved on purpose: no predicate here touches the ORM."""
    return User(username="member")


def staffer() -> User:
    return User(username="boss", is_staff=True)


class TestAssertingWhereItNavigated:
    """AC1: push, replace and redirect each get an assertion, URL and params included."""

    @pytest.mark.asyncio
    async def test_it_matches_the_path(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/")

        assert view.assert_pushed_to("/items/").url == "/items/"

    @pytest.mark.asyncio
    async def test_it_matches_query_params_given_in_the_url(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/?page=2&sort=name")

        view.assert_pushed_to("/items/?sort=name&page=2")

    @pytest.mark.asyncio
    async def test_it_matches_query_params_given_separately(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/?page=2")

        view.assert_pushed_to("/items/", params={"page": "2"})

    @pytest.mark.asyncio
    async def test_params_are_compared_whole(self):
        """A subset must not pass: a stray param is exactly what a test should catch."""
        view = await mount(Navigator)
        await view.call("go_push", url="/items/?page=2&sort=name")

        with pytest.raises(AssertionError):
            view.assert_pushed_to("/items/", params={"page": "2"})

    @pytest.mark.asyncio
    async def test_it_does_not_confuse_the_three_commands(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/")

        view.assert_pushed_to("/items/")
        with pytest.raises(AssertionError):
            view.assert_replaced_to("/items/")
        with pytest.raises(AssertionError):
            view.assert_redirected_to("/items/")

    @pytest.mark.asyncio
    async def test_replace_and_redirect_have_their_own(self):
        view = await mount(Navigator)
        await view.call("go_replace", url="/a/")
        await view.call("go_redirect", url="/b/")

        view.assert_replaced_to("/a/")
        view.assert_redirected_to("/b/")

    @pytest.mark.asyncio
    async def test_a_miss_reports_what_did_happen(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/")

        with pytest.raises(AssertionError) as caught:
            view.assert_pushed_to("/other/")
        assert "/items/" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_miss_with_no_navigation_at_all_says_so(self):
        view = await mount(Navigator)

        with pytest.raises(AssertionError) as caught:
            view.assert_pushed_to("/items/")
        assert "not at all" in str(caught.value)

    @pytest.mark.asyncio
    async def test_json_params_read_the_way_the_component_will(self):
        """``extract_params``' ``.json`` rule, not ``parse_qs``' plain strings."""
        view = await mount(Navigator)
        await view.call("go_push", url='/items/?filter.json={"tag": "x"}')

        assert view.assert_pushed_to("/items/").params == {"filter.json": {"tag": "x"}}


class TestAssertingItStayedPut:
    """The control the positive assertions need: "it navigated" and "it navigated here" look alike."""

    @pytest.mark.asyncio
    async def test_it_passes_when_nothing_navigated(self):
        view = await mount(Navigator)
        await view.call("increment") if hasattr(Navigator, "increment") else None

        view.assert_no_navigation()

    @pytest.mark.asyncio
    async def test_it_fails_and_names_the_navigation(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/")

        with pytest.raises(AssertionError) as caught:
            view.assert_no_navigation()
        assert "/items/" in str(caught.value)

    @pytest.mark.asyncio
    async def test_it_narrows_to_one_command(self):
        view = await mount(Navigator)
        await view.call("go_replace", url="/a/")

        view.assert_no_navigation("push")
        with pytest.raises(AssertionError):
            view.assert_no_navigation("replace")


class TestFollowingARedirect:
    """AC2: follow the redirect and mount the destination -- under the destination's rules."""

    @pytest.mark.asyncio
    async def test_it_mounts_the_destination(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/livesession/public/")

        landed = await view.follow_redirect(Destination, count=7)
        assert isinstance(landed.component, Destination)
        assert landed.component.count == 7

    @pytest.mark.asyncio
    async def test_it_carries_the_destinations_query_as_params(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/livesession/public/?tab=open")

        landed = await view.follow_redirect(Destination)
        assert landed.component.wire.params == {"tab": "open"}

    @pytest.mark.asyncio
    async def test_params_can_be_overridden(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/livesession/public/?tab=open")

        landed = await view.follow_redirect(Destination, params={"tab": "closed"})
        assert landed.component.wire.params == {"tab": "closed"}

    @pytest.mark.asyncio
    async def test_it_carries_the_user(self):
        user = member()
        view = await mount(Navigator, user=user)
        await view.call("go_redirect", url="/livesession/public/")

        landed = await view.follow_redirect(Destination)
        assert landed.component.user is user

    @pytest.mark.asyncio
    async def test_it_takes_the_boundary_from_the_urlconf(self):
        """Not inherited from here: a redirect is how a page changes boundary."""
        view = await mount(Navigator, user=member())
        await view.call("go_redirect", url="/livesession/members/")

        landed = await view.follow_redirect(MembersOnly)
        assert landed.component.wire.live_session is not None
        assert landed.component.wire.live_session.name == MEMBERS
        # ``_live_sessions`` would have frozen the mount under any other boundary.
        assert landed.is_frozen is False

    @pytest.mark.asyncio
    async def test_leaving_a_boundary_drops_it(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_redirect", url="/livesession/public/")

        landed = await view.follow_redirect(Destination)
        assert landed.component.wire.live_session is None

    @pytest.mark.asyncio
    async def test_a_refusing_destination_refuses_here_too(self):
        """The server answers with a login redirect or a 403 and renders nothing."""
        view = await mount(Navigator, user=AnonymousUser())
        await view.call("go_redirect", url="/livesession/members/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_redirect(Destination)
        assert MEMBERS in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_same_destination_admits_the_user_it_should(self):
        """Paired with the refusal above: "always refuses" would pass that one alone."""
        view = await mount(Navigator, user=member())
        await view.call("go_redirect", url="/livesession/members/")

        landed = await view.follow_redirect(Destination)
        assert landed.component.wire.live_session.name == MEMBERS

    @pytest.mark.asyncio
    async def test_a_second_boundary_decides_separately(self):
        """A logged-in non-staff user passes ``ls-members`` and fails ``ls-staff``."""
        view = await mount(Navigator, user=member())
        await view.call("go_redirect", url="/livesession/staff/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_redirect(Destination)
        assert STAFF in str(caught.value)

        staff_view = await mount(Navigator, user=staffer())
        await staff_view.call("go_redirect", url="/livesession/staff/")
        landed = await staff_view.follow_redirect(Destination)
        assert landed.component.wire.live_session.name == STAFF

    @pytest.mark.asyncio
    async def test_an_unrouted_destination_says_what_to_do(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/nothing/serves/this/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_redirect(Destination)
        assert "live_session=" in str(caught.value)

    @pytest.mark.asyncio
    async def test_an_unrouted_destination_can_be_followed_explicitly(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/nothing/serves/this/")

        landed = await view.follow_redirect(Destination, live_session=None)
        assert isinstance(landed.component, Destination)

    @pytest.mark.asyncio
    async def test_it_refuses_when_nothing_redirected(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/items/")

        with pytest.raises(AssertionError):
            await view.follow_redirect(Destination)


class TestFollowingAPush:
    """The other half of a push: the client tells the server the new params."""

    @pytest.mark.asyncio
    async def test_it_runs_params_changed(self):
        view = await mount(Navigator)
        await view.call("go_push", url="?page=2")

        nav = await view.follow_push()
        assert nav.command == "push"
        assert view.component.seen_params == {"page": "2"}
        assert view.component.seen_uri == "?page=2"

    @pytest.mark.asyncio
    async def test_it_updates_the_params_the_component_renders_from(self):
        """The repository and the wire share one dict, as they do on a connection."""
        view = await mount(Navigator, params={"page": "1"})
        await view.call("go_push", url="?page=2")

        await view.follow_push()
        assert view.component.wire.params == {"page": "2"}

    @pytest.mark.asyncio
    async def test_a_replace_is_followed_too(self):
        view = await mount(Navigator)
        await view.call("go_replace", url="?tab=open")

        nav = await view.follow_push()
        assert nav.command == "replace"
        assert view.component.seen_params == {"tab": "open"}

    @pytest.mark.asyncio
    async def test_a_move_inside_the_boundary_is_followed(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_push", url="/livesession/members2/?tab=open")

        await view.follow_push()
        assert view.component.seen_params == {"tab": "open"}

    @pytest.mark.asyncio
    async def test_leaving_the_boundary_is_not_a_push_to_follow(self):
        """It becomes a full page load, and params_changed is never sent (#58)."""
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_push", url="/livesession/public/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_push()
        assert "full page load" in str(caught.value)
        assert view.component.seen_params == {}

    @pytest.mark.asyncio
    async def test_entering_a_boundary_is_not_one_either(self):
        view = await mount(Navigator, user=member())
        await view.call("go_push", url="/livesession/members/")

        with pytest.raises(AssertionError):
            await view.follow_push()

    @pytest.mark.asyncio
    async def test_it_refuses_when_nothing_pushed(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/livesession/public/")

        with pytest.raises(AssertionError):
            await view.follow_push()


class TestStreamHelpers:
    """AC3: the recipe every project was copying out of the docs."""

    @pytest.mark.asyncio
    async def test_it_collects_the_html(self):
        view = await mount(Streamer)
        await view.call("emit")

        assert view.stream_html("posts") == "<li>first</li><li>second</li>"

    @pytest.mark.asyncio
    async def test_it_narrows_to_one_stream(self):
        view = await mount(Streamer)
        await view.call("emit")

        assert view.stream_html("notes") == "<li>note</li>"
        assert "note" not in view.stream_html("posts")

    @pytest.mark.asyncio
    async def test_without_a_name_it_takes_every_stream(self):
        view = await mount(Streamer)
        await view.call("emit")

        assert len(view.stream_items()) == 3

    @pytest.mark.asyncio
    async def test_it_exposes_the_operations(self):
        view = await mount(Streamer)
        await view.call("emit")

        assert [op["op"] for op in view.stream_ops("posts")] == ["reset", "insert"]
        assert view.stream_ops("posts")[1]["at"] == 0

    @pytest.mark.asyncio
    async def test_clear_messages_resets_it(self):
        view = await mount(Streamer)
        await view.call("emit")
        view.clear_messages()

        assert view.stream_html() == ""


class TestTheMixinMountsTheSameWay:
    """``ComponentTestCase.mount`` is documented as the module-level one. It has to be."""

    class Case(ComponentTestCase):
        pass

    @pytest.mark.asyncio
    async def test_it_forwards_the_boundary(self):
        view = await self.Case().mount(MembersOnly, user=member(), live_session=MEMBERS)

        assert view.is_frozen is False
        assert view.component.wire.live_session.name == MEMBERS

    @pytest.mark.asyncio
    async def test_and_still_refuses_without_one(self):
        view = await self.Case().mount(MembersOnly, user=member())

        assert view.is_frozen is True
