"""Test components for slots functionality."""

from wireview.component import Component


class Card(Component):
    """A card component with header, footer, and default slots."""

    _template_name = "slots/card.html"
    _slots = {
        "header": {"required": False, "doc": "Card header content"},
        "footer": {"required": False, "doc": "Card footer content"},
    }

    title: str = ""
    variant: str = "default"


class List(Component):
    """A list component demonstrating slot let: binding."""

    _template_name = "slots/list.html"
    _slots = {
        "item": {"required": False, "doc": "Template for each list item"},
    }

    items: list[dict] = []


class Modal(Component):
    """A modal component with required title slot."""

    _template_name = "slots/modal.html"
    _slots = {
        "title": {"required": True, "doc": "Modal title - required"},
        "body": {"required": False, "doc": "Modal body content"},
        "actions": {"required": False, "doc": "Modal action buttons"},
    }

    is_open: bool = False


class Alert(Component):
    """A simple alert component with icon and message slots."""

    _template_name = "slots/alert.html"
    _slots = {
        "icon": {"required": False, "doc": "Custom icon"},
    }

    message: str = ""
    variant: str = "info"  # info, success, warning, error
