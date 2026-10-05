"""Tests for async-native render optimization."""

import inspect
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


def _helper() -> str:
    return "called"


class EveryShape(Component):
    """Every kind of name a component can have, for the context's name cache (#176)."""

    class Meta:
        template_name = "todo/todo_list.html"

    LIMIT: t.ClassVar[int] = 3
    formatter: t.ClassVar[t.Any] = staticmethod(_helper)
    value: int = 1
    callback_name: str = "go"
    items: list[int] = []

    @property
    def doubled(self) -> int:
        return self.value * 2

    @property
    def action(self) -> t.Any:
        return _helper  # a property whose value is callable stays out

    @classmethod
    def build(cls) -> None:
        pass

    @staticmethod
    def pure() -> None:
        pass

    async def go(self) -> None:
        pass

    def _private(self) -> None:
        pass


def context_names_of(klass: type) -> set[str]:
    from wireview.core.meta import _CONTEXT_NAMES

    return {name for names in _CONTEXT_NAMES[klass][2].values() for name in names}


def _by_dir(wire: MockWireviewMeta, component: Component) -> dict[str, t.Any]:
    """The context as reading every public name of dir() gave it, before the names were kept (#176)."""
    names = [n for n in dir(component) if not n.startswith("_") and n not in wire._PYDANTIC_CLASS_ATTRS]
    return {n: v for n in names if not callable(v := getattr(component, n))}


def _same_as_dir(wire: MockWireviewMeta, component: Component) -> bool:
    context = wire._collect_context(component, MockRepository())
    expected = _by_dir(wire, component)
    return {k: v for k, v in context.items() if k in expected or k not in _ADDED} == expected


_ADDED = {"slots", "this", "wireview_repository"}


class _Shape(Component):
    value: int = 1

    async def act(self) -> None:
        pass

    def helper(self) -> int:
        return 1


def _make(klass: type[Component], **fields: t.Any) -> Component:
    from django.contrib.auth.models import AnonymousUser

    return klass(user=AnonymousUser(), wire=MockWireviewMeta(), **fields)


class TestContextNamesFollowTheClass:
    """The kept names give what dir() gives when the class changes after a render (#176)."""

    @pytest.mark.unit
    def test_a_property_attached_later(self):
        class LateShape(_Shape):
            pass

        component = _make(LateShape)
        assert _same_as_dir(component.wire, component)
        LateShape.late = property(lambda self: "late")  # type: ignore[attr-defined]

        assert _same_as_dir(component.wire, component)
        assert component.wire._collect_context(component, MockRepository())["late"] == "late"
        assert _same_as_dir(component.wire, _make(LateShape))

    @pytest.mark.unit
    def test_a_class_attribute_added_and_deleted_later(self):
        class AddedShape(_Shape):
            pass

        component = _make(AddedShape)
        assert _same_as_dir(component.wire, component)
        AddedShape.added = 5  # type: ignore[attr-defined]
        assert _same_as_dir(component.wire, component)
        del AddedShape.added  # type: ignore[attr-defined]

        assert _same_as_dir(component.wire, component)  # not an AttributeError

    @pytest.mark.unit
    def test_a_name_added_to_a_mixin_later(self):
        class Mixin:
            pass

        class MixedShape(Mixin, _Shape):
            pass

        component = _make(MixedShape)
        assert _same_as_dir(component.wire, component)
        Mixin.mixed_in = "m"  # type: ignore[attr-defined]

        assert _same_as_dir(component.wire, component)

    @pytest.mark.unit
    def test_a_dir_that_follows_the_state_is_read_every_time(self):
        class DirShape(_Shape):
            mode: int = 0

            def __dir__(self) -> list[str]:
                return [*super().__dir__(), *(["ghost"] if self.mode else [])]

            def __getattr__(self, name: str) -> t.Any:
                return "boo" if name == "ghost" else super().__getattr__(name)

        component = _make(DirShape)
        assert _same_as_dir(component.wire, component)
        component.mode = 1

        assert _same_as_dir(component.wire, component)
        assert component.wire._collect_context(component, MockRepository())["ghost"] == "boo"

    @pytest.mark.unit
    def test_what_the_instance_changes(self):
        class SwapA(_Shape):
            a_only: t.ClassVar[str] = "a"

        class SwapB(_Shape):
            b_only: t.ClassVar[str] = "b"

        component = _make(SwapA)
        assert _same_as_dir(component.wire, component)
        object.__setattr__(component, "helper", 99)  # hides the method
        assert _same_as_dir(component.wire, component)
        object.__setattr__(component, "__class__", SwapB)
        assert _same_as_dir(component.wire, component)

    @pytest.mark.unit
    def test_a_property_that_raises_attribute_error_still_raises(self):
        class RaisingShape(_Shape):
            @property
            def broken(self) -> int:
                raise AttributeError("broken")

        component = _make(RaisingShape)
        with pytest.raises(AttributeError):
            component.wire._collect_context(component, MockRepository())

    @pytest.mark.unit
    def test_a_method_rebound_in_place_to_a_value_is_not_seen(self):
        """The one change the names do not follow, as _context_names() says: the counts stay the same."""

        class ReboundShape(_Shape):
            pass

        ReboundShape.helper = _Shape.helper  # type: ignore[method-assign]
        component = _make(ReboundShape)
        assert _same_as_dir(component.wire, component)
        ReboundShape.helper = 7  # type: ignore[assignment,method-assign]

        assert "helper" in _by_dir(component.wire, component)
        assert "helper" not in component.wire._collect_context(component, MockRepository())


class TestCollectContext:
    """The live render's context: read by _collect_context(), off the event loop in
    render_diff(), with async properties awaited back on the loop."""

    @pytest.mark.unit
    def test_the_names_it_reads_are_dir_s_but_the_methods(self):
        """Kept per class, the names give the context reading every public name of dir() gave (#176)."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        repo = MockRepository()

        def by_dir(component):
            names = [n for n in dir(component) if not n.startswith("_") and n not in wire._PYDANTIC_CLASS_ATTRS]
            return {n: v for n in names if not callable(v := getattr(component, n))}

        for component in (
            EveryShape(user=AnonymousUser(), wire=wire, value=4),
            EveryShape(user=AnonymousUser(), wire=wire, value=5),  # from the cache
            SimpleComponent(user=AnonymousUser(), wire=wire, value=42),
            ComponentWithAsyncProperty(user=AnonymousUser(), wire=wire, base_value=1),
        ):
            context = wire._collect_context(component, repo)
            expected = by_dir(component)
            for coroutine in [v for v in [*context.values(), *expected.values()] if inspect.iscoroutine(v)]:
                coroutine.close()
            assert list(context)[: len(expected)] == list(expected)
            assert {k: v for k, v in context.items() if k in expected and not inspect.iscoroutine(v)} == {
                k: v for k, v in expected.items() if not inspect.iscoroutine(v)
            }
        assert context_names_of(EveryShape) >= {"LIMIT", "value", "callback_name", "items", "doubled", "action"}
        assert not context_names_of(EveryShape) & {"build", "pure", "go", "formatter"}

    @pytest.mark.unit
    def test_an_instance_name_hides_the_method_it_shadows(self):
        """A name the instance holds itself is read, even where the class has a method of that name."""
        from django.contrib.auth.models import AnonymousUser

        wire = MockWireviewMeta()
        component = EveryShape(user=AnonymousUser(), wire=wire)
        object.__setattr__(component, "go", "shadowed")

        assert wire._collect_context(component, MockRepository())["go"] == "shadowed"

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
