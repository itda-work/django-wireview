"""Test components for slots functionality."""

from wireview import Component


class Card(Component):
    """A card component with header, footer, and default slots."""

    class Meta:
        template_name = "slots/card.html"
        slots = {
            "header": {"required": False, "doc": "Card header content"},
            "footer": {"required": False, "doc": "Card footer content"},
        }

    title: str = ""
    variant: str = "default"


class List(Component):
    """A list component demonstrating slot let: binding."""

    class Meta:
        template_name = "slots/list.html"
        slots = {
            "item": {"required": False, "doc": "Template for each list item"},
        }

    items: list[dict] = []


class Modal(Component):
    """A modal component with required title slot."""

    class Meta:
        template_name = "slots/modal.html"
        slots = {
            "title": {"required": True, "doc": "Modal title - required"},
            "body": {"required": False, "doc": "Modal body content"},
            "actions": {"required": False, "doc": "Modal action buttons"},
        }

    is_open: bool = False


class Alert(Component):
    """A simple alert component with icon and message slots."""

    class Meta:
        template_name = "slots/alert.html"
        slots = {
            "icon": {"required": False, "doc": "Custom icon"},
        }

    message: str = ""
    variant: str = "info"  # info, success, warning, error


class SlotsGallery(Component):
    """The page that calls the four: what a caller writes into each one's slots.

    Its buttons sit outside every fill. An event goes to the component around
    the element it fires on, and inside a fill that is the slot's owner (a Card,
    a List), which has no handler of the caller's.
    """

    class Meta:
        template_name = "slots/gallery.html"

    books: list[dict] = [
        {"name": "Two Scoops of Django", "note": "설정과 구조"},
        {"name": "Fluent Python", "note": "언어 깊이 읽기"},
    ]
    alert_variant: str = "info"
    modal_open: bool = False

    async def add_book(self):
        extra = [
            {"name": "Architecture Patterns with Python", "note": "도메인 모델"},
            {"name": "High Performance Django", "note": "운영"},
            {"name": "Test-Driven Development with Python", "note": "테스트 먼저"},
        ]
        if len(self.books) < 2 + len(extra):
            self.books = [*self.books, extra[len(self.books) - 2]]

    async def cycle_alert(self):
        order = ["info", "success", "warning", "error"]
        self.alert_variant = order[(order.index(self.alert_variant) + 1) % len(order)]

    async def toggle_modal(self):
        self.modal_open = not self.modal_open
