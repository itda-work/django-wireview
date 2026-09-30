"""Tests for async-native render optimization."""

import typing as t

import pytest
from django.template import Template

from wireview import Component
from wireview.testing import MockRepository, MockWireviewMeta, mount


class InlineTemplate:
    source: t.ClassVar[str]
    _compiled: t.ClassVar[Template | None] = None

    @classmethod
    def _get_template(cls, template_name=None):
        if cls.__dict__.get("_compiled") is None:
            cls._compiled = Template("{% load wireview %}" + cls.source)
        return cls._compiled


# The render path crosses channels' ``database_sync_to_async``: see the note in
# tests/test_diff_stability.py for why that needs the database marker here.
pytestmark = pytest.mark.django_db


class SimpleComponent(Component):
    """Simple component for testing."""

    class Meta:
        template_name = "todo/todo_list.html"  # Use existing template

    value: int = 0


class ComponentWithAsyncProperty(Component):
    """Component with async property for testing."""

    class Meta:
        template_name = "todo/todo_list.html"  # Use existing template

    base_value: int = 0

    @property
    async def computed_value(self) -> int:
        """Async property that computes a value."""
        return self.base_value * 2


class ComponentWithQueryingProperty(InlineTemplate, Component):
    """A plain property that uses the ORM, next to an async one."""

    source: t.ClassVar[str] = "<div {% tag_header %}>{{ usernames|join:',' }} <b>{{ computed_value }}</b></div>"

    base_value: int = 0

    @property
    def usernames(self) -> list[str]:
        from django.contrib.auth.models import User

        return list(User.objects.values_list("username", flat=True))

    @property
    async def computed_value(self) -> int:
        return self.base_value * 2


class TestCollectContext:
    """The live render's context: read by _collect_context(), off the event loop in
    render_diff(), with async properties awaited back on the loop."""

    @pytest.mark.unit
    def test_basic_context_building(self):
        """Test basic context building with sync attributes."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = wire._collect_context(component, repo)

        assert "value" in context
        assert context["value"] == 42
        assert context["this"] is component
        assert context["wireview_repository"] is repo

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_async_property_resolution(self):
        """An async property is left as a coroutine, so nothing is rendered off the
        loop until the caller awaits it."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = ComponentWithAsyncProperty(user=AnonymousUser(), wire=wire, base_value=21)

        context, html, pending = wire._collect_and_render(component, repo)
        assert pending and html is None

        await wire._await_properties(context)
        assert context["computed_value"] == 42  # 21 * 2

    @pytest.mark.unit
    def test_excludes_private_attributes(self):
        """Test that private attributes are excluded from context."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = wire._collect_context(component, repo)

        # Private attributes should be excluded
        assert "_name" not in context
        assert "_template_name" not in context

    @pytest.mark.unit
    def test_excludes_pydantic_class_attrs(self):
        """Test that Pydantic v2 class attributes are excluded."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = wire._collect_context(component, repo)

        # Pydantic v2 class-level attributes should be excluded
        assert "model_fields" not in context
        assert "model_config" not in context

    @pytest.mark.unit
    def test_excludes_callables(self):
        """Test that callable methods are excluded from context."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = wire._collect_context(component, repo)

        # Methods should be excluded
        assert "joined" not in context
        assert "leaving" not in context


class TestRenderWithContext:
    """Tests for _render_with_context method."""

    @pytest.mark.unit
    def test_render_with_frozen_meta(self):
        """Test that rendering returns None when meta is frozen."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        wire.freeze()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = {"value": 42, "this": component, "wireview_repository": repo}
        result = wire._render_with_context(component, context)

        assert result is None

    @pytest.mark.unit
    def test_render_with_redirect(self):
        """Test that rendering handles redirect case."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        wire._redirected_to = "/redirect-url/"
        # Without channel_name, it should return meta refresh
        wire.channel_name = None
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        context = {"value": 42, "this": component, "wireview_repository": repo}
        result = wire._render_with_context(component, context)

        assert result is not None
        assert 'meta http-equiv="refresh"' in str(result)
        assert "/redirect-url/" in str(result)


class TestRenderDiffOptimization:
    """Tests for render_diff optimization."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.django_db(transaction=True)  # the async ORM writes on another thread's connection
    async def test_a_property_that_queries_renders_live(self):
        """Properties are read off the event loop, where the ORM may run (#120).

        Read on the loop, the first live render of a component with such a
        property raised SynchronousOnlyOperation on a real server.
        """
        from django.contrib.auth.models import User

        await User.objects.acreate(username="alice")
        view = await mount(ComponentWithQueryingProperty, base_value=21)

        diff = await view.render_diff()

        assert diff is not None
        assert "alice" in str(diff)
        assert "42" in str(diff)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_render_diff_skips_when_flagged(self):
        """Test that render_diff respects skip_render flag."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = SimpleComponent(user=AnonymousUser(), wire=wire, value=42)

        wire.skip_render()
        diff = await wire.render_diff(component, repo)

        assert diff is None


class TestSyncContextWarning:
    """Tests for sync context warning in _get_context."""

    @pytest.mark.unit
    def test_sync_get_context_warns_on_async_property(self, caplog):
        """Test that _get_context logs warning for async properties."""
        import logging

        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()
        component = ComponentWithAsyncProperty(user=AnonymousUser(), wire=wire, base_value=21)

        with caplog.at_level(logging.WARNING, logger="wireview"):
            context = wire._get_context(component, repo)

        assert context["computed_value"] == 42
        assert "Sync context detected while resolving async property 'computed_value'" in caplog.text
