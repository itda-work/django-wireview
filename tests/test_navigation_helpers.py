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

from wireview import Component, WireviewDeprecationWarning
from wireview.features.streams import StreamItem, StreamOp
from wireview.testing import ComponentTestCase, mount

pytestmark = pytest.mark.unit


MEMBERS = "ls-members"
STAFF = "ls-staff"


class Navigator(Component):
    """Navigates on demand, and records what it was told about the URL."""

    class Meta:
        template_name = "todo/counter.html"

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

    class Meta:
        template_name = "todo/counter.html"

    count: int = 0


class MembersOnly(Component):
    """Refuses to exist outside ``ls-members``. Proves the boundary really carried."""

    class Meta:
        template_name = "todo/counter.html"
        live_sessions = {MEMBERS}

    count: int = 0


class Streamer(Component):
    """Sends stream operations without needing an item template.

    The helpers under test read the messages; rendering items is
    ``tests/test_streams.py``'s subject, not this one's.
    """

    class Meta:
        template_name = "todo/counter.html"

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


class TestMountingWithParams:
    """A page load with a query: the join runs params_changed after joined()
    (WireviewSession.command_join), and so does mount() (#169)."""

    @pytest.mark.asyncio
    async def test_params_reach_params_changed(self):
        view = await mount(Navigator, params={"page": "2"})
        assert view.component.seen_params == {"page": "2"}
        assert view.component.seen_uri == "?page=2"

    @pytest.mark.asyncio
    async def test_no_params_no_call(self):
        """As the join: an empty query sends nothing."""
        view = await mount(Navigator)
        assert view.component.seen_uri == ""

    @pytest.mark.asyncio
    async def test_a_followed_redirect_hears_the_destinations_query(self):
        view = await mount(Navigator)
        await view.call("go_redirect", url="/livesession/public/?tab=open")

        landed = await view.follow_redirect(Navigator)
        assert landed.component.seen_params == {"tab": "open"}


class Crumb(Component):
    """A component with a field called ``path``, which 1.1's ``mount(path=)`` set."""

    class Meta:
        template_name = "todo/counter.html"

    count: int = 0
    path: str = "/"


class TestMountingOnAPath:
    """mount(path=) names the page; a field of that name is set through state= (#169)."""

    @pytest.mark.asyncio
    async def test_a_field_called_path_makes_path_ambiguous(self):
        with pytest.raises(TypeError) as caught:
            await mount(Crumb, path="/a/b/")
        assert "state={'path': ...}" in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_field_goes_through_state(self):
        view = await mount(Crumb, state={"path": "/a/b/"})
        assert view.component.path == "/a/b/"

    @pytest.mark.asyncio
    async def test_with_the_field_in_state_path_is_the_page(self):
        view = await mount(Crumb, path="/livesession/public/", state={"path": "/a/b/"})
        assert view.component.path == "/a/b/"
        assert view._path == "/livesession/public/"

    @pytest.mark.asyncio
    async def test_the_mixin_refuses_it_too(self):
        with pytest.raises(TypeError):
            await ComponentTestCase().mount(Crumb, path="/a/b/")


class TestFollowingAPush:
    """The other half of a push, as the browser does it (#169): on the page's own
    path a patch -- the same instance hears params_changed -- and on another path
    a new page, whose components mount fresh."""

    @pytest.mark.asyncio
    async def test_it_runs_params_changed(self):
        view = await mount(Navigator)
        await view.call("go_push", url="?page=2")

        nav = await view.follow_push()
        assert nav.command == "push"
        assert view.component.seen_params == {"page": "2"}
        assert view.component.seen_uri == "?page=2"

    @pytest.mark.asyncio
    async def test_a_patch_keeps_what_events_changed(self):
        """The browser keeps the instance; so does the helper."""
        view = await mount(Navigator)
        await view.call("go_push", url="?page=2")
        view.component.count = 3

        await view.follow_push()
        assert view.component.count == 3

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
    async def test_a_push_to_the_pages_own_path_is_a_patch(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_push", url="/livesession/members/?tab=open")

        await view.follow_push()
        assert view.component.seen_params == {"tab": "open"}

    @pytest.mark.asyncio
    async def test_a_path_without_the_page_is_followed_as_1_1_did_with_a_warning(self):
        """Patch or new page depends on where the component is. Without mount(path=) the
        helper cannot tell, so it keeps 1.1's answer -- this instance hears the params --
        and warns that 2.0 fails here (#169)."""
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_push", url="/livesession/members/?tab=open")
        view.component.count = 3

        with pytest.warns(WireviewDeprecationWarning, match=r"path=") as caught:
            nav = await view.follow_push()
        assert "2.0" in str(caught[0].message)
        assert nav.url == "/livesession/members/?tab=open"
        assert view.component.seen_params == {"tab": "open"}
        assert view.component.seen_uri == "/livesession/members/?tab=open"
        assert view.component.wire.params == {"tab": "open"}
        assert view.component.count == 3, "the same instance, as in 1.1"

    @pytest.mark.asyncio
    async def test_a_replace_to_a_path_without_the_page_is_followed_as_1_1_did(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_replace", url="/livesession/members2/?tab=open")

        with pytest.warns(WireviewDeprecationWarning):
            await view.follow_push()
        assert view.component.seen_params == {"tab": "open"}

    @pytest.mark.asyncio
    async def test_a_push_out_of_the_boundary_without_the_page_still_fails_as_in_1_1(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_push", url="/livesession/public/")

        with pytest.warns(WireviewDeprecationWarning), pytest.raises(AssertionError) as caught:
            await view.follow_push()
        assert "full page load" in str(caught.value)
        assert view.component.seen_params == {}

    @pytest.mark.asyncio
    async def test_an_unrouted_push_without_the_page_still_fails_as_in_1_1(self):
        view = await mount(Navigator)
        await view.call("go_push", url="/nowhere-at-all/")

        with pytest.warns(WireviewDeprecationWarning), pytest.raises(AssertionError) as caught:
            await view.follow_push()
        assert "URLconf" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_destination_without_the_page_asks_for_it(self):
        """follow_push(Destination) is 1.2's: the test that uses it says where the page is."""
        view = await mount(Navigator, user=member(), live_session=MEMBERS)
        await view.call("go_push", url="/livesession/members2/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_push(Navigator)
        assert "path=" in str(caught.value)
        assert view.component.seen_params == {}

    @pytest.mark.asyncio
    async def test_a_query_only_push_needs_no_page_and_does_not_warn(self, recwarn):
        view = await mount(Navigator)
        await view.call("go_push", url="?page=2")

        await view.follow_push()
        assert not [w for w in recwarn if issubclass(w.category, WireviewDeprecationWarning)]

    @pytest.mark.asyncio
    async def test_another_path_inside_the_boundary_mounts_the_destination(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_push", url="/livesession/members2/?tab=open")
        view.component.count = 3

        landed = await view.follow_push(Navigator)
        assert landed is not view
        assert landed.component.count == 0, "the page was fetched: event-only state is gone"
        assert landed.component.wire.params == {"tab": "open"}
        assert landed.component.wire.live_session.name == MEMBERS
        assert view.component.seen_params == {}, "the old instance never hears the new params"

    @pytest.mark.asyncio
    async def test_the_destination_lands_on_its_own_path(self):
        """Its next push is judged from the page it is on now."""
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_push", url="/livesession/members2/")
        landed = await view.follow_push(Navigator)

        await landed.call("go_push", url="/livesession/members2/?tab=b")
        await landed.follow_push()
        assert landed.component.seen_params == {"tab": "b"}

    @pytest.mark.asyncio
    async def test_a_replace_to_another_path_mounts_the_destination(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_replace", url="/livesession/members2/")

        landed = await view.follow_push(Destination)
        assert isinstance(landed.component, Destination)

    @pytest.mark.asyncio
    async def test_another_path_without_the_destination_says_what_to_do(self):
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_push", url="/livesession/members2/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_push()
        assert "follow_push(Destination)" in str(caught.value)
        assert view.component.seen_params == {}

    @pytest.mark.asyncio
    async def test_a_patch_with_a_destination_is_refused(self):
        """Asking for a new page where the browser keeps the old one would hide the difference."""
        view = await mount(Navigator)
        await view.call("go_push", url="?page=2")

        with pytest.raises(AssertionError) as caught:
            await view.follow_push(Navigator)
        assert "params_changed" in str(caught.value)

    @pytest.mark.asyncio
    async def test_leaving_the_boundary_mounts_outside_it(self):
        """A full page load (#58): the old instance hears nothing, the destination mounts without a boundary."""
        view = await mount(Navigator, user=member(), live_session=MEMBERS, path="/livesession/members/")
        await view.call("go_push", url="/livesession/public/")

        landed = await view.follow_push(Destination)
        assert landed.component.wire.live_session is None
        assert view.component.seen_params == {}

    @pytest.mark.asyncio
    async def test_entering_a_boundary_that_refuses_the_user_is_refused(self):
        view = await mount(Navigator, user=member(), path="/livesession/public/")
        await view.call("go_push", url="/livesession/staff/")

        with pytest.raises(AssertionError) as caught:
            await view.follow_push(Destination)
        assert STAFF in str(caught.value)

    @pytest.mark.asyncio
    async def test_entering_a_boundary_the_user_passes_mounts_inside_it(self):
        view = await mount(Navigator, user=member(), path="/livesession/public/")
        await view.call("go_push", url="/livesession/members/")

        landed = await view.follow_push(MembersOnly)
        assert landed.component.wire.live_session.name == MEMBERS

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


@pytest.mark.parametrize("name", ["redirect_to", "push_to", "replace_to"])
def test_the_destination_is_positional_only_so_every_keyword_reaches_reverse(name):
    """A URL argument called ``to`` collided with the destination (#119)."""
    import inspect

    from wireview import WireviewMeta

    to = inspect.signature(getattr(WireviewMeta, name)).parameters["to"]
    assert to.kind is inspect.Parameter.POSITIONAL_ONLY
