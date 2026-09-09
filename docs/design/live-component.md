# LiveComponent 설계 문서 (GAP-005)

> Phoenix LiveView의 LiveComponent를 django-wireview에 구현하기 위한 설계

---

## 1. 개요

### LiveComponent란?

LiveComponent는 독립적인 상태를 가진 중첩 가능한 컴포넌트입니다. 부모 컴포넌트 내에서 렌더링되지만, 자체 상태 관리와 이벤트 처리를 수행합니다.

### Component vs LiveComponent

| 특성 | Component | LiveComponent |
|------|-----------|---------------|
| 상태 | 독립 상태 | 독립 상태 |
| 중첩 | ❌ 단일 레벨 | ✅ 부모 내 중첩 |
| WebSocket | 개별 연결 | 부모 연결 공유 |
| 이벤트 | 자신에게 전달 | @myself 타겟팅 필요 |
| 업데이트 | 전체 렌더 | 부분 렌더 |

---

## 2. API 설계

### 2.1 LiveComponent 클래스

```python
from wireview import LiveComponent

class Counter(LiveComponent):
    _template_name = "counter.html"

    count: int = 0

    async def update(self, **assigns):
        """props가 변경될 때 호출됨 (mount 이후)."""
        # 기본 구현: assigns를 상태에 반영
        pass

    async def increment(self):
        self.count += 1
```

### 2.2 템플릿 태그

```html
{% load wireview %}

<!-- 부모 컴포넌트 템플릿 -->
<div {% tag_header %}>
    <h1>Dashboard</h1>

    <!-- LiveComponent 렌더링 -->
    {% live_component "Counter" id="counter-1" count=10 %}
    {% live_component "Counter" id="counter-2" count=20 %}
</div>
```

### 2.3 @myself 타겟팅

LiveComponent 내부 이벤트는 `@myself`로 타겟팅해야 합니다:

```html
<!-- counter.html -->
{% load wireview %}
<div {% tag_header %}>
    <span>{{ count }}</span>
    <button {% on "click" "increment" target="@myself" %}>+1</button>
</div>
```

`target="@myself"`가 없으면 이벤트가 부모 컴포넌트로 전달됩니다.

### 2.4 부모-자식 통신

#### 부모 → 자식 (send_update)

```python
class Dashboard(Component):
    async def reset_all_counters(self):
        # 특정 LiveComponent 업데이트
        await self.send_update("Counter", id="counter-1", count=0)

        # 또는 클래스로 지정
        await self.send_update(Counter, id="counter-1", count=0)
```

#### 자식 → 부모 (send_to_parent)

```python
class Counter(LiveComponent):
    async def increment(self):
        self.count += 1
        # 부모에게 알림
        await self.send_to_parent("counter_changed", count=self.count)

class Dashboard(Component):
    async def counter_changed(self, count: int):
        # 자식에서 온 메시지 처리
        self.total_count = sum(c.count for c in self.live_components.values())
```

---

## 3. 구현 상세

### 3.1 LiveComponent 클래스 구조

```python
# wireview/live_component.py

class LiveComponent(Component, public=False):
    """독립 상태를 가진 중첩 가능한 컴포넌트."""

    # 부모 컴포넌트 참조
    _parent_id: str | None = None
    _myself: str = ""  # 자기 참조 ID (템플릿에서 @myself로 사용)

    async def update(self, **assigns) -> None:
        """props가 변경될 때 호출됨.

        기본 구현은 assigns를 상태에 반영합니다.
        오버라이드하여 커스텀 로직을 추가할 수 있습니다.
        """
        for key, value in assigns.items():
            if hasattr(self, key):
                setattr(self, key, value)

    async def send_to_parent(self, event: str, **kwargs) -> None:
        """부모 컴포넌트에 이벤트 전송."""
        if self._parent_id:
            await self.wire.send_to_component(self._parent_id, event, kwargs)
```

### 3.2 ComponentRepository 확장

```python
class ComponentRepository:
    def __init__(self, ...):
        ...
        self.live_components: dict[str, dict[str, LiveComponent]] = {}
        # {parent_id: {child_id: LiveComponent}}

    def register_live_component(
        self,
        parent_id: str,
        component: LiveComponent,
    ) -> LiveComponent:
        """LiveComponent를 부모 아래에 등록."""
        component._parent_id = parent_id
        component._myself = component.id

        if parent_id not in self.live_components:
            self.live_components[parent_id] = {}
        self.live_components[parent_id][component.id] = component

        return component

    def get_live_component(
        self,
        parent_id: str,
        child_id: str,
    ) -> LiveComponent | None:
        """부모 아래의 LiveComponent 조회."""
        parent_children = self.live_components.get(parent_id, {})
        return parent_children.get(child_id)
```

### 3.3 이벤트 라우팅

```python
# consumer.py

async def command_user_event(self, id, command, target, implicit_args, explicit_args):
    """
    target이 "@myself"이면 해당 LiveComponent로 라우팅,
    그렇지 않으면 부모 컴포넌트로 라우팅.
    """
    if target and target.startswith("lc:"):
        # LiveComponent 타겟
        live_component_id = target[3:]
        component = self.repo.get_live_component_by_id(live_component_id)
    else:
        # 일반 컴포넌트 타겟
        component = self.repo.get(id)

    if component:
        await self.repo.dispatch_event(component.id, command, [], kwargs)
        await self.send_render(component)
```

### 3.4 템플릿 태그 구현

```python
# templatetags/wireview.py

@register.simple_tag(takes_context=True)
def live_component(context, _name: str, **kwargs):
    """LiveComponent 렌더링.

    Usage:
        {% live_component "Counter" id="counter-1" count=10 %}
    """
    # ID 필수
    if "id" not in kwargs:
        raise TemplateSyntaxError(
            "live_component requires an 'id' attribute"
        )

    parent: Component = context.get("this")
    repo: ComponentRepository = context.get("wireview_repository")

    # LiveComponent 빌드 및 등록
    component = repo.build_live_component(
        parent_id=parent.id,
        name=_name,
        state=kwargs,
    )

    return component._render(repo) or ""
```

### 3.5 클라이언트 측 변경

```javascript
// wireview.js

class WireviewComponent {
    // @myself 타겟 처리
    resolveTarget(target) {
        if (target === "@myself") {
            // 가장 가까운 LiveComponent의 ID 반환
            return this.findClosestLiveComponent(element);
        }
        return target;
    }
}
```

---

## 4. 렌더링 플로우

### 4.1 초기 렌더링

> 2026-09-09 개정. 구현은 [live-component-ownership.md](./live-component-ownership.md)를 따른다. 원안의
> "부모 HTML에 삽입"은 동기 템플릿 안에서 `joined()`를 기다릴 수 없어 실현되지 않았고, 그 결과가
> [live-component-lifecycle.md](./live-component-lifecycle.md)의 결함들이었다.

```
1. 부모 Component 템플릿 평가 (동기)
2. {% live_component %} 태그 만남 → LiveComponent 빌드·등록, 참조 {"c": id}만 출력
3. 부모 diff 계산
4. 이번 렌더가 이름 붙인 자식마다: 새 자식 joined(), props가 바뀐 자식 update(),
   사라진 자식 leaving() (비동기, consumer.send_render 안)
5. 훅이 돈 자식 렌더 (손자식은 재귀)
6. 부모 diff + children {id: diff}를 render 메시지 하나로 전송
7. 클라이언트: 자식 등록 → 부모 HTML 합성(참조 자리에 자식 HTML) → morph 한 번
```

HTTP 최초 렌더는 dead render라 자식을 인라인으로 그리고 `joined()`는 없다.

### 4.2 LiveComponent 업데이트

```
1. @myself 이벤트 수신
2. LiveComponent 찾기 (by ID)
3. 이벤트 핸들러 실행
4. LiveComponent만 렌더링
5. diff 전송 (부모는 그대로)
```

### 4.3 send_update 플로우

```
1. 부모에서 send_update(Counter, id="counter-1", count=0) 호출
2. LiveComponent 찾기
3. LiveComponent.update(count=0) 호출
4. LiveComponent 렌더링
5. diff 전송
```

---

## 5. 구현 단계

### Phase 1: 기본 구조
- [ ] `LiveComponent` 클래스 생성
- [ ] `{% live_component %}` 템플릿 태그
- [ ] `ComponentRepository` 확장
- [ ] 기본 테스트

### Phase 2: 이벤트 처리
- [ ] `@myself` 타겟팅 (`{% on %}` 태그 확장)
- [ ] 이벤트 라우팅 (consumer.py)
- [ ] 클라이언트 측 타겟 해석

### Phase 3: 통신
- [ ] `send_update()` 구현
- [ ] `send_to_parent()` 구현
- [ ] `update()` 콜백

### Phase 4: 최적화
- [ ] 부분 렌더링 (LiveComponent만)
- [ ] update_many 배치 처리
- [ ] 메모리 관리

---

## 6. 제한사항 및 고려사항

### Django/Python 한계
- Python은 Elixir처럼 프로세스 격리가 없음
- 모든 컴포넌트가 같은 WebSocket 연결 공유
- GIL로 인한 동시성 제한

### 설계 결정
1. **ID 필수**: 모든 LiveComponent는 고유 ID 필요
2. **단일 레벨 중첩**: LiveComponent 내부에 LiveComponent는 지원하지 않음 (v1)
3. **명시적 타겟팅**: `@myself` 없으면 부모로 전달

### 성능 고려
- LiveComponent가 많으면 메모리 사용량 증가
- 각 LiveComponent 상태가 서버에 유지됨
- 적절한 `_temporary_assigns` 사용 권장

---

## 7. 참고 자료

- [Phoenix LiveComponent](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveComponent.html)
- [LiveComponent Guide](https://hexdocs.pm/phoenix_live_view/live-components.html)

---

*작성일: 2025-12-09*
