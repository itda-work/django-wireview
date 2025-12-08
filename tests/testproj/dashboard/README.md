# Dashboard App Example

A complete wireview example demonstrating complex component composition, async loading states, and advanced patterns.

## Features Demonstrated

### AsyncResult for Loading States
- **assign_async()**: Deferred data loading with automatic state management
- **Loading/Success/Error**: Template patterns for each state
- **Skeleton Loading**: Visual feedback during data fetch

### Complex Component Composition
- **Nested Components**: Dashboard containing StatCards and ActivityFeed
- **Unique IDs**: Each nested component with distinct ID
- **Conditional Rendering**: Tab-based content switching

### Streams for Efficient Lists
- **stream()**: Initial list population
- **stream_insert()**: Prepend/append items
- **stream_delete()**: Remove items
- **Pagination**: Load more with loading states

### URL State Management
- **wire.params**: Access URL query parameters
- **Bookmarkable State**: Tab and filter state in URL

## Structure

```
dashboard/
├── live.py               # Component definitions
├── models.py             # Stat, Activity models
├── views.py              # View functions
├── urls.py               # URL routing
└── templates/
    ├── base.html         # Base layout with styles
    ├── index.html        # Dashboard wrapper
    └── dashboard/
        ├── dashboard.html          # XDashboard template
        ├── stat_card.html          # XStatCard template
        ├── activity_feed.html      # XActivityFeed template
        └── activity_feed_item.html # Stream item template
```

## Components

### XDashboard
Main container with tabs and nested components.

```python
class XDashboard(Component):
    _template_name = "dashboard/dashboard.html"

    active_tab: str = "overview"
    date_range: str = "7d"

    async def joined(self):
        # Initialize from URL params
        self.active_tab = self.wire.params.get("tab", "overview")

    async def change_tab(self, tab: str):
        self.active_tab = tab
        self.wire.params["tab"] = tab  # Update URL
```

### XStatCard
Individual stat with AsyncResult loading.

```python
class XStatCard(Component):
    _template_name = "dashboard/stat_card.html"

    stat_name: str
    data: AsyncResult[Stat] | None = None

    async def joined(self):
        # assign_async returns loading state immediately,
        # then updates when data loads
        self.data = await self.assign_async(self._load_stat())

    async def _load_stat(self) -> Stat:
        await asyncio.sleep(0.3)  # Simulate API call
        return await Stat.objects.aget(name=self.stat_name)
```

### XActivityFeed
Activity list with Streams and pagination.

```python
class XActivityFeed(Component):
    _template_name = "dashboard/activity_feed.html"

    activities: list[Activity] = []
    is_loading: bool = False
    has_more: bool = True

    async def joined(self):
        activities = list(await Activity.objects.all()[:10])
        await self.stream("activities", activities)

    async def load_more(self):
        self.is_loading = True
        await self.send_render()  # Show loading state

        offset = len(self.activities)
        older = list(await Activity.objects.all()[offset:offset + 10])

        for activity in older:
            await self.stream_insert("activities", activity, at=-1)

        self.is_loading = False
```

## Key Patterns

### AsyncResult Loading States
```python
# In component
self.data = await self.assign_async(self._load_stat())
```

```html
<!-- In template -->
{% if data.loading %}
  <div class="skeleton">Loading...</div>
{% elif data.ok %}
  <p>{{ data.result.value }}</p>
{% elif data.failed %}
  <p>Error: {{ data.error_message }}</p>
  <button {% on 'click' 'refresh' %}>Retry</button>
{% endif %}
```

### URL State Management
```python
# Read from URL params
self.active_tab = self.wire.params.get("tab", "default")

# Write to URL params
self.wire.params["tab"] = new_tab
```

### Nested Components with Unique IDs
```html
{% component 'XStatCard' id="stat-revenue" stat_name='revenue' %}
{% component 'XStatCard' id="stat-users" stat_name='users' %}
```

## Setup

Before running, create some sample data:

```python
# In Django shell
from testproj.dashboard.models import Stat, Activity

# Create stats
Stat.objects.create(name='revenue', label='Revenue', value=124500, change_percent=12.5)
Stat.objects.create(name='users', label='Active Users', value=8432, change_percent=5.2)
Stat.objects.create(name='orders', label='Orders', value=1893, change_percent=-2.1)
Stat.objects.create(name='conversion', label='Conversion', value=3.24, change_percent=0.8)

# Create activities
Activity.objects.create(type='purchase', user_name='John Doe', description='completed an order')
Activity.objects.create(type='login', user_name='Jane Smith', description='logged in')
```

## Running

```bash
cd tests
python manage.py migrate
python manage.py runserver
# Visit http://localhost:8000/dashboard/
```
