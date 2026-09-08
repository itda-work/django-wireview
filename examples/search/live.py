"""
Search App Components

This module demonstrates wireview's form and input handling patterns:
- .debounce.300 modifier for input events
- focus_on() for focus management
- push_js() with JS().set_value() to clear input
- Keyboard navigation (.keydown.key.ArrowDown/Up)
- Loading state management
"""

from django.db.models import Q

from wireview.component import Component
from wireview.js import JS

from .models import Book


class XLiveSearch(Component):
    """
    Live search with autocomplete dropdown.

    Demonstrates:
    - Input debouncing with .debounce modifier
    - Keyboard navigation through results
    - focus_on() and push_js() for UX polish
    - Loading state with .wireview-loading
    """

    _template_name = "search/live_search.html"

    query: str = ""
    results: list[Book] = []
    selected_index: int = -1  # Currently highlighted result (-1 = none)
    is_open: bool = False  # Dropdown visibility
    selected_book: Book | None = None  # Book selected by user

    async def search(self, q: str):
        """
        Search for books matching query.

        Uses debounce in template to avoid excessive queries.
        """
        self.query = q
        self.selected_index = -1

        if len(q) < 2:
            self.results = []
            self.is_open = False
            return

        # Search in title, author, and description
        self.results = [
            book
            async for book in Book.objects.filter(
                Q(title__icontains=q) | Q(author__icontains=q) | Q(description__icontains=q)
            )[:10]
        ]
        self.is_open = len(self.results) > 0

    async def navigate(self, direction: int):
        """
        Navigate through results with arrow keys.

        direction: 1 for down, -1 for up
        """
        if not self.results:
            self.skip_render()
            return

        # Calculate new index with wrapping
        max_index = len(self.results) - 1
        if self.selected_index == -1:
            self.selected_index = 0 if direction == 1 else max_index
        else:
            new_index = self.selected_index + direction
            if new_index < 0:
                self.selected_index = max_index
            elif new_index > max_index:
                self.selected_index = 0
            else:
                self.selected_index = new_index

    async def select_result(self, index: int):
        """
        Select a result by index (click or Enter).
        """
        if 0 <= index < len(self.results):
            self.selected_book = self.results[index]
            self.is_open = False
            self.query = self.selected_book.title

    async def select_current(self):
        """
        Select the currently highlighted result (Enter key).
        """
        if self.selected_index >= 0:
            await self.select_result(self.selected_index)
        elif len(self.results) == 1:
            # Auto-select if only one result
            await self.select_result(0)

    async def close_dropdown(self):
        """Close the dropdown (Escape key or blur)."""
        self.is_open = False
        self.selected_index = -1

    async def clear(self):
        """
        Clear the search.

        Demonstrates push_js() to clear input value.
        """
        self.query = ""
        self.results = []
        self.selected_index = -1
        self.is_open = False
        self.selected_book = None

        # Clear the input field and refocus
        await self.push_js(JS().set_value(f"#{self.id} input[name=q]", "").focus(f"#{self.id} input[name=q]"))

    async def view_details(self):
        """
        View selected book details.

        Demonstrates focus management after state change.
        """
        if self.selected_book:
            # In a real app, this might navigate to a detail page
            # Here we just focus back to the search input
            await self.focus_on(f"#{self.id} input[name=q]")
