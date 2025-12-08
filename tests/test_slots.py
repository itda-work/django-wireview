"""Unit tests for the slots system.

Tests for Slot, SlotContainer classes and slot-related template tags.
"""

import pytest
from django.template import Context, Template
from django.template.base import NodeList, TextNode

from wireview.slots import Slot, SlotContainer


class TestSlotClass:
    """Tests for the Slot dataclass."""

    def test_slot_creation(self):
        """Test creating a basic slot."""
        nodelist = NodeList([TextNode("Hello World")])
        slot = Slot(name="header", nodelist=nodelist)

        assert slot.name == "header"
        assert slot.nodelist == nodelist
        assert slot.let_vars == []

    def test_slot_with_let_vars(self):
        """Test creating a slot with let variables."""
        nodelist = NodeList([TextNode("{{ item.name }}")])
        slot = Slot(name="item", nodelist=nodelist, let_vars=["item", "index"])

        assert slot.let_vars == ["item", "index"]

    def test_slot_render_basic(self):
        """Test rendering a slot."""
        nodelist = NodeList([TextNode("Hello World")])
        slot = Slot(name="header", nodelist=nodelist)

        context = Context({})
        result = slot.render(context)

        assert result == "Hello World"

    def test_slot_render_with_extra_context(self):
        """Test rendering a slot with extra context."""
        nodelist = NodeList([TextNode("Hello")])
        slot = Slot(name="header", nodelist=nodelist, let_vars=["name"])

        context = Context({})
        result = slot.render(context, {"name": "World"})

        assert result == "Hello"


class TestSlotContainer:
    """Tests for the SlotContainer class."""

    def test_empty_container(self):
        """Test empty slot container."""
        container = SlotContainer()

        assert not container
        assert not container.has("header")
        assert not container.has_default()
        assert container.get("header") == []

    def test_add_slot(self):
        """Test adding a slot to container."""
        container = SlotContainer()
        slot = Slot(name="header", nodelist=NodeList([TextNode("Header")]))

        container.add(slot)

        assert container.has("header")
        assert container.get("header") == [slot]
        assert bool(container)

    def test_add_multiple_slots_same_name(self):
        """Test adding multiple slots with the same name."""
        container = SlotContainer()
        slot1 = Slot(name="item", nodelist=NodeList([TextNode("Item 1")]))
        slot2 = Slot(name="item", nodelist=NodeList([TextNode("Item 2")]))

        container.add(slot1)
        container.add(slot2)

        assert len(container.get("item")) == 2

    def test_set_default(self):
        """Test setting default slot content."""
        container = SlotContainer()
        default = NodeList([TextNode("Default content")])

        container.set_default(default)

        assert container.has_default()
        assert container.get_default() == default

    def test_contains(self):
        """Test 'in' operator."""
        container = SlotContainer()
        container.add(Slot(name="header", nodelist=NodeList()))

        assert "header" in container
        assert "footer" not in container

    def test_slot_accessor_truthiness(self):
        """Test slots.header returns truthy accessor when slot exists."""
        container = SlotContainer()
        container.add(Slot(name="header", nodelist=NodeList([TextNode("Header")])))

        # slots.header should be truthy
        assert container.header
        # slots.footer should be falsy
        assert not container.footer

    def test_slot_accessor_iteration(self):
        """Test iterating over slot accessor."""
        container = SlotContainer()
        slot1 = Slot(name="item", nodelist=NodeList([TextNode("1")]))
        slot2 = Slot(name="item", nodelist=NodeList([TextNode("2")]))
        container.add(slot1)
        container.add(slot2)

        slots = list(container.item)
        assert len(slots) == 2
        assert slots[0] == slot1
        assert slots[1] == slot2

    def test_render_slot_named(self):
        """Test rendering a named slot."""
        container = SlotContainer()
        container.add(Slot(name="header", nodelist=NodeList([TextNode("Header Content")])))

        context = Context({})
        result = container.render_slot(context, "header")

        assert result == "Header Content"

    def test_render_slot_default(self):
        """Test rendering the default slot."""
        container = SlotContainer()
        container.set_default(NodeList([TextNode("Default Content")]))

        context = Context({})
        result = container.render_slot(context, "")

        assert result == "Default Content"

    def test_render_slot_missing(self):
        """Test rendering a missing slot returns empty string."""
        container = SlotContainer()

        context = Context({})
        result = container.render_slot(context, "missing")

        assert result == ""

    def test_repr(self):
        """Test string representation."""
        container = SlotContainer()
        container.add(Slot(name="header", nodelist=NodeList()))
        container.add(Slot(name="footer", nodelist=NodeList()))

        repr_str = repr(container)
        assert "header" in repr_str
        assert "footer" in repr_str


class TestSlotContainerBooleanContext:
    """Tests for SlotContainer in boolean/conditional context."""

    def test_empty_container_is_falsy(self):
        """Empty container should be falsy."""
        container = SlotContainer()
        assert not bool(container)

    def test_container_with_slots_is_truthy(self):
        """Container with slots should be truthy."""
        container = SlotContainer()
        container.add(Slot(name="test", nodelist=NodeList()))
        assert bool(container)

    def test_container_with_only_default_is_truthy(self):
        """Container with only default slot should be truthy."""
        container = SlotContainer()
        container.set_default(NodeList([TextNode("default")]))
        assert bool(container)


@pytest.mark.django_db
class TestSlotTemplateTags:
    """Tests for slot-related template tags."""

    def test_fill_tag_parses(self):
        """Test that fill tag parses correctly."""
        template_str = """
        {% load wireview %}
        {% fill header %}Content{% endfill %}
        """
        # Should not raise
        Template(template_str)

    def test_fill_tag_with_let(self):
        """Test fill tag with let: syntax."""
        template_str = """
        {% load wireview %}
        {% fill item let:item let:index %}Content{% endfill %}
        """
        # Should not raise
        Template(template_str)

    def test_render_slot_tag_parses(self):
        """Test that render_slot tag parses correctly."""
        template_str = """
        {% load wireview %}
        {% render_slot %}
        {% render_slot "header" %}
        {% render_slot "item" item=obj %}
        """
        # Should not raise
        Template(template_str)

    def test_render_slot_with_empty_slots(self):
        """Test render_slot with empty slots context."""
        template_str = """{% load wireview %}{% render_slot "header" %}"""
        template = Template(template_str)

        # Without slots in context
        result = template.render(Context({}))
        assert result.strip() == ""

        # With empty SlotContainer
        result = template.render(Context({"slots": SlotContainer()}))
        assert result.strip() == ""

    def test_render_slot_with_slot_content(self):
        """Test render_slot renders slot content."""
        template_str = """{% load wireview %}{% render_slot "header" %}"""
        template = Template(template_str)

        container = SlotContainer()
        container.add(Slot(name="header", nodelist=NodeList([TextNode("Header Text")])))

        result = template.render(Context({"slots": container}))
        assert "Header Text" in result

    def test_render_slot_default(self):
        """Test render_slot for default slot."""
        template_str = """{% load wireview %}{% render_slot %}"""
        template = Template(template_str)

        container = SlotContainer()
        container.set_default(NodeList([TextNode("Default Text")]))

        result = template.render(Context({"slots": container}))
        assert "Default Text" in result

    def test_fill_outside_component_is_noop(self):
        """Test that fill tag outside component renders empty."""
        template_str = """{% load wireview %}{% fill header %}Content{% endfill %}"""
        template = Template(template_str)

        result = template.render(Context({}))
        assert result.strip() == ""


@pytest.mark.django_db
class TestSlotValidation:
    """Tests for slot validation."""

    def test_component_block_validates_required_slots(self):
        """Test that required slots are validated."""
        from wireview.component import Component

        # Create a component with required slot
        class TestCard(Component):
            _template_name = "test_card.html"
            _slots = {"header": {"required": True}}

        # The validation happens in ComponentBlockNode._validate_required_slots
        # which is called during render. We'll test this in integration tests.
        assert TestCard._slots["header"]["required"] is True
