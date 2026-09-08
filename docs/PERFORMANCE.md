# Performance Guide

Optimize django-wireview for maximum performance.

## Understanding Async/Sync Transitions

### The Problem

Django Channels operates in an async context, but Django templates and ORM are synchronous. This requires context transitions:

```
ASYNC (Consumer)
  → sync_to_async (template render)
    → SYNC (Django template)
```

When nested transitions occur, performance degrades:

```
ASYNC (Consumer)
  → sync_to_async
    → SYNC (render)
      → async_to_sync (async property)  # Extra overhead!
        → ASYNC (property coroutine)
```

Each `async_to_sync` call creates a new event loop, adding ~0.5-2ms overhead.

### The Solution (v0.x.x+)

django-wireview now resolves async properties **before** entering sync context:

```
ASYNC (Consumer)
  → await async properties  # Direct await, no overhead
  → sync_to_async (template render)
    → SYNC (Django template)  # Context already resolved
```

## Detecting Performance Issues

### Enable Transition Tracking

During development, enable detection:

```python
# settings.py
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": True,
    "SYNC_TRANSITION_WARNING_THRESHOLD": 2,  # Warn at depth > 2
    "SYNC_TRANSITION_ERROR_THRESHOLD": 3,    # Error at depth > 3
}
```

This logs warnings when nested transitions are detected:

```
WARNING wireview.sync_detector: Nested sync context detected (total depth=3)
at _run_coro. This may cause performance degradation.
```

### Disable in Production

```python
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": False,  # Zero overhead when disabled
}
```

## Best Practices

### 1. Pre-load Data in `joined()`

Instead of async properties, load data in the `joined()` lifecycle method:

```python
# Bad: Async property accessed during render
class UserProfile(Component):
    @property
    async def recent_posts(self):
        return await Post.objects.filter(user=self.user)[:5]

# Good: Pre-load in joined()
class UserProfile(Component):
    posts: list[Post] = []

    async def joined(self):
        self.posts = await sync_to_async(list)(
            Post.objects.filter(user=self.user)[:5]
        )
```

### 2. Use `asend_to()` in Components

Prefer async functions when in async context:

```python
# Bad: Uses async_to_sync internally
from wireview.utils import send_notification

class ChatRoom(Component):
    async def send_message(self, text: str):
        # Creates nested transition if called from async
        send_notification("chat_room_1", message=text)

# Good: Pure async, no transitions
from wireview.utils import asend_notification

class ChatRoom(Component):
    async def send_message(self, text: str):
        await asend_notification("chat_room_1", message=text)
```

### 3. Batch Database Queries

Reduce sync_to_async calls by batching:

```python
# Bad: Multiple sync_to_async calls
async def joined(self):
    self.user = await sync_to_async(User.objects.get)(pk=self.user_id)
    self.posts = await sync_to_async(list)(self.user.posts.all())
    self.comments = await sync_to_async(list)(self.user.comments.all())

# Good: Single sync_to_async call with prefetch
async def joined(self):
    @sync_to_async
    def load_user_data():
        user = User.objects.prefetch_related('posts', 'comments').get(pk=self.user_id)
        return user, list(user.posts.all()), list(user.comments.all())

    self.user, self.posts, self.comments = await load_user_data()
```

### 4. Use Streams for Large Lists

Streams avoid re-rendering entire lists:

```python
class ItemList(Component):
    items: list[Item] = []

    async def add_item(self, name: str):
        item = await Item.objects.acreate(name=name)
        # Only sends the new item, not the entire list
        await self.stream_insert("items", item, at=0)
```

### 5. Skip Unnecessary Renders

```python
class Counter(Component):
    count: int = 0

    async def increment_silent(self):
        self.count += 1
        # Skip render if UI doesn't need update
        self.wire.skip_render()
```

## Performance Tuning

### HTML Diff Settings

> Partial diffs depend on the marker comments emitted by `render_with_markers()`.
> `USE_HMIN` strips HTML comments, which silently disables partial diffs.
> See [features/html-diff.md](./features/html-diff.md) for the diff format and measured payloads.

```python
WIREVIEW = {
    "USE_HTML_DIFF": True,   # Send only changes, not full HTML
    "USE_HMIN": True,        # Minify HTML (requires django-hmin)
}
```

### Template Optimization

1. **Use template fragments**: Smaller templates = faster renders
2. **Avoid complex template logic**: Move to Python code
3. **Cache template compilation**: Django does this by default

### Database Optimization

1. **Use `select_related()` and `prefetch_related()`**
2. **Add database indexes for filtered fields**
3. **Use connection pooling** (`CONN_MAX_AGE`)

### Channel Layer Optimization

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
            "capacity": 1500,      # Max messages in memory
            "expiry": 10,          # Message expiry in seconds
        },
    },
}
```

## Profiling

### Django Debug Toolbar

Install and configure for development:

```python
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
```

### Custom Profiling

```python
import time
import logging

log = logging.getLogger("wireview.profiling")

class ProfiledComponent(Component):
    async def render_diff(self, *args, **kwargs):
        start = time.perf_counter()
        result = await super().render_diff(*args, **kwargs)
        duration = time.perf_counter() - start
        if duration > 0.1:  # Log slow renders
            log.warning(f"Slow render: {self._name} took {duration:.3f}s")
        return result
```

### Async Profiling with py-spy

```bash
py-spy record -o profile.svg --pid <PID>
```

## Performance Benchmarks

> Measure instead of guessing: `make bench` runs the in-process and WebSocket benchmarks in `bench/`,
> and `make bench-compare BASE=<ref>` benchmarks a past commit next to the current tree.
> See [bench/README.md](../bench/README.md).

### Expected Performance

| Operation | Target | Notes |
|-----------|--------|-------|
| WebSocket connect | < 50ms | Initial connection |
| Component join | < 100ms | Including joined() |
| Event handler | < 50ms | User interaction |
| Render diff | < 20ms | HTML generation |
| Channel broadcast | < 10ms | Redis pub/sub |

### Load Testing

Use `locust` for WebSocket load testing:

```python
from locust import HttpUser, task
from locust_plugins.users import SocketIOUser

class WireviewUser(SocketIOUser):
    @task
    def join_component(self):
        self.send('{"command": "join", "name": "Counter"}')
```

## Troubleshooting

### Slow Initial Load

1. Check `joined()` for slow database queries
2. Profile with Django Debug Toolbar
3. Consider lazy loading large datasets

### High Memory Usage

1. Use streams instead of storing large lists
2. Check for circular references
3. Monitor component instance count

### Frequent Disconnections

1. Check WebSocket proxy timeout settings
2. Verify Redis connection stability
3. Monitor server resources

### Async/Sync Warnings

If you see "Nested sync context detected":

1. Check for async properties in templates
2. Use `joined()` for data loading
3. Review async function call chains

See [Deployment Guide](DEPLOYMENT.md) for production configuration.
