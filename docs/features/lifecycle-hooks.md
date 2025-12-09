# Lifecycle Hooks

django-wireview provides lifecycle hooks for sharing common functionality
across components. This includes `on_mount` hooks for initialization and
`attach_hook` for intercepting lifecycle stages.

## Overview

| Feature | Description |
|---------|-------------|
| `_on_mount` | Class-level hooks that run before `joined()` |
| `attach_hook()` | Instance-level hooks for intercepting events |
| `detach_hook()` | Remove an attached hook |

## on_mount Hooks

Define reusable hooks that run during component initialization.

### Defining a Hook

```python
class AuthHook:
    """Ensure user is authenticated."""

    @staticmethod
    async def on_mount(component, params, session):
        if not component.user.is_authenticated:
            await component.wire.redirect_to("/login")
            return {"halt": True}
        return {"cont": True}


class TrackingHook:
    """Track page views."""

    @staticmethod
    async def on_mount(component, params, session):
        await analytics.track_page_view(
            user=component.user,
            path=params.get("path", "/"),
        )
        return {"cont": True}
```

### Using Hooks

```python
from wireview import Component


class ProtectedDashboard(Component):
    _template_name = "dashboard.html"
    _on_mount = [AuthHook, TrackingHook]

    async def joined(self):
        # This only runs if all on_mount hooks return {"cont": True}
        self.data = await self.load_dashboard_data()
```

### Hook Execution Order

1. Hooks run in the order they're listed in `_on_mount`
2. If any hook returns `{"halt": True}`, remaining hooks are skipped
3. If halted, `joined()` is not called
4. Component still renders (useful for redirects)

### Hook Signature

```python
async def on_mount(
    component: Component,
    params: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, bool]:
    """
    Args:
        component: The component instance being mounted
        params: URL parameters and component kwargs
        session: Session data (if available)

    Returns:
        {"cont": True} to continue, {"halt": True} to stop
    """
```

## attach_hook

Attach hooks to intercept specific lifecycle stages after mount.

### Available Stages

| Stage | When Called | Use Case |
|-------|-------------|----------|
| `handle_event` | Before event handlers | Event logging, validation |
| `handle_params` | Before `params_changed` | URL tracking, guards |
| `after_render` | After component renders | Analytics, cleanup |

### Attaching Hooks

```python
class EventLoggingHook:
    @staticmethod
    async def on_mount(component, params, session):
        async def log_events(event: str, params: dict):
            await logger.info(f"Event: {event}", extra=params)
            return {"cont": True}

        component.attach_hook("event_logger", "handle_event", log_events)
        return {"cont": True}
```

### Hook Signatures by Stage

**handle_event:**
```python
async def hook(event: str, params: dict) -> dict:
    # Return {"halt": True} to prevent event handler execution
    # Return {"cont": True} to continue
    return {"cont": True}
```

**handle_params:**
```python
async def hook(params: dict, uri: str) -> dict:
    # Return {"halt": True} to skip params_changed
    return {"cont": True}
```

**after_render:**
```python
async def hook() -> None:
    # No return value needed
    pass
```

### Detaching Hooks

```python
# Remove a specific hook
component.detach_hook("event_logger")

# Remove from specific stage only
component.detach_hook("event_logger", stage="handle_event")
```

## Common Patterns

### Authentication Guard

```python
class RequireAuth:
    """Redirect unauthenticated users to login."""

    @staticmethod
    async def on_mount(component, params, session):
        if not component.user.is_authenticated:
            # Store intended destination
            await component.wire.redirect_to(
                f"/login?next={params.get('path', '/')}"
            )
            return {"halt": True}
        return {"cont": True}


class RequireAdmin:
    """Redirect non-admin users."""

    @staticmethod
    async def on_mount(component, params, session):
        if not component.user.is_staff:
            await component.wire.redirect_to("/forbidden")
            return {"halt": True}
        return {"cont": True}
```

### Event Tracking

```python
class GoogleAnalytics:
    """Track all events to Google Analytics."""

    @staticmethod
    async def on_mount(component, params, session):
        async def track_event(event: str, params: dict):
            await ga.track_event(
                category="component",
                action=event,
                label=component.__class__.__name__,
            )
            return {"cont": True}

        component.attach_hook("ga_tracking", "handle_event", track_event)
        return {"cont": True}
```

### Rate Limiting

```python
class RateLimitHook:
    """Limit event frequency."""

    @staticmethod
    async def on_mount(component, params, session):
        last_event_time = {}

        async def check_rate_limit(event: str, params: dict):
            now = time.time()
            last = last_event_time.get(event, 0)

            if now - last < 0.1:  # 100ms minimum between events
                return {"halt": True}

            last_event_time[event] = now
            return {"cont": True}

        component.attach_hook("rate_limit", "handle_event", check_rate_limit)
        return {"cont": True}
```

### Audit Logging

```python
class AuditLog:
    """Log all user actions for compliance."""

    @staticmethod
    async def on_mount(component, params, session):
        async def audit_event(event: str, params: dict):
            await AuditEntry.objects.acreate(
                user=component.user,
                action=event,
                component=component.__class__.__name__,
                data=params,
            )
            return {"cont": True}

        component.attach_hook("audit", "handle_event", audit_event)
        return {"cont": True}
```

## Comparison with Phoenix LiveView

| Feature | Phoenix LiveView | django-wireview |
|---------|------------------|-----------------|
| Class hooks | `on_mount: [Hook]` | `_on_mount = [Hook]` |
| Instance hooks | `attach_hook/4` | `attach_hook()` |
| Detach hooks | `detach_hook/3` | `detach_hook()` |
| Hook stages | `:handle_event`, `:handle_params`, `:handle_info`, `:handle_async`, `:after_render` | `handle_event`, `handle_params`, `after_render` |
| Return value | `{:cont, socket}` or `{:halt, socket}` | `{"cont": True}` or `{"halt": True}` |
| Session access | Full session | Passed as parameter |
