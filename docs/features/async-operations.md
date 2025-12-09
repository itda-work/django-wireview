# Async Operations

django-wireview provides async operation primitives for non-blocking UI updates.
This allows you to show loading states while fetching data, and handle
success/error results gracefully.

## Overview

| Method | Purpose |
|--------|---------|
| `assign_async()` | Simple async with automatic AsyncResult tracking |
| `start_async()` | Named async operation that can be cancelled/replaced |
| `cancel_async()` | Cancel an in-flight async operation |
| `handle_async()` | Callback for handling start_async results |

## assign_async()

The simplest way to perform async operations. Returns an `AsyncResult` that
tracks loading, success, and error states.

```python
from wireview import Component
from wireview.async_result import AsyncResult


class Dashboard(Component):
    _template_name = "dashboard.html"

    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # Returns AsyncResult in loading state immediately
        # Updates to success/error when complete
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        # Simulate slow operation
        import asyncio
        await asyncio.sleep(1)
        return {"users": 100, "revenue": 50000}
```

Template:

```html
{% load wireview %}

<div {% tag_header %}>
  {% if this.stats.loading %}
    <div class="skeleton">Loading stats...</div>
  {% elif this.stats.failed %}
    <div class="error">Error: {{ this.stats.error_message }}</div>
  {% elif this.stats.ok %}
    <div class="stats">
      <p>Users: {{ this.stats.result.users }}</p>
      <p>Revenue: ${{ this.stats.result.revenue }}</p>
    </div>
  {% endif %}
</div>
```

### AsyncResult Properties

| Property | Type | Description |
|----------|------|-------------|
| `loading` | bool | True if operation is in progress |
| `ok` | bool | True if completed successfully |
| `failed` | bool | True if completed with error |
| `done` | bool | True if completed (success or error) |
| `pending` | bool | True if not started |
| `result` | T | The result value (if ok) |
| `error` | Exception | The error (if failed) |
| `error_message` | str | Human-readable error message |

### AsyncResult Methods

```python
# Create states
AsyncResult.loading_state()  # Create loading state
AsyncResult.success(value)   # Create success state
AsyncResult.failure(error)   # Create error state

# Transform result
result.map(lambda x: x * 2)  # Transform if successful
result.get_or(default)       # Get result or default
result.get_or_raise()        # Get result or raise error
```

## start_async() / cancel_async()

For more control over async operations, use named tasks that can be
cancelled or replaced.

```python
class Search(Component):
    _template_name = "search.html"

    query: str = ""
    results: list[dict] = []
    loading: bool = False
    error: str | None = None

    async def search(self, query: str):
        self.query = query
        self.loading = True
        self.error = None

        # Start named async operation
        # If "search" is already running, it will be cancelled
        await self.start_async("search", self.do_search(query))

    async def do_search(self, query: str) -> list[dict]:
        import asyncio
        await asyncio.sleep(0.5)  # Debounce
        return await SearchService.search(query)

    async def handle_async(self, name: str, result):
        """Called when start_async completes."""
        if name == "search":
            self.loading = False
            if result[0] == "ok":
                self.results = result[1]
            else:
                self.error = str(result[1])
                self.results = []

    async def clear_search(self):
        # Cancel any in-flight search
        await self.cancel_async("search")
        self.query = ""
        self.results = []
        self.loading = False
```

### handle_async() Callback

The `handle_async` method is called when a `start_async` operation completes:

```python
async def handle_async(
    self,
    name: str,
    result: tuple[Literal["ok"], Any] | tuple[Literal["exit"], Exception],
) -> None:
    # name: The name you passed to start_async
    # result: ("ok", value) or ("exit", exception)

    if name == "load_data":
        status, value = result
        if status == "ok":
            self.data = value
        else:
            self.error = f"Failed to load: {value}"
```

### Named Tasks

Task names can be any string. Use descriptive names:

```python
# Good: descriptive names
await self.start_async("load_user_profile", self.fetch_profile(user_id))
await self.start_async("search_products", self.search(query))
await self.start_async(f"load_page_{page}", self.fetch_page(page))

# Check if can be cancelled
if await self.cancel_async("search_products"):
    print("Search was cancelled")
```

### Automatic Replacement

If you call `start_async` with the same name while an operation is running,
the previous operation is automatically cancelled:

```python
async def search(self, query: str):
    # Each keystroke starts a new search
    # Previous search is automatically cancelled
    await self.start_async("search", self.do_search(query))
```

## Use Cases

### Typeahead Search

```python
class Typeahead(Component):
    _template_name = "typeahead.html"

    query: str = ""
    suggestions: list[str] = []
    loading: bool = False

    async def on_input(self, query: str):
        self.query = query

        if len(query) < 2:
            self.suggestions = []
            await self.cancel_async("suggest")
            return

        self.loading = True
        await self.start_async("suggest", self.fetch_suggestions(query))

    async def fetch_suggestions(self, query: str) -> list[str]:
        import asyncio
        await asyncio.sleep(0.3)  # Natural debounce
        return await SuggestionService.get(query)

    async def handle_async(self, name, result):
        if name == "suggest":
            self.loading = False
            if result[0] == "ok":
                self.suggestions = result[1]
```

### Parallel Data Loading

```python
class Dashboard(Component):
    _template_name = "dashboard.html"

    users: AsyncResult[list] | None = None
    orders: AsyncResult[list] | None = None
    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # Load all data in parallel
        self.users = await self.assign_async(self.load_users())
        self.orders = await self.assign_async(self.load_orders())
        self.stats = await self.assign_async(self.load_stats())

    async def load_users(self):
        return await User.objects.all()[:10]

    async def load_orders(self):
        return await Order.objects.filter(status="pending")[:10]

    async def load_stats(self):
        return {"total_users": await User.objects.acount()}
```

### Cancellable File Processing

```python
class FileProcessor(Component):
    _template_name = "processor.html"

    progress: int = 0
    processing: bool = False
    result: str | None = None

    async def process_file(self, file_id: str):
        self.processing = True
        self.progress = 0
        await self.start_async("process", self.do_process(file_id))

    async def do_process(self, file_id: str):
        import asyncio
        for i in range(100):
            await asyncio.sleep(0.1)
            self.progress = i + 1
            await self.send_render()  # Update progress
        return "Processing complete!"

    async def cancel_processing(self):
        if await self.cancel_async("process"):
            self.processing = False
            self.progress = 0

    async def handle_async(self, name, result):
        if name == "process":
            self.processing = False
            if result[0] == "ok":
                self.result = result[1]
```

## Error Handling

### With assign_async

```python
async def joined(self):
    self.data = await self.assign_async(
        self.load_data(),
        on_error=lambda e: logger.error(f"Load failed: {e}")
    )
```

### With start_async

```python
async def handle_async(self, name, result):
    if name == "critical_task":
        if result[0] == "exit":
            error = result[1]
            logger.error(f"Critical task failed: {error}")
            # Optionally retry
            await self.start_async("critical_task", self.retry_task())
```

## Comparison with Phoenix LiveView

| Feature | Phoenix LiveView | django-wireview |
|---------|------------------|-----------------|
| Simple async | `assign_async/3` | `assign_async()` |
| Named async | `start_async/3` | `start_async()` |
| Cancel async | `cancel_async/3` | `cancel_async()` |
| Handle result | `handle_async/3` | `handle_async()` |
| Result wrapper | `AsyncResult` struct | `AsyncResult` dataclass |
| Auto-cancel | Same name replaces | Same name replaces |
