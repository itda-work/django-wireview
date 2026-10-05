"""
Search App Components

This module demonstrates wireview's form and input handling patterns:
- .debounce.300 modifier for input events
- The query kept in the address (push_to, params_changed)
- focus_on() for focus management
- push_js() with JS().set_value() to clear input
- Keyboard navigation (.keydown.key.ArrowDown/Up)
- Loading state management
"""

from urllib.parse import urlencode

from django.db.models import Q

from wireview import JS, Component

from .models import Book


class XLiveSearch(Component):
    """
    Live search with autocomplete dropdown.

    Demonstrates:
    - Input debouncing with .debounce modifier
    - The query in the address: a reload, a shared link and Back show its results
    - Keyboard navigation through results
    - focus_on() and push_js() for UX polish
    - Loading state with .wireview-loading
    """

    class Meta:
        template_name = "search/live_search.html"

    query: str = ""
    results: list[Book] = []  # signed as their pks, read again on a rejoin
    selected_index: int = -1  # Currently highlighted result (-1 = none)
    is_open: bool = False  # Dropdown visibility
    selected_book: Book | None = None  # Book selected by user

    async def search(self, q: str):
        """
        Put the query in the address; params_changed() runs the search.

        Same path, so push_to is a patch: nothing is fetched, this instance
        stays, and every query the user paused on is a history entry Back
        returns to. The handler does not search itself -- a reload, a shared
        link and Back reach the results only through params_changed(), so the
        typed query takes the same way.
        """
        q = q.strip()
        if q:
            await self.wire.push_to(f"?{urlencode({'q': q})}")
        else:
            # The page's own path: no "?q=" left in the address
            await self.wire.push_to("search:index")

    async def params_changed(self, params, uri):
        """
        Show the results for the address's query.

        Runs when the page loads with a query (a reload, a shared link) --
        on the HTTP render, so the first response already shows the results,
        and again on the join, after joined() -- on a patch from search() or
        clear(), and on Back/Forward. Twice for one load, so it only derives
        the results from the query.
        """
        q = params.get("q", "")
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
        self.selected_book = None
        # params_changed() empties the query and the results
        await self.wire.push_to("search:index")

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
