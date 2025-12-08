"""Tests for the params_changed lifecycle callback."""

import pytest

from wireview import Component
from wireview.testing import mount


class SimpleComponent(Component):
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
        view = await mount(SimpleComponent)
        assert hasattr(view.component, "params_changed")
        assert callable(view.component.params_changed)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_params_changed_is_noop_by_default(self):
        """params_changed should be a no-op by default."""
        view = await mount(SimpleComponent)
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
