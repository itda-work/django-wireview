# LiveComponent

> 독립적인 상태를 가진 중첩 가능한 컴포넌트

---

## 개요

LiveComponent는 부모 Component 내에서 렌더링되면서 자체 상태를 유지하는 컴포넌트입니다.
Phoenix LiveView의 LiveComponent에서 영감을 받았습니다.

**사용 사례:**
- 재사용 가능한 상태 컴포넌트 (Counter, Toggle, Modal)
- 부분 업데이트가 필요한 복잡한 UI
- 부모-자식 간 상태 분리

```python
from wireview import LiveComponent

class Counter(LiveComponent):
    _template_name = "components/counter.html"

    count: int = 0
    label: str = "Count"

    async def increment(self):
        self.count += 1
```

---

## 기본 사용법

### 1. LiveComponent 정의

```python
# myapp/components.py
from wireview import LiveComponent

class Counter(LiveComponent):
    _template_name = "myapp/counter.html"

    count: int = 0

    async def increment(self, amount: int = 1):
        self.count += amount

    async def decrement(self):
        self.count -= 1
```

### 2. LiveComponent 템플릿

```html
<!-- templates/myapp/counter.html -->
{% load wireview %}
<div {% live_tag_header %}>
    <span>{{ count }}</span>
    <button {% on "click" "decrement" myself=True %}>-</button>
    <button {% on "click" "increment" myself=True %}>+</button>
</div>
```

**중요**: LiveComponent 내부의 이벤트는 반드시 `myself=True`를 사용해야 합니다.
그렇지 않으면 이벤트가 부모 Component로 전달됩니다.

### 3. 부모 템플릿에서 사용

```html
<!-- templates/myapp/dashboard.html -->
{% load wireview %}
<div {% tag_header %}>
    <h1>Dashboard</h1>

    {% live_component "Counter" id="counter-1" count=10 %}
    {% live_component "Counter" id="counter-2" count=20 %}
</div>
```

**`id`는 필수**입니다. 각 LiveComponent는 고유한 ID를 가져야 합니다.

---

## @myself 타겟팅

LiveComponent 내부의 이벤트 핸들러는 `myself=True`를 사용합니다.

```html
<!-- myself=True 사용 (권장) -->
<button {% on "click" "save" myself=True %}>Save</button>

<!-- myself 없이 (이벤트가 부모로 전달됨) -->
<button {% on "click" "save" %}>Save</button>
```

### 동작 방식

| myself | 타겟 |
|:------:|------|
| `True` | 현재 LiveComponent |
| `False` / 없음 | 가장 가까운 부모 Component |

---

## 부모-자식 통신

### 부모 → 자식 (send_update)

부모 Component에서 자식 LiveComponent의 상태를 업데이트합니다.

```python
class Dashboard(Component):
    _template_name = "myapp/dashboard.html"

    async def reset_all(self):
        """모든 카운터를 0으로 리셋."""
        await self.send_update("counter-1", count=0)
        await self.send_update("counter-2", count=0)

    async def set_counter_value(self, counter_id: str, value: int):
        """특정 카운터 값 설정."""
        await self.send_update(counter_id, count=value)
```

### 자식 → 부모 (send_to_parent)

LiveComponent에서 부모에게 이벤트를 전송합니다.

```python
class Counter(LiveComponent):
    _template_name = "myapp/counter.html"
    count: int = 0

    async def increment(self):
        self.count += 1
        # 부모에게 변경 알림
        await self.send_to_parent("counter_changed", counter_id=self.id, count=self.count)

class Dashboard(Component):
    _template_name = "myapp/dashboard.html"
    total: int = 0

    async def counter_changed(self, counter_id: str, count: int):
        """자식 카운터 변경 시 호출됨."""
        # 총합 재계산 등의 로직
        self.total = await self.calculate_total()
```

---

## update() 콜백

부모에서 `send_update()`를 호출하면 LiveComponent의 `update()` 콜백이 호출됩니다.

```python
class Counter(LiveComponent):
    count: int = 0
    label: str = "Count"

    async def update(self, **assigns):
        """props가 변경될 때 호출됨."""
        old_count = self.count

        # 기본 동작: assigns를 상태에 반영
        await super().update(**assigns)

        # 커스텀 로직
        if self.count != old_count:
            await self.on_count_changed()

    async def on_count_changed(self):
        """count 변경 시 추가 로직."""
        pass
```

---

## 템플릿 태그

### {% live_component %}

LiveComponent를 렌더링합니다.

```html
{% load wireview %}
{% live_component "Counter" id="my-counter" count=10 label="My Counter" %}
```

| 파라미터 | 필수 | 설명 |
|---------|:----:|------|
| 이름 | ✅ | LiveComponent 클래스 이름 |
| id | ✅ | 고유 식별자 |
| 기타 | ❌ | 초기 props |

### {% live_tag_header %}

LiveComponent의 루트 엘리먼트에 필요한 속성을 생성합니다.

```html
<div {% live_tag_header %}>
    <!-- LiveComponent 내용 -->
</div>
```

생성되는 속성:
- `id`: 컴포넌트 ID
- `data-name`: 컴포넌트 이름
- `data-state`: 서명된 상태
- `data-is-live`: live 여부
- `data-parent`: 부모 컴포넌트 ID
- `wireview-component wireview-live`: 클래스

---

## Component vs LiveComponent

| 특성 | Component | LiveComponent |
|------|-----------|---------------|
| WebSocket | 독립 연결 | 부모 공유 |
| 상태 | 독립 | 독립 |
| 중첩 | ❌ | ✅ |
| 이벤트 타겟 | 자신 | `myself=True` 필요 |
| 부모 통신 | N/A | `send_to_parent()` |

### 선택 가이드

- **Component**: 페이지 최상위, 독립 WebSocket 필요
- **LiveComponent**: 부모 내 중첩, 재사용 가능한 상태 컴포넌트

---

## 예제

### Toggle 컴포넌트

```python
class Toggle(LiveComponent):
    _template_name = "components/toggle.html"

    is_on: bool = False
    label: str = ""

    async def toggle(self):
        self.is_on = not self.is_on
        await self.send_to_parent("toggled", toggle_id=self.id, is_on=self.is_on)
```

```html
<!-- toggle.html -->
{% load wireview %}
<label {% live_tag_header %} class="toggle">
    <input type="checkbox"
           {% if is_on %}checked{% endif %}
           {% on "change" "toggle" myself=True %}>
    <span>{{ label }}</span>
</label>
```

### 모달 컴포넌트

```python
class Modal(LiveComponent):
    _template_name = "components/modal.html"

    is_open: bool = False
    title: str = ""

    async def open(self):
        self.is_open = True

    async def close(self):
        self.is_open = False
        await self.send_to_parent("modal_closed", modal_id=self.id)
```

```html
<!-- modal.html -->
{% load wireview %}
<div {% live_tag_header %} class="modal {% if is_open %}is-open{% endif %}">
    <div class="modal-backdrop" {% on "click" "close" myself=True %}></div>
    <div class="modal-content">
        <header>
            <h2>{{ title }}</h2>
            <button {% on "click" "close" myself=True %}>&times;</button>
        </header>
        <div class="modal-body">
            {% render_slot %}
        </div>
    </div>
</div>
```

---

## 제한사항

1. **중첩 제한**: LiveComponent 내부에 다른 LiveComponent를 중첩할 수 없습니다 (v1).
2. **ID 필수**: 모든 LiveComponent는 고유한 `id`가 필요합니다.
3. **myself 필수**: LiveComponent 내부 이벤트는 `myself=True`를 사용해야 합니다.

---

## 참고

- [Phoenix LiveComponent](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveComponent.html)
- [설계 문서](../design/live-component.md)

---

*마지막 업데이트: 2025-12-09*
