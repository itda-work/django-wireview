# Wireview - Phoenix LiveView for Django

Wireview enables you to build real-time, server-rendered interactive UIs using Django Channels, similar to Phoenix Framework's LiveView.

![TODO MVC demo app](demo.gif)

## What's in the box?

This is no replacement for VueJS or ReactJS, but it allows you to leverage all the potential of Django to create interactive front-ends. Everything is server-side rendered, so the interface comes with meaningful information in the first request. You can use all the power of Django templates and ORM directly in your components and update the interface in real-time by subscribing to events.

**Key Features:**
- Server-side rendered components with real-time updates
- Pydantic-based state management with automatic validation
- WebSocket communication via Django Channels
- HTML diff for efficient bandwidth usage
- Model subscriptions for automatic UI updates
- Streams API for efficient large list handling
- Presence tracking for online users and typing indicators
- File uploads with progress tracking

## Improvements over django-reactor

Wireview is a modern evolution of [django-reactor](https://github.com/edelvalle/reactor), with significant improvements:

### New Features

| Feature | reactor | wireview | Description |
|---------|---------|----------|-------------|
| **Streams API** | - | ✅ | Memory-efficient large list handling with `stream()`, `stream_insert()`, `stream_delete()` |
| **Presence API** | - | ✅ | Real-time user tracking and typing indicators with `PresenceMixin`, `PresenceTrackerMixin` |
| **File Uploads** | - | ✅ | Chunked uploads with progress tracking, magic bytes validation |
| **AsyncResult** | - | ✅ | Loading/success/error state management for async operations |
| **JS Commands** | - | ✅ | Phoenix LiveView.JS-style client-side commands with `JS()` builder |
| **Testing Utils** | - | ✅ | `mount()` utility for easy component testing without WebSocket |
| **Debug Tools** | - | ✅ | Browser console debugging with `wireview.debug` |

### Architecture Improvements

| Aspect | reactor | wireview |
|--------|---------|----------|
| **Pydantic** | v1 (legacy) | v2 (modern) |
| **DOM Morphing** | morphdom | idiomorph (better attribute preservation) |
| **Python** | ≥3.9 | ≥3.10 |
| **Django** | 3.2+ | 4.2, 5.0, 5.1, 6.0 |
| **Module Structure** | Flat | Organized (`core/`, `features/`) |

### New Component Methods

```python
# Lifecycle
async def leaving(self):
    """Called when component disconnects - cleanup hook"""

# UI Control
await self.scroll_into_view(element_id, behavior="smooth")
await self.push_js(JS().set_value("input", ""))

# Streams
await self.stream("items", items)
await self.stream_insert("items", item, at=0)
await self.stream_delete("items", item_id)

# Presence
await self.presence_join()
await self.presence_set_typing(True)

# Async Loading
self.data = await self.assign_async(fetch_data())
```

### Migration from reactor

Most reactor components work with minimal changes:

```python
# reactor
from reactor.component import Component

class XCounter(Component):
    _subscriptions = {"counter"}

# wireview (same API)
from wireview.component import Component

class XCounter(Component):
    _subscriptions = {"counter"}
```

Key differences:
- Package name: `reactor` → `wireview`
- Settings prefix: `REACTOR_*` → `WIREVIEW` dict
- Template tag: `{% load reactor %}` → `{% load wireview %}`

## Table of Contents

- [Improvements over django-reactor](#improvements-over-django-reactor)
- [Installation and Setup](#installation-and-setup)
- [Quick Start](#quick-start)
- [Component Lifecycle](#component-lifecycle)
- [Event Binding](#event-binding)
- [URL State Management](#url-state-management)
- [Model Subscriptions](#model-subscriptions)
- [Streams API](#streams-api)
- [Presence API](#presence-api)
- [File Uploads](#file-uploads)
- [AsyncResult](#asyncresult-and-async-operations)
- [JS Command Builder](#js-command-builder)
- [Component API Reference](#component-api-reference)
- [Template Tags Reference](#template-tags-reference)
- [JavaScript API](#front-end-apis)
- [Testing](#testing-components)
- [Debug Tools](#debug-tools)
- [Settings](#settings)

## Installation and Setup

Wireview requires Python >=3.10 and Django >=4.2 (supports Django 4.2, 5.0, 5.1, and 6.0).

```bash
pip install django-wireview
```

Wireview uses `django-channels`. By default, Channels uses an InMemory channel layer which doesn't support real broadcasting. For production, use Redis: [Channel Layers](https://channels.readthedocs.io/en/latest/topics/channel_layers.html)

Add `wireview` and `channels` to your `INSTALLED_APPS` before Django applications:

```python
INSTALLED_APPS = [
    'wireview',
    'channels',
    ...
]

ASGI_APPLICATION = 'project_name.asgi.application'
```

Modify your `project_name/asgi.py`:

```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_name.settings')

import django
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from wireview.urls import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
})
```

Include wireview JavaScript in your templates:

```html
{% load wireview %}
<!doctype html>
<html>
    <head>
        {% wireview_header %}
    </head>
    ...
</html>
```

## Quick Start

Create a template `x-counter.html`:

```html
{% load wireview %}
<div {% tag_header %}>
  {{ amount }}
  <button {% on 'click' 'inc' %}>+</button>
  <button {% on 'click' 'dec' %}>-</button>
  <button {% on 'click' 'set_to' amount=0 %}>reset</button>
</div>
```

Create the component in `live.py`:

```python
from wireview.component import Component


class XCounter(Component):
    _template_name = 'x-counter.html'

    amount: int = 0

    async def inc(self):
        self.amount += 1

    async def dec(self):
        self.amount -= 1

    async def set_to(self, amount: int):
        self.amount = amount
```

Render the component in a view template:

```html
{% load wireview %}
<!doctype html>
<html>
    <head>
        {% wireview_header %}
    </head>
    <body>
        {% component 'XCounter' %}
        {% component 'XCounter' amount=100 %}
    </body>
</html>
```

## Component Lifecycle

### Initialization & Rendering

Components are initialized when included in a template:

```html
{% component 'Component' param1=1 param2=2 %}
```

Parameters are passed to `Component.new()` which returns the component instance.

### Joins

When the component reaches the front-end, it "joins" the backend via WebSocket. The serialized state is sent to the backend, which rebuilds the component and calls `Component.joined()`.

```python
class ChatRoom(Component):
    async def joined(self):
        # Called when component connects via WebSocket
        await self.broadcast(f"room.{self.room_id}", action="joined", user=self.username)
```

### Leaving

When a component is destroyed or the WebSocket connection closes, `Component.leaving()` is called. Use this for cleanup:

```python
class ChatRoom(Component):
    async def leaving(self):
        # Called when component disconnects
        await self.broadcast(f"room.{self.room_id}", action="left", user=self.username)
```

### User Events

After joining, components can receive user events via the `{% on %}` template tag. Events are sent to the backend, the handler is executed, and the component is re-rendered.

### Model Subscriptions

Components can subscribe to model changes. When a mutation occurs, `Component.mutation()` is called:

```python
class TodoList(Component):
    _subscriptions = {"todo-item"}  # Subscribe to todo item changes

    async def mutation(self, channel: str, action: ModelAction, instance):
        # Called when subscribed model changes
        self.items = await self.load_items()
```

### Notifications

For arbitrary messages, use `broadcast()` and `notification()`:

```python
# Sender
await self.broadcast("chat.room.1", message="Hello!", sender=self.username)

# Receiver (subscribed to "chat.room.1")
async def notification(self, channel: str, **kwargs):
    message = kwargs.get("message")
    sender = kwargs.get("sender")
```

## Event Binding

### Basic Syntax

```html
{% on <event.modifiers> <handler> [kwargs] %}
```

Examples:

```html
<button {% on "click" "increment" %}>+1</button>
<button {% on "click" "increment" amount=5 %}>+5</button>
<button {% on "click.prevent" "submit" %}>Submit</button>
<input {% on "keypress.enter" "search" %}>
<input {% on "input.debounce.300" "filter" %}>
```

### Available Modifiers

| Modifier | Description |
|----------|-------------|
| `prevent` | Calls `event.preventDefault()` |
| `stop` | Calls `event.stopPropagation()` |
| `ctrl`, `alt`, `shift`, `meta` | Requires modifier key |
| `debounce.<ms>` | Debounces the event (e.g., `debounce.300`) |
| `throttle.<ms>` | Throttles the event (e.g., `throttle.100`) |
| `enter`, `tab`, `delete`, `backspace`, `space` | Key aliases |
| `up`, `down`, `left`, `right` | Arrow key aliases |
| `key.<keycode>` | Specific key (e.g., `key.escape`) |
| `inlinejs` | Treats handler as literal JavaScript |

### Implicit Arguments

Form inputs within a component are automatically sent as arguments:

```html
<div {% tag_header %}>
  <input name="query">
  <button {% on "click" "search" %}>Search</button>
</div>
```

```python
async def search(self, query: str):
    self.results = await self.do_search(query)
```

## URL State Management

Persist component state in the URL query string:

```python
class SearchList(Component):
    query: str = ""

    @classmethod
    def new(cls, wire, **kwargs):
        kwargs.setdefault("query", wire.params.get("query", ""))
        return cls(wire=wire, **kwargs)

    async def filter_results(self, query: str):
        self.query = query
        self.wire.params["query"] = query  # Updates URL
```

For complex values, use `.json` suffix:

```python
class TreeView(Component):
    @classmethod
    def new(cls, wire, id: str, **kwargs):
        kwargs["expanded"] = id in wire.params.get("expanded.json", [])
        return cls(wire=wire, id=id, **kwargs)

    async def toggle_expanded(self):
        self.expanded = not self.expanded
        expanded = self.wire.params.setdefault("expanded.json", [])
        if self.expanded:
            expanded.append(self.id)
        elif self.id in expanded:
            expanded.remove(self.id)
```

## Model Subscriptions

Subscribe to Django model changes for automatic UI updates:

```python
class TodoList(Component):
    _subscriptions = {"todo-item"}

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.CREATED:
            self.items.append(instance)
        elif action == ModelAction.DELETED:
            self.items = [i for i in self.items if i.id != instance.id]
```

Enable auto-broadcast in settings:

```python
WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,      # Broadcast on model changes
        model_pk=True,   # Include PK in channel name
    ),
}
```

## Streams API

Streams provide memory-efficient handling of large lists by rendering items individually and sending incremental updates.

### Basic Usage

Template with stream container:

```html
{% load wireview %}
<div {% tag_header %}>
  <ul wire-stream="messages">
    {% for message in messages %}
      {% include "chat/message_item.html" %}
    {% endfor %}
  </ul>
</div>
```

Item template (`chat/message_item.html`):

```html
<li id="messages-{{ message.pk }}">
  <strong>{{ message.sender }}:</strong> {{ message.text }}
</li>
```

Component:

```python
class MessageList(Component):
    _template_name = "chat/message_list.html"
    messages: list = []

    async def joined(self):
        # Initial load with stream
        messages = await Message.objects.order_by('-created')[:50]
        await self.stream("messages", reversed(messages))

    async def add_message(self, text: str):
        message = await Message.objects.acreate(sender=self.user, text=text)
        await self.stream_insert("messages", message, at=-1)  # Append
        await self.scroll_into_view(f"messages-{message.pk}")

    async def delete_message(self, message_id: int):
        await Message.objects.filter(id=message_id).adelete()
        await self.stream_delete("messages", message_id)
```

### Stream Methods

| Method | Description |
|--------|-------------|
| `stream(name, items)` | Reset/initialize stream with items |
| `stream_insert(name, item, at=-1)` | Insert item (-1=append, 0=prepend, n=index) |
| `stream_delete(name, dom_id)` | Delete item by DOM ID or PK |

### DOM ID Convention

By default, DOM IDs follow the pattern `{stream_name}-{item.pk}`. Custom ID functions:

```python
await self.stream("items", items, dom_id=lambda item: f"item-{item.uuid}")
```

### Custom Item Templates

```python
await self.stream_insert("messages", message, template="chat/special_message.html")
```

## Presence API

Track online users and typing indicators in real-time.

### PresenceMixin (Producer)

For components that broadcast their own presence:

```python
from wireview.component import Component
from wireview.features.presence import PresenceMixin


class ChatInput(PresenceMixin, Component):
    _template_name = "chat/input.html"
    room_id: int
    username: str

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_user_id(self) -> str:
        return str(self.user_id)

    def _presence_username(self) -> str:
        return self.username

    async def joined(self):
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def on_typing(self):
        await self.presence_set_typing(True)  # Auto-clears after 3 seconds
```

### PresenceTrackerMixin (Consumer)

For components that display other users' presence:

```python
from wireview.features.presence import PresenceTrackerMixin


class OnlineUsers(PresenceTrackerMixin, Component):
    _template_name = "chat/online_users.html"
    room_id: int
    username: str

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_my_user_id(self) -> str:
        return str(self.user_id)

    @property
    def _subscriptions(self):
        return {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.username)
```

Template:

```html
{% load wireview %}
<div {% tag_header %}>
  <h3>Online ({{ this.presence_online_count }})</h3>
  <ul>
    {% for user in this.presence_users %}
      <li>
        {{ user.username }}
        {% if user.is_typing %}<span class="typing">typing...</span>{% endif %}
      </li>
    {% endfor %}
  </ul>
</div>
```

### Presence Properties

| Property | Description |
|----------|-------------|
| `presence_users` | List of all tracked users |
| `presence_online_count` | Number of online users |
| `presence_typing_users` | List of users currently typing |

### Configuration

```python
from wireview.features.presence import PresenceConfig

class MyComponent(PresenceMixin, Component):
    _presence_config = PresenceConfig(
        typing_timeout=3.0,     # Seconds until typing auto-clears
        sync_on_join=True,      # Request sync from others on join
        channel_prefix="presence",
    )
```

## File Uploads

Handle file uploads with progress tracking and validation.

### Basic Setup

```python
from wireview.component import Component
from wireview.features.uploads import UploadConfig


class FileUploader(Component):
    _template_name = "uploader.html"

    async def joined(self):
        self.allow_upload(UploadConfig(
            name="avatar",
            accept=[".jpg", ".png", ".gif"],
            max_file_size=5 * 1024 * 1024,  # 5MB
            max_entries=1,
        ))

    async def save_avatar(self):
        for upload in self.consume_uploads("avatar"):
            path = await upload.save_to("avatars/", filename=f"{self.user_id}.jpg")
            self.avatar_url = path
```

Template:

```html
{% load wireview %}
<div {% tag_header %}>
  <input type="file" wire-upload="avatar" accept=".jpg,.png,.gif">

  {% for entry in this.uploads.avatar %}
    <div class="upload-entry">
      {{ entry.client_name }} - {{ entry.progress }}%
      {% if entry.errors %}
        <span class="error">{{ entry.errors|join:", " }}</span>
      {% endif %}
    </div>
  {% endfor %}

  <button {% on "click" "save_avatar" %}>Save</button>
</div>
```

### UploadConfig Options

| Option | Default | Description |
|--------|---------|-------------|
| `name` | required | Upload field identifier |
| `accept` | `[]` | Allowed extensions (e.g., `[".jpg", ".png"]`) |
| `max_entries` | `1` | Maximum concurrent uploads |
| `max_file_size` | `10MB` | Maximum file size in bytes |
| `chunk_size` | `64KB` | Upload chunk size |
| `auto_upload` | `True` | Start upload immediately on selection |

### ConsumedUpload Methods

| Method | Description |
|--------|-------------|
| `read()` | Read entire file into memory |
| `open(mode="rb")` | Open file handle |
| `save_to(directory, filename=None)` | Save to Django storage |
| `name` | Original filename |
| `size` | File size in bytes |
| `content_type` | MIME type |

### Security

Wireview validates file signatures (magic bytes) before saving to prevent extension spoofing.

## AsyncResult and Async Operations

Handle async data loading with loading/error states:

```python
from wireview import Component, AsyncResult


class Dashboard(Component):
    _template_name = "dashboard.html"
    stats: AsyncResult = None

    async def joined(self):
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        return await Stats.objects.aget()
```

Template:

```html
{% if stats.loading %}
  <div class="spinner">Loading...</div>
{% elif stats.ok %}
  <div>Total: {{ stats.result.total }}</div>
{% elif stats.failed %}
  <div class="error">{{ stats.error_message }}</div>
{% endif %}
```

### AsyncResult Properties

| Property | Description |
|----------|-------------|
| `loading` | True while operation is in progress |
| `ok` | True if operation succeeded |
| `failed` | True if operation failed |
| `done` | True if completed (success or failure) |
| `result` | The result value (if successful) |
| `error` | The exception (if failed) |
| `error_message` | String representation of the error |

### AsyncResult Methods

| Method | Description |
|--------|-------------|
| `map(func)` | Transform the result value |
| `get_or(default)` | Get result or default value |
| `get_or_raise()` | Get result or raise the error |

## JS Command Builder

Build client-side commands that execute without server round-trips:

```python
from wireview import JS

# In template
<button {% on "click" JS().toggle("#modal") %}>Toggle Modal</button>

# Chaining commands
<button {% on "click" JS().add_class("#btn", "loading").push("save") %}>
  Save
</button>

# With transitions
<div {% on "click" JS().hide(transition=("fade-out", 300)) %}></div>
```

### Push JS from Server

Send JS commands from event handlers:

```python
async def clear_input(self):
    await self.push_js(JS().set_value("input[name=search]", ""))
```

### Available Commands

**Visibility:**
- `show(selector, transition=None, display=None)`
- `hide(selector, transition=None)`
- `toggle(selector, show=None, hide=None)`

**CSS Classes:**
- `add_class(selector, classes, transition=None)`
- `remove_class(selector, classes, transition=None)`
- `toggle_class(selector, classes, transition=None)`

**Attributes:**
- `set_attr(selector, attr, value)`
- `remove_attr(selector, attr)`
- `set_value(selector, value)` - Set input value

**Focus:**
- `focus(selector)`
- `focus_first(selector, input_only=False)`

**Transitions:**
- `transition(selector, classes, time=None)`

**Server Communication:**
- `push(event, value=None, target=None)` - Send event to server

**Navigation:**
- `navigate(url, replace=False)`
- `dispatch(event, to=None, detail=None, bubbles=True)`

### Loading Classes

During server requests, these classes are automatically added:

| Class | Description |
|-------|-------------|
| `wireview-loading` | Added during any request |
| `wireview-click-loading` | Added for click events |
| `wireview-submit-loading` | Added for submit events |

```css
.wireview-loading {
  opacity: 0.5;
  pointer-events: none;
}
```

## Component API Reference

### Class Attributes

| Attribute | Default | Description |
|-----------|---------|-------------|
| `_template_name` | required | Template path |
| `_exclude_fields` | `{"user", "wire"}` | Fields excluded from serialization |
| `_subscriptions` | `set()` | Channels to subscribe to |

### Lifecycle Methods

| Method | Description |
|--------|-------------|
| `new(cls, wire, **kwargs)` | Class method to construct instance |
| `joined()` | Called when component connects via WebSocket |
| `leaving()` | Called when component disconnects |
| `mutation(channel, action, instance)` | Called on model changes |
| `notification(channel, **kwargs)` | Called on broadcast messages |

### Render Control

| Method | Description |
|--------|-------------|
| `skip_render()` | Skip the next render cycle |
| `send_render()` | Force immediate render |
| `force_render()` | Mark for re-render |
| `freeze()` | Prevent all future renders |

### Actions

| Method | Description |
|--------|-------------|
| `destroy()` | Remove component from interface |
| `focus_on(selector)` | Focus an element |
| `scroll_into_view(element_id, behavior="auto", block="start", inline="nearest")` | Scroll element into view |
| `push_js(js)` | Execute JS commands on client |
| `dom(action, id, component_or_template, **kwargs)` | DOM manipulation |
| `deffer(func, *args, **kwargs)` | Defer function execution |

### Broadcasting

| Method | Description |
|--------|-------------|
| `broadcast(channel, **kwargs)` | Send message to channel (queued in `joined()`) |
| `abroadcast(channel, **kwargs)` | Send message immediately (async) |

### Navigation

| Method | Description |
|--------|-------------|
| `wire.redirect_to(url, **kwargs)` | Navigate and fetch new page |
| `wire.replace_to(url, **kwargs)` | Replace current URL |
| `wire.push_to(url, **kwargs)` | Push URL without fetch |

### Streams

| Method | Description |
|--------|-------------|
| `stream(name, items, template=None, dom_id=None)` | Initialize/reset stream |
| `stream_insert(name, item, at=-1, template=None, dom_id=None)` | Insert item |
| `stream_delete(name, dom_id)` | Delete item |

### Uploads

| Method | Description |
|--------|-------------|
| `allow_upload(config)` | Register upload configuration |
| `consume_uploads(name)` | Get completed uploads |
| `cancel_upload(name, ref)` | Cancel an upload |

## Template Tags Reference

```html
{% load wireview %}
```

| Tag | Description |
|-----|-------------|
| `{% wireview_header %}` | Include required JavaScript (~10KB minified) |
| `{% component 'Name' kwarg=value %}` | Render a component |
| `{% on 'event.modifiers' 'handler' kwargs %}` | Bind event handler |
| `{% tag_header %}` | Add component attributes to root element |
| `{% cond {'hidden': is_hidden} %}` | Conditional attribute |
| `{% class {'active': is_active} %}` | Conditional CSS classes |

## Front-end APIs

```javascript
// Send event to component
wireview.send(element, 'handler_name', {arg1: value1})

// Debounce/throttle
wireview.debounce(300)(fn)
wireview.throttle(100)(fn)

// Execute JS commands
wireview.exec(element, commands)

// Debug utilities
wireview.debug.enable()
wireview.debug.disable()
wireview.debug.status()
```

## Testing Components

Test components without WebSocket:

```python
import pytest
from wireview.testing import mount


@pytest.mark.asyncio
async def test_counter_increment():
    view = await mount(Counter, count=0)
    await view.call("increment", amount=5)
    assert view.component.count == 5
    assert len(view.sent_messages) > 0


@pytest.mark.asyncio
async def test_redirect():
    view = await mount(MyComponent)
    await view.call("do_redirect", url="/dashboard")
    assert view.redirected_to == "/dashboard"
    assert view.is_frozen
```

### Testing API

| Method/Property | Description |
|----------------|-------------|
| `mount(ComponentClass, **kwargs)` | Mount component for testing |
| `view.component` | Access component instance |
| `view.call(handler, **kwargs)` | Call event handler |
| `view.sent_messages` | Messages that would be sent |
| `view.redirected_to` | Redirect URL (if any) |
| `view.is_frozen` | Whether component is frozen |
| `view.clear_messages()` | Clear sent messages |

## Debug Tools

```javascript
// Enable debug logging
wireview.debug.enable()

// Disable debug logging
wireview.debug.disable()

// Simulate network latency
wireview.debug.latency(500)  // 500ms delay

// Show connection status
wireview.debug.status()

// List all components
wireview.debug.components()

// Get specific component
wireview.debug.component("rx-123")
```

## Settings

```python
from wireview.schemas import AutoBroadcast

WIREVIEW = {
    "TRANSPILER_CACHE_SIZE": 1024,    # Event handler cache size
    "USE_HTML_DIFF": True,            # Enable HTML diffing
    "USE_HMIN": False,                # Use django-hmin minification
    "BOOST_PAGES": False,             # Enable client-side navigation
    "AUTO_BROADCAST": AutoBroadcast(
        model=False,       # Broadcast on model changes
        model_pk=False,    # Include PK in channel
        related=False,     # Broadcast related model changes
        m2m=False,         # Broadcast M2M changes
        senders=set(),     # Models to auto-broadcast
    ),
}
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - Internal design and patterns
- [Tutorials](docs/tutorials/) - Step-by-step guides
- [Roadmap](docs/ROADMAP.md) - Future development plans

## Development & Contributing

```bash
git clone git@github.com:itda-work/django-wireview.git
cd django-wireview
make install
make test
```

Run the test server:

```bash
cd tests
python manage.py runserver
```

## License

MIT License - see [LICENSE](LICENSE) for details.
