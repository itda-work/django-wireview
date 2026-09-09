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

### Where the Hooks Run

Every path that produces a component instance a user can see runs the hooks,
because an authorization hook that covers only some of them is not a boundary
at all. Until #75 none of these call sites existed and `_on_mount` did nothing.

| Path | When | `joined()` after? |
|------|------|-------------------|
| HTTP (dead) render | `{% component %}` / `{% live_component %}` renders the first HTML | No — a dead render never calls `joined()` |
| WebSocket join | `ComponentRepository.join()`, right after the instance is built | Yes, unless a hook halted |
| LiveComponent child | The consumer settles the children the parent's render named | Yes, unless a hook halted |
| `wireview.testing.mount()` | Before `joined()`, so unit tests exercise the hooks | Yes, unless a hook halted |

The dead render matters most: the protected HTML goes out with the very first
response, long before the WebSocket connects. Guarding only the join would ship
the page once and then redirect.

Because the template pass is synchronous, the HTTP path has to get an async
callback out of sync code, and how it does that depends on the thread. A sync
view, or an async view whose template pass is wrapped in `sync_to_async`,
renders on a plain worker thread, where `asgiref.sync.async_to_sync` is the
right bridge. An `async def` view that calls `render()` directly renders on the
event-loop thread itself, where `async_to_sync` raises; there the hooks get
their own loop on a helper thread instead, so the page renders either way. A
hook that touches the ORM on that helper thread opens its own connection.

A component with an empty `_on_mount` never reaches any of this.

### What the Hooks Cannot Gate

A **nested regular `Component`** (a `{% component %}` inside another component's
template) is not an authorization boundary on the WebSocket path. The parent's
template pass renders it inline, so its HTML travels inside the parent's render
frame, and its own `join` runs the hooks only afterwards. A hook there stops the
join, not the HTML that already went out.

Put an authorization hook on the **page component**, which guards everything its
template renders, or on a **LiveComponent**, whose render happens after its hooks
run (see `docs/design/live-component-ownership.md`). On a nested regular
Component, `_on_mount` is for tracking and `attach_hook` wiring, not for gating
its markup.

### Once per Instance

The hooks run **once per component instance**, not once per render. A re-render,
a second `{% component %}` tag naming the same id in the same pass, or a
LiveComponent whose parent re-renders will not run them again.

A re-join is different: a second `join` for an id that already joined retires
that instance and mounts a fresh one, so the fresh instance runs the hooks. An
HTTP render and the WebSocket join that follows it are two instances in two
repositories, so the hooks run in both.

### Hook Execution Order

1. Hooks run in the order they're listed in `_on_mount`
2. If any hook returns `{"halt": True}`, remaining hooks are skipped
3. If halted, `joined()` is not called
4. Component still renders (see below: halt alone does not withhold the HTML)
5. An exception inside a hook is treated exactly like one inside `joined()` on
   the same path: it aborts a WebSocket join, and it is logged and stepped over
   when a LiveComponent child mounts

**A halt on its own does not hide anything.** It skips the remaining hooks and
`joined()`, and then the component renders its template as usual, on every path,
the HTTP render included. A guard that only returns `{"halt": True}` still ships
the protected HTML; it merely leaves the component's state unloaded.

To actually withhold the markup, halt **and** redirect. `wire.redirect_to()`
freezes the component, so over the WebSocket the client gets a `url_change` frame
and no render, and on an HTTP render, where there is no socket,
`WireviewMeta.render()` emits `<meta http-equiv="refresh" content="0; url=...">`
in place of the component's own HTML.

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
        params: URL parameters, as the repository holds them
        session: The request session, when the call site has one

    Returns:
        {"cont": True} to continue, {"halt": True} to stop
    """
```

`session` is whatever the call site could hand over: `request.session` on an
HTTP render, `scope["session"]` on a WebSocket connection, the `session=`
argument of `testing.mount()`, and `{}` when there is none. It is passed to the
hooks only. Components have no `self.session` API — that is a separate gap
(GAP-029).

### Checking Your Hooks

`manage.py check` reports an `_on_mount` entry wireview cannot call as
`wireview.W007`: a class with no `on_mount` method (which the runtime skips in
silence, leaving the component unguarded) or an `on_mount` that is not async.
See [System Checks](./checks.md).

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

## Related

- [System Checks](./checks.md) — `wireview.W007` on an `_on_mount` entry that cannot run
- [LiveComponent](./live-component.md) — nested components and their lifecycle
