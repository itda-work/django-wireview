"""Tests for the params_changed lifecycle callback."""

from urllib.parse import parse_qsl

import pytest

from wireview import Component
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.repository import ComponentRepository
from wireview.testing import mount


def make_consumer() -> WireviewConsumer:
    """A consumer with just enough state for ``command_params_changed``."""
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    return consumer


class RecordingMeta(WireviewMeta):
    """A real ``WireviewMeta`` whose ``send`` is captured rather than transported.

    The mock in ``wireview.testing`` overrides the three navigation methods, so a
    test that went through it would not see this module's own behaviour.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent: list[tuple[str, str]] = []

    async def send(self, _command: str, **kwargs) -> None:
        self.sent.append((kwargs["command"], kwargs["url"]))


class ParamsSimpleComponent(Component):
    """A simple component without params_changed override."""

    _template_name = "todo/counter.html"
    count: int = 0


class ParamsAwareComponent(Component):
    """A component that tracks params_changed calls."""

    _template_name = "todo/counter.html"

    page: int = 1
    sort: str = "created_at"
    params_changed_called: bool = False
    received_params: dict = {}
    received_uri: str = ""

    async def params_changed(self, params: dict[str, str], uri: str) -> None:
        """Track params_changed calls for testing."""
        self.params_changed_called = True
        self.received_params = params
        self.received_uri = uri
        self.page = int(params.get("page", "1"))
        self.sort = params.get("sort", "created_at")


class TestParamsChangedMethod:
    """Test the params_changed lifecycle method."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_exists_on_base_component(self):
        """params_changed should exist on base Component class."""
        view = await mount(ParamsSimpleComponent)
        assert hasattr(view.component, "params_changed")
        assert callable(view.component.params_changed)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_is_noop_by_default(self):
        """params_changed should be a no-op by default."""
        view = await mount(ParamsSimpleComponent)
        # Should not raise any exceptions
        await view.component.params_changed({"page": "2"}, "/test?page=2")
        # Component state should remain unchanged
        assert view.component.count == 0

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_receives_correct_params(self):
        """params_changed should receive correct params dict."""
        view = await mount(ParamsAwareComponent)
        params = {"page": "3", "sort": "name"}
        uri = "/products?page=3&sort=name"

        await view.component.params_changed(params, uri)

        assert view.component.params_changed_called is True
        assert view.component.received_params == params
        assert view.component.received_uri == uri

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_updates_component_state(self):
        """params_changed can update component state based on params."""
        view = await mount(ParamsAwareComponent)
        assert view.component.page == 1
        assert view.component.sort == "created_at"

        await view.component.params_changed({"page": "5", "sort": "price"}, "?page=5&sort=price")

        assert view.component.page == 5
        assert view.component.sort == "price"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_with_empty_params(self):
        """params_changed should handle empty params."""
        view = await mount(ParamsAwareComponent)
        await view.component.params_changed({}, "/products")

        assert view.component.params_changed_called is True
        assert view.component.received_params == {}
        assert view.component.page == 1  # default value


class TestParamsChangedWithWire:
    """Test params_changed interaction with wire."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_initial_params_accessible(self):
        """Initial params should be accessible via wire.params."""
        view = await mount(ParamsAwareComponent, params={"page": "2"})
        assert view.component.wire.params == {"page": "2"}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_push_to_sends_url_change(self):
        """push_to should send url_change message."""
        view = await mount(ParamsAwareComponent)
        await view.component.wire.push_to("/products?page=3")

        # Check that url_change message was sent
        url_changes = [m for m in view.sent_messages if m.get("type") == "url_change"]
        assert len(url_changes) == 1
        assert url_changes[0]["command"] == "push"
        assert "page=3" in url_changes[0]["url"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_replace_to_sends_url_change(self):
        """replace_to should send url_change message."""
        view = await mount(ParamsAwareComponent)
        await view.component.wire.replace_to("/products?sort=price")

        # Check that url_change message was sent
        url_changes = [m for m in view.sent_messages if m.get("type") == "url_change"]
        assert len(url_changes) == 1
        assert url_changes[0]["command"] == "replace"
        assert "sort=price" in url_changes[0]["url"]


class TestAQueryOnlyDestination:
    """``push_to("?page=2")`` is the documented way to say "this page, new query".

    ``resolve_url`` reverses any string with no ``/`` and no ``.`` in it, so
    before ``resolve_destination`` these three raised ``NoReverseMatch`` -- while
    ``params_changed``'s own docstring taught the call.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_push_keeps_it_literal(self):
        wire = RecordingMeta(params={}, channel_name="c")
        await wire.push_to("?page=2")

        assert wire.sent == [("push", "?page=2")]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_replace_keeps_it_literal(self):
        wire = RecordingMeta(params={}, channel_name="c")
        await wire.replace_to("?tab=open")

        assert wire.sent == [("replace", "?tab=open")]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_redirect_keeps_it_literal(self):
        wire = RecordingMeta(params={}, channel_name="c")
        await wire.redirect_to("#section")

        assert wire.sent == [("redirect", "#section")]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_view_name_still_reverses(self):
        """The control: the exception is for ``?`` and ``#``, not for every string."""
        wire = RecordingMeta(params={}, channel_name="c")
        await wire.push_to("livesession:public")

        assert wire.sent == [("push", "/livesession/public/")]


class TestJsonParamsSurviveANavigation:
    """A ``.json`` key decodes on the first load; it has to decode after a push too.

    The client hands back plain strings from the address bar, and
    ``get_query_string`` re-encodes with the ``.json`` rule, so without decoding
    on the way in the same key is a dict on load and a string afterwards.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_the_repository_decodes_what_the_client_sends(self):
        consumer = make_consumer()

        await consumer.command_params_changed({"filter.json": '{"tag": "x"}'}, "?filter.json=...")

        assert consumer.repo.params == {"filter.json": {"tag": "x"}}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_it_matches_what_the_first_load_produced(self):
        consumer = make_consumer()
        qs = 'filter.json={"tag": "x"}&page=2'

        await consumer.command_params_changed(dict(parse_qsl(qs)), f"?{qs}")

        assert consumer.repo.params == ComponentRepository.extract_params(qs)

    @pytest.mark.unit
    def test_a_plain_key_is_left_alone(self):
        assert ComponentRepository.decode_params({"page": "2"}) == {"page": "2"}


class TestParamsChangedSignature:
    """Test the params_changed method signature."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_is_dict_of_strings(self):
        """params should be a dict with string keys and values."""
        view = await mount(ParamsAwareComponent)

        params = {"page": "2", "filter": "active", "sort": "name"}
        await view.component.params_changed(params, "/test")

        assert view.component.received_params == params
        # Verify all values are strings
        for key, value in view.component.received_params.items():
            assert isinstance(key, str)
            assert isinstance(value, str)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_uri_includes_query_string(self):
        """uri should include the full query string."""
        view = await mount(ParamsAwareComponent)

        uri = "/products?page=2&sort=name&filter=active"
        await view.component.params_changed({}, uri)

        assert view.component.received_uri == uri
        assert "?" in view.component.received_uri
