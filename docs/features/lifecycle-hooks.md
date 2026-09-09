# 라이프사이클 훅

컴포넌트 사이에 공통 동작을 나눠 쓰는 장치다. 초기화 시점의 `_on_mount`와, 이후 단계를 가로채는
`attach_hook` 둘로 나뉜다.

| 기능 | 뜻 |
|------|-----|
| `_on_mount` | 클래스 단위 훅. `joined()`보다 먼저 돈다 |
| `attach_hook()` | 인스턴스 단위 훅. 이벤트를 가로챈다 |
| `detach_hook()` | 붙인 훅을 뗀다 |
| [`live_session`](./live-session.md) | 페이지 단위 경계. 세션 훅이 컴포넌트 훅보다 먼저 돈다 |

## on_mount 훅

컴포넌트 초기화 중에 도는, 재사용 가능한 훅이다.

### 훅 정의

```python
class AuthHook:
    """Ensure the user is authenticated."""

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

### 훅 사용

```python
from wireview import Component


class ProtectedDashboard(Component):
    _template_name = "dashboard.html"
    _on_mount = [AuthHook, TrackingHook]

    async def joined(self):
        # on_mount 훅이 전부 {"cont": True}를 돌려줬을 때만 실행된다
        self.data = await self.load_dashboard_data()
```

### 훅이 도는 자리

사용자가 볼 수 있는 컴포넌트 인스턴스를 만드는 **모든 경로**에서 돈다. 일부만 덮는 인가 훅은
경계가 아니기 때문이다. #75 이전에는 이 호출 지점들이 아예 없어서 `_on_mount`가 아무 일도 하지
않았다.

| 경로 | 언제 | 뒤이어 `joined()`? |
|------|------|--------------------|
| HTTP(dead) 렌더 | `{% component %}` / `{% live_component %}`가 첫 HTML을 그릴 때 | 아니다 — dead 렌더는 `joined()`를 부르지 않는다 |
| WebSocket join | `ComponentRepository.join()`, 인스턴스를 만든 직후 | 예 (halt하지 않았다면) |
| LiveComponent 자식 | 부모 렌더가 지목한 자식을 컨슈머가 정착시킬 때 | 예 (halt하지 않았다면) |
| `wireview.testing.mount()` | `joined()` 직전 — 단위 테스트도 훅을 지난다 | 예 (halt하지 않았다면) |

**dead 렌더가 가장 중요하다.** 보호 대상 HTML은 WebSocket이 붙기 한참 전에, 첫 응답과 함께 나간다.
join만 지키면 페이지를 한 번 보내 놓고 나서 리다이렉트하는 꼴이 된다.

템플릿 렌더는 동기이므로, HTTP 경로는 동기 코드에서 async 콜백을 불러내야 하고 그 방법이 스레드에
따라 갈린다. 동기 뷰나 템플릿 렌더를 `sync_to_async`로 감싼 async 뷰는 일반 워커 스레드에서 그리며,
거기서는 `asgiref.sync.async_to_sync`가 올바른 다리다. `render()`를 직접 부르는 `async def` 뷰는
이벤트 루프 스레드 위에서 그리는데 거기서는 `async_to_sync`가 예외를 던지므로, 훅은 보조 스레드에
자기 루프를 만들어 돈다. 어느 쪽이든 페이지는 그려진다. 그 보조 스레드에서 ORM을 건드리는 훅은
자기 커넥션을 새로 연다.

`_on_mount`가 비어 있고 페이지에 경계도 없는 컴포넌트는 이 경로에 들어가지도 않는다.

### 훅이 막을 수 **없는** 것

**중첩된 일반 `Component`**(다른 컴포넌트 템플릿 안의 `{% component %}`)는 WebSocket 경로에서
인가 경계가 아니다. 부모의 템플릿 렌더가 그것을 인라인으로 그려 부모의 render 프레임에 실어
보내고, 자기 `join`은 그 뒤에야 훅을 돌린다. 거기 붙인 훅은 join을 막을 뿐, 이미 나간 HTML은 막지
못한다.

인가 훅은 **페이지 컴포넌트**(자기 템플릿이 그리는 모든 것을 지킨다)나 **LiveComponent**(훅이 돈
뒤에 렌더한다 — `docs/design/live-component-ownership.md`)에 붙인다. 중첩된 일반 Component의
`_on_mount`는 추적과 `attach_hook` 배선용이지 마크업을 막는 용도가 아니다.

중첩된 일반 Component에도 적용되는 것이 하나 있다 — `_live_sessions` 선언이다. 이것은 await가
필요 없어서 부모의 템플릿 패스 안에서도 판정할 수 있고, 어긋나면 그 자리에 아무것도 그리지 않는다.
"이 컴포넌트는 이 페이지에 있으면 안 된다"를 중첩 위치에서도 걸고 싶다면 훅이 아니라 이쪽이다.

### 인스턴스당 한 번

훅은 **렌더마다가 아니라 인스턴스마다 한 번** 돈다. 다시 렌더하거나, 같은 렌더 안에서 같은 id를
가리키는 `{% component %}`가 하나 더 있거나, 부모가 다시 렌더하는 LiveComponent에서는 다시 돌지
않는다.

재join은 다르다. 이미 join한 id로 두 번째 `join`이 오면 그 인스턴스를 물러나게 하고 새 인스턴스를
mount하므로, 새 인스턴스가 훅을 돈다. HTTP 렌더와 그 뒤의 WebSocket join은 저장소가 다른 두
인스턴스이므로 양쪽에서 각각 돈다.

### 실행 순서

1. 페이지가 [`live_session`](./live-session.md) 안이면 그 세션의 훅이 **먼저** 돈다
2. 그다음 `_on_mount`에 적은 순서대로 돈다
3. 어느 하나가 `{"halt": True}`를 돌려주면 나머지는 건너뛴다
4. halt하면 `joined()`는 호출되지 않고, 컴포넌트는 **렌더되지 않는다**
5. 훅 안의 예외는 같은 경로에서 `joined()`가 던진 예외와 똑같이 다뤄진다 — WebSocket join은
   중단되고, LiveComponent 자식이 mount될 때는 로그를 남기고 넘어간다

**halt는 마크업까지 막는다** (#58부터). 거절된 컴포넌트는 HTML도 `data-state`도 내보내지 않고,
저장소에서도 지워져 그 id로 이벤트를 보내도 처리되지 않는다. 그 전에는 halt가 `joined()`만
건너뛰고 컴포넌트는 평소대로 그려졌는데, 그러면 가드가 막으려던 HTML이 그대로 나갔다.

**리다이렉트는 halt와 같이 쓴다.** `wire.redirect_to()`를 부른 뒤 halt하면 WebSocket에서는
클라이언트가 `url_change`를 받고, 소켓이 없는 HTTP 렌더에서는 `WireviewMeta.render()`가 컴포넌트
HTML 대신 `<meta http-equiv="refresh" content="0; url=...">`를 내보낸다. halt만 하면 아무것도
없는 자리가 남을 뿐이므로, 사용자가 어디로 가야 하는지는 훅이 말해 줘야 한다.

### 훅 시그니처

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

`session`은 호출 지점이 건넬 수 있었던 값이다 — HTTP 렌더에서는 `request.session`, WebSocket에서는
`scope["session"]`, `testing.mount()`에서는 `session=` 인자, 없으면 빈 세션. 훅이 받는 것은 컴포넌트가
`self.session`으로 보는 것과 **같은 읽기 전용 객체**다. 상세는 [세션 읽기](./session.md).

### 훅 점검

`manage.py check`는 wireview가 부를 수 없는 `_on_mount` 항목을 `wireview.W007`로 보고한다.
`on_mount` 메서드가 없는 클래스(런타임이 조용히 건너뛰어 컴포넌트가 무방비가 된다)나, async가
아닌 `on_mount`가 그것이다. [시스템 체크](./checks.md) 참고.

## attach_hook

mount 이후의 특정 단계를 가로챈다.

| 단계 | 언제 | 쓰임새 |
|------|------|--------|
| `handle_event` | 이벤트 핸들러 직전 | 이벤트 로깅, 검증 |
| `handle_params` | `params_changed` 직전 | URL 추적, 가드 |
| `after_render` | 컴포넌트 렌더 직후 | 분석, 정리 |

### 붙이기

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

### 단계별 시그니처

**handle_event**

```python
async def hook(event: str, params: dict) -> dict:
    # {"halt": True}면 이벤트 핸들러가 실행되지 않는다
    # {"cont": True}면 계속한다
    return {"cont": True}
```

**handle_params**

```python
async def hook(params: dict, uri: str) -> dict:
    # {"halt": True}면 params_changed를 건너뛴다
    return {"cont": True}
```

**after_render**

```python
async def hook() -> None:
    # 반환값이 필요 없다
    pass
```

### 떼기

```python
# 이름으로 전부 뗀다
component.detach_hook("event_logger")

# 특정 단계에서만 뗀다
component.detach_hook("event_logger", stage="handle_event")
```

## 자주 쓰는 형태

### 인증 가드

```python
class RequireAuth:
    """Redirect unauthenticated users to the login page."""

    @staticmethod
    async def on_mount(component, params, session):
        if not component.user.is_authenticated:
            # 돌아올 곳을 기억해 둔다
            await component.wire.redirect_to(
                f"/login?next={params.get('path', '/')}"
            )
            return {"halt": True}
        return {"cont": True}


class RequireAdmin:
    """Redirect users who are not staff."""

    @staticmethod
    async def on_mount(component, params, session):
        if not component.user.is_staff:
            await component.wire.redirect_to("/forbidden")
            return {"halt": True}
        return {"cont": True}
```

### 이벤트 추적

```python
class GoogleAnalytics:
    """Track every event to Google Analytics."""

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

### 속도 제한

```python
class RateLimitHook:
    """Limit how often events may fire."""

    @staticmethod
    async def on_mount(component, params, session):
        last_event_time = {}

        async def check_rate_limit(event: str, params: dict):
            now = time.time()
            last = last_event_time.get(event, 0)

            if now - last < 0.1:  # 이벤트 간 최소 100ms
                return {"halt": True}

            last_event_time[event] = now
            return {"cont": True}

        component.attach_hook("rate_limit", "handle_event", check_rate_limit)
        return {"cont": True}
```

### 감사 로그

```python
class AuditLog:
    """Record every user action for compliance."""

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

## Phoenix LiveView 대응

| 기능 | Phoenix LiveView | django-wireview |
|------|------------------|-----------------|
| 클래스 훅 | `on_mount: [Hook]` | `_on_mount = [Hook]` |
| 인스턴스 훅 | `attach_hook/4` | `attach_hook()` |
| 훅 떼기 | `detach_hook/3` | `detach_hook()` |
| 단계 | `:handle_event`, `:handle_params`, `:handle_info`, `:handle_async`, `:after_render` | `handle_event`, `handle_params`, `after_render` |
| 반환값 | `{:cont, socket}` / `{:halt, socket}` | `{"cont": True}` / `{"halt": True}` |
| 세션 접근 | 세션 전체 | 인자로 전달 |

## live_session과의 역할 구분

셋은 서로 다른 질문에 답한다. 하나가 통과했다고 다음이 통과하는 것이 아니다.

| | 무엇을 판정하나 | 어디서 | 통과하면 |
|---|---|---|---|
| `live_session`의 `authorize` | 이 사용자가 **이 페이지**에 들어올 수 있는가 | 뷰(첫 바이트 전)와 join(마운트 전) | 페이지의 컴포넌트들이 만들어지기 시작한다 |
| `_on_mount` / 세션 `on_mount` | 이 컴포넌트를 마운트할 때 **무엇을 먼저 하나** | 컴포넌트를 만드는 모든 경로 | `joined()`가 돌고 컴포넌트가 렌더된다 |
| 이벤트 인가 | 이 사용자가 **이 객체**를 건드릴 수 있는가 | 핸들러 안 (직접 쓴다) | 그 한 번의 조작이 일어난다 |

**페이지에 들어왔다는 것이 그 안의 객체를 건드릴 권한을 뜻하지 않는다.** `live_session`은 페이지의
문이지 행마다 붙는 자물쇠가 아니다. 남의 주문서 id로 이벤트를 보내는 것은 여전히 핸들러가 막아야
한다.

`live_session`을 쓸 때 `_on_mount`가 없어지지는 않는다. 페이지 전체에 걸리는 것(인증, 감사 로그)은
세션의 `on_mount`로 올리고, 컴포넌트 하나에만 해당하는 것은 그대로 `_on_mount`에 둔다. 상세는
[live_session](./live-session.md).

## 관련

- [live_session](./live-session.md) — 페이지 단위 경계. 세션 훅이 컴포넌트 훅보다 먼저 돈다
- [시스템 체크](./checks.md) — 부를 수 없는 `_on_mount` 항목을 잡는 `wireview.W007`,
  경계와 어긋난 선언을 잡는 `wireview.W010`
- [LiveComponent](./live-component.md) — 중첩 컴포넌트와 그 수명주기
