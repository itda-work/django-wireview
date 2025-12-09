# LiveComponent 구현 이슈 및 개선 계획

> 2025-12-09 코드 리뷰 결과

---

## 발견된 이슈

### 🔴 P0: Critical - `joined()` 호출되지 않음

**문제**:
LiveComponent의 `joined()` 라이프사이클 메서드가 절대 호출되지 않습니다.

**원인 분석**:

```
1. 부모 Component가 WebSocket으로 join
2. Consumer가 repo 생성 시 is_live=True 설정
3. 부모 render → {% live_component %} → build_live_component()
4. LiveComponent 생성, repo.components에 등록
5. {% live_tag_header %} 출력: data-is-live="true"
6. 클라이언트가 diff 수신, morph, joinAllComponents() 호출
7. JavaScript join() 체크: element.dataset.isLive === "false" → FALSE
8. join 메시지 전송 안 함 → joined() 호출 안 됨!
```

**영향**:
- LiveComponent에서 `joined()`에서 초기화하는 로직이 실행되지 않음
- 데이터 로딩, 구독 설정 등이 작동하지 않음

**해결 방안**:

```python
# Option A: build_live_component에서 직접 호출
def build_live_component(self, name, state, parent_id):
    ...
    component = component_class._build(...)
    component._parent_id = parent_id
    self.components[component.id] = component

    # 새로 생성된 경우에만 joined() 호출
    if not existing:
        await component.joined()  # ← 추가

    return component
```

```python
# Option B: mount() 라이프사이클 분리
class LiveComponent(Component):
    async def mount(self, **assigns):
        """첫 렌더링 전 호출 (joined 대신)"""
        pass
```

---

### 🔴 P0: Critical - 이벤트 라우팅 검증 필요

**문제**:
`myself=True` 이벤트가 실제로 LiveComponent로 라우팅되는지 검증되지 않았습니다.

**현재 구현**:

```javascript
// wireview.js
send(element, name, args, eventType) {
    const targetId = args._target;
    if (targetId) delete args._target;

    let componentId = targetId || component_el.id;
    let component = connection.components[componentId];

    if (component !== undefined) {
        component.dispatch(name, args, formScope);
    }
}
```

**의문점**:
- `connection.components[componentId]`에 LiveComponent가 등록되어 있는가?
- P0 이슈로 인해 `join()`이 호출되지 않으면 등록도 안 됨
- LiveComponent 이벤트가 부모로 전송될 수 있음

**검증 필요**:
- E2E 테스트로 실제 이벤트 플로우 확인
- LiveComponent ID가 `connection.components`에 있는지 확인

---

### 🟠 P1: LiveComponent 재등록 시 update() 미호출

**문제**:
부모가 re-render될 때 LiveComponent의 props가 변경되면 `update()`가 호출되어야 하지만, 현재는 속성만 직접 설정합니다.

**현재 코드**:
```python
# repository.py:114-121
if existing := self.components.get(component_id):
    if isinstance(existing, LiveComponent):
        # update() 없이 직접 설정
        for key, value in state.items():
            if key != "id" and key in existing.model_fields:
                setattr(existing, key, value)
        return existing
```

**해결 방안**:
```python
if existing := self.components.get(component_id):
    if isinstance(existing, LiveComponent):
        await existing.update(**state)  # update() 콜백 호출
        return existing
```

---

### 🟠 P1: parent_id 검증 미흡

**문제**:
같은 ID로 다른 부모에서 LiveComponent를 요청하면 기존 것을 반환합니다.

**시나리오**:
```html
<!-- Dashboard A -->
{% live_component "Counter" id="counter-1" %}

<!-- Dashboard B (다른 페이지) -->
{% live_component "Counter" id="counter-1" %}  <!-- 같은 ID! -->
```

**해결 방안**:
```python
if existing := self.components.get(component_id):
    if isinstance(existing, LiveComponent):
        if existing._parent_id != parent_id:
            raise ValueError(
                f"LiveComponent '{component_id}' already exists "
                f"under parent '{existing._parent_id}'"
            )
```

---

### 🟡 P2: preload 없음 (N+1 쿼리 위험)

**Phoenix**:
```elixir
def preload(list_of_assigns) do
  # 여러 LiveComponent의 데이터를 한 번에 로드
end
```

**현재**: 각 LiveComponent가 개별적으로 데이터를 로드하면 N+1 쿼리 발생

**해결 방안 (장기)**:
- `_preload` 클래스 메서드 추가
- 부모 렌더 전에 모든 LiveComponent assigns를 수집하여 preload 호출

---

### 🟡 P2: mount vs joined 혼동

**문서**:
```
Lifecycle
=========
1. **mount**: Called once when LiveComponent is first rendered
```

**실제**: `mount()` 메서드가 없고, `joined()`도 호출되지 않음

**해결 방안**:
- 문서를 현재 구현에 맞게 수정
- 또는 `mount()` 라이프사이클 추가

---

### 🟡 P2: _render_live 사용되지 않음

**코드**:
```python
def _render_live(self, repo):
    """Render for live context (within parent)."""
    return self.wire.render(self, repo)
```

**실제 사용**: `_render(repo)`가 호출됨

**해결**: 메서드 제거 또는 활용

---

## 테스트 커버리지 부족

### 현재 테스트 현황

| 영역 | 커버리지 | 상태 |
|------|---------|------|
| 등록/해결 | 6개 | ✅ |
| 필드/마커 | 5개 | ✅ |
| update() | 2개 | ✅ |
| 이벤트 핸들러 | 3개 | ✅ |
| Repository | 5개 | ✅ |
| myself 타겟팅 | 3개 | ⚠️ 단위만 |
| send_update | 2개 | ⚠️ mock만 |
| **E2E 테스트** | 0개 | ❌ |
| **joined() 호출** | 0개 | ❌ |
| **consumer 핸들러** | 0개 | ❌ |

### 필요한 E2E 테스트

```python
@pytest.mark.e2e
class TestLiveComponentE2E:
    async def test_live_component_joins_and_renders(self):
        """LiveComponent가 부모 내에서 join하고 렌더되는지"""
        pass

    async def test_myself_event_routing(self):
        """myself=True 이벤트가 LiveComponent로 라우팅되는지"""
        pass

    async def test_send_to_parent(self):
        """send_to_parent가 부모 메서드를 호출하는지"""
        pass

    async def test_send_update_rerenders(self):
        """send_update가 LiveComponent를 re-render하는지"""
        pass

    async def test_parent_rerender_preserves_state(self):
        """부모 re-render 시 LiveComponent 상태가 유지되는지"""
        pass
```

---

## 개선 로드맵

### Phase 1: 긴급 수정 (P0)

1. **build_live_component에서 joined() 호출**
   ```python
   async def build_live_component(self, name, state, parent_id):
       ...
       if not existing:
           await component.joined()
       return component
   ```

2. **E2E 테스트 추가**
   - Playwright로 실제 WebSocket 통신 테스트
   - 이벤트 라우팅 검증

### Phase 2: 안정화 (P1)

3. **update() 콜백 호출**
   - props 변경 시 update() 호출

4. **parent_id 검증**
   - 충돌 방지

5. **consumer 테스트 추가**
   - `component_update_live_component` 테스트

### Phase 3: 개선 (P2)

6. **문서/코드 정리**
   - mount vs joined 명확화
   - 미사용 메서드 정리

7. **preload 검토**
   - N+1 쿼리 해결 방안

---

## 권장 우선순위

| 우선순위 | 작업 | 예상 시간 |
|---------|------|----------|
| **P0** | joined() 호출 수정 | 1시간 |
| **P0** | E2E 테스트 추가 | 2-3시간 |
| **P1** | update() 콜백 | 30분 |
| **P1** | parent_id 검증 | 30분 |
| **P2** | 문서 정리 | 1시간 |

---

## 결론

LiveComponent의 핵심 아키텍처는 올바르게 설계되었으나, **joined() 미호출**이라는 치명적인 버그가 있습니다. 이로 인해 LiveComponent의 초기화 로직이 실행되지 않아 실제 사용 시 예상대로 작동하지 않을 수 있습니다.

E2E 테스트 없이 구현을 완료한 것이 근본 원인입니다. 앞으로는:

1. 핵심 기능 구현 시 E2E 테스트를 먼저 작성 (TDD)
2. 단위 테스트만으로는 통합 문제를 발견하기 어려움
3. Phoenix LiveView 문서를 더 면밀히 분석하여 라이프사이클 이해

이 문서를 기반으로 수정을 진행하면 안정적인 LiveComponent를 제공할 수 있습니다.
