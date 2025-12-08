# JavaScript Hooks

JavaScript Hooks enable integration of third-party JavaScript libraries (Chart.js, Mapbox, CodeMirror, etc.) with wireview components. This feature follows the Phoenix LiveView hooks pattern for familiarity.

## Quick Start

### 1. Define a Hook

```javascript
window.wireview.hooks.ChartHook = {
  mounted() {
    // Called when element joins the page
    const config = JSON.parse(this.el.dataset.config);
    this.chart = new Chart(this.el, config);
  },

  updated() {
    // Called after DOM morph
    this.chart.update();
  },

  destroyed() {
    // Called when element is removed
    this.chart.destroy();
  }
};
```

### 2. Use in Template

```html
<div wire-hook="ChartHook" data-config='{"type": "line", "data": {...}}'>
</div>
```

## Hook Lifecycle

| Callback | When Called | Use Case |
|----------|-------------|----------|
| `mounted()` | After element joins and first render | Initialize third-party libraries |
| `beforeUpdate()` | Before DOM morph (sync) | Save scroll position, selection |
| `updated()` | After DOM morph completes | Restore state, update libraries |
| `destroyed()` | When element removed from DOM | Cleanup resources |
| `disconnected()` | When WebSocket closes | Show offline indicator |
| `reconnected()` | When WebSocket reconnects | Refresh data |

## Hook Context

Inside hook callbacks, `this` provides:

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `this.el` | `HTMLElement` | The DOM element with `wire-hook` attribute |

### Methods

| Method | Description |
|--------|-------------|
| `this.pushEvent(event, payload, callback)` | Send event to server |
| `this.handleEvent(event, callback)` | Register handler for server events |

## Server Communication

### Sending Events to Server (pushEvent)

```javascript
window.wireview.hooks.InfiniteScroll = {
  mounted() {
    this.observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) {
        this.loadMore();
      }
    });
    this.observer.observe(this.el.querySelector('.sentinel'));
  },

  loadMore() {
    // Push event to server with callback
    this.pushEvent("load_more", { page: this.page }, (response) => {
      console.log("Server response:", response);
      if (response.hasMore) {
        this.page++;
      } else {
        this.observer.disconnect();
      }
    });
    this.page = (this.page || 1) + 1;
  },

  destroyed() {
    this.observer.disconnect();
  }
};
```

### Handling Events from Server (handleEvent)

```javascript
window.wireview.hooks.Notification = {
  mounted() {
    // Register handler for server-pushed events
    this.handleEvent("show_toast", ({ message, type }) => {
      this.showToast(message, type);
    });

    this.handleEvent("highlight", ({ color }) => {
      this.el.style.backgroundColor = color;
      setTimeout(() => {
        this.el.style.backgroundColor = '';
      }, 1000);
    });
  },

  showToast(message, type) {
    // Your toast implementation
  }
};
```

### Server-side Handler

```python
from wireview.component import Component


class Dashboard(Component):
    _template_name = "dashboard.html"

    items: list = []
    page: int = 1

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict):
        """Handle events from JavaScript hooks.

        Args:
            hook_id: Unique identifier of the hook instance
            event: Event name sent by the hook
            payload: Event data from the hook

        Returns:
            Response data sent to the hook's callback (or None)
        """
        if event == "load_more":
            page = payload.get("page", 1)
            new_items = await self.fetch_items(page)
            self.items.extend(new_items)

            return {
                "hasMore": len(new_items) == 20,
                "count": len(new_items)
            }

        return None

    async def notify_user(self, message: str):
        """Push event to all hooks in this component."""
        await self.push_event("show_toast", {
            "message": message,
            "type": "success"
        })

    async def highlight_item(self, hook_id: str):
        """Push event to specific hook."""
        await self.push_event(
            "highlight",
            {"color": "yellow"},
            hook_id=hook_id
        )
```

## Multiple Hooks on Same Element

You can attach multiple hooks to a single element by separating names with spaces:

```html
<div wire-hook="Sortable Draggable Tooltip">
  <!-- Content -->
</div>
```

Each hook gets its own instance and lifecycle.

## Complete Examples

### Chart.js Integration

```javascript
window.wireview.hooks.Chart = {
  mounted() {
    const config = JSON.parse(this.el.dataset.config);
    this.chart = new Chart(this.el, config);

    // Handle server-pushed data updates
    this.handleEvent("update_data", ({ datasets }) => {
      this.chart.data.datasets = datasets;
      this.chart.update();
    });
  },

  beforeUpdate() {
    // Save chart state before morph
    this.chartState = {
      animation: this.chart.options.animation
    };
  },

  updated() {
    // Restore animation after morph
    this.chart.options.animation = this.chartState.animation;
  },

  destroyed() {
    this.chart.destroy();
  }
};
```

### CodeMirror Integration

```javascript
window.wireview.hooks.CodeEditor = {
  mounted() {
    this.editor = CodeMirror(this.el, {
      mode: this.el.dataset.mode || "javascript",
      lineNumbers: true
    });

    // Sync to server on change (debounced)
    let timeout;
    this.editor.on("change", () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        this.pushEvent("content_changed", {
          content: this.editor.getValue()
        });
      }, 300);
    });

    // Handle server-pushed content
    this.handleEvent("set_content", ({ content }) => {
      this.editor.setValue(content);
    });
  },

  destroyed() {
    this.editor.toTextArea();
  }
};
```

### Scroll Position Preservation

```javascript
window.wireview.hooks.PreserveScroll = {
  beforeUpdate() {
    // Save scroll position before morph
    this.scrollTop = this.el.scrollTop;
  },

  updated() {
    // Restore scroll position after morph
    this.el.scrollTop = this.scrollTop;
  }
};
```

### Offline Indicator

```javascript
window.wireview.hooks.ConnectionStatus = {
  mounted() {
    this.updateStatus(true);
  },

  disconnected() {
    this.updateStatus(false);
  },

  reconnected() {
    this.updateStatus(true);
  },

  updateStatus(connected) {
    this.el.classList.toggle('connected', connected);
    this.el.classList.toggle('disconnected', !connected);
    this.el.textContent = connected ? 'Connected' : 'Reconnecting...';
  }
};
```

## Best Practices

1. **Always cleanup in `destroyed()`**: Disconnect observers, destroy library instances, remove event listeners.

2. **Use `beforeUpdate()` for state preservation**: Save scroll position, focus, or selection before morphing.

3. **Initialize libraries in `mounted()`**: Don't initialize before the element is on the page.

4. **Handle reconnection gracefully**: Use `reconnected()` to refresh data that may have changed while offline.

5. **Use `handleEvent()` for push updates**: Instead of polling, let the server push updates when data changes.

6. **Keep hooks focused**: Each hook should do one thing well. Use multiple hooks on an element if needed.

## API Reference

### Component.handle_hook_event()

```python
async def handle_hook_event(
    self,
    hook_id: str,
    event: str,
    payload: dict[str, Any],
) -> Any
```

Handle events sent from client-side hooks via `pushEvent()`.

**Parameters:**
- `hook_id`: Unique identifier of the hook instance
- `event`: Event name sent by the hook
- `payload`: Event data from the hook

**Returns:** Response data sent back to the hook's callback (or `None`)

### Component.push_event()

```python
async def push_event(
    self,
    event: str,
    payload: dict[str, Any] | None = None,
    hook_id: str | None = None,
) -> None
```

Push an event to client-side JavaScript hooks.

**Parameters:**
- `event`: Event name to dispatch
- `payload`: Event data (default: empty dict)
- `hook_id`: Target specific hook instance (None = broadcast to all)
