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

`myself=True`는 이 LiveComponent를 이벤트 대상으로 명시합니다. 생략하면 클라이언트가 가장 가까운
`wireview-component` 요소, 즉 이 LiveComponent 자신을 대상으로 삼으므로 위 예제에서는 결과가 같습니다.
명시해 두면 버튼이 슬롯 등으로 다른 컴포넌트 안에 놓여도 대상이 바뀌지 않습니다.

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

`count=10` 같은 나머지 인자는 **부모가 넘기는 props**입니다. 처음 만들 때 초기값이 되고, 그 뒤로는
부모가 재렌더할 때 **직전에 넘긴 값과 달라진 것만** `update()`로 전달됩니다. 부모가 같은 값을 계속
넘기는 동안 자식이 스스로 바꾼 상태는 그대로 남습니다.

---

## 수명주기

LiveComponent는 **부모가 소유**합니다. 부모 템플릿이 이름을 붙이는 동안 살고, 부모가 더는 그리지
않으면 사라집니다. 클라이언트가 자식을 따로 join하지 않으므로 초기화 경로는 하나입니다.

| 시점 | 호출 | 비고 |
|------|------|------|
| 부모 렌더에 처음 등장 | `joined()` | 인스턴스당 한 번. 자식의 첫 HTML은 이 뒤에 렌더된다 |
| 부모가 다른 props를 넘김 | `update(**changed)` | 값이 달라진 props만. `send_update()`는 항상 호출 |
| 부모가 더는 그리지 않음 | `leaving()` | 서버가 제거한다. 조건부로 사라진 자식은 상태를 잃는다 |
| 부모가 화면에서 사라짐 (`leave`) | `leaving()` | 부모에서 자식으로 cascade |
| 연결 종료 | `leaving()` | Component와 같다 |

라이브 렌더에서 부모 diff에는 자식의 HTML이 아니라 **참조** `{"c": "counter-1"}`만 들어갑니다.
자식의 diff는 부모의 `render` 메시지 안 `children`으로 함께 오고, 클라이언트가 부모 HTML을 만들 때
참조 자리에 자식의 현재 HTML을 넣습니다. 그래서 부모가 재렌더돼도 자식 마크업은 다시 전송되지 않고,
자식만 바뀌면 자식 diff만 갑니다 ([html-diff](./html-diff.md), 설계는
[live-component-ownership](../design/live-component-ownership.md)).

**HTTP 최초 응답은 dead render**입니다. 자식이 인라인으로 그려지고 `joined()`는 호출되지 않습니다.
WebSocket이 붙으면 새 인스턴스가 만들어지고 `joined()`가 한 번 돕니다. Component와 같은 계약입니다.

---

## @myself 타겟팅

`myself=True`는 이벤트 대상을 이 LiveComponent로 고정합니다.

```html
<!-- 대상을 명시 -->
<button {% on "click" "save" myself=True %}>Save</button>

<!-- 생략: 가장 가까운 wireview-component 요소가 대상. LiveComponent 안이면 곧 자신 -->
<button {% on "click" "save" %}>Save</button>
```

### 동작 방식

| myself | 타겟 |
|:------:|------|
| `True` | 현재 LiveComponent (어디에 놓이든) |
| `False` / 없음 | 버튼을 감싸는 가장 가까운 `wireview-component` 요소 |

부모에게 알릴 일은 핸들러 안에서 `send_to_parent()`로 합니다. `myself`를 생략하는 것은 부모 통신
수단이 아닙니다.

---

## 부모-자식 통신

### 부모 → 자식 (send_update)

부모 Component에서 자식 LiveComponent의 상태를 업데이트합니다. `await`는 세션에 메시지를 넣는 데서
끝나며, 자식의 `update()`와 렌더는 현재 핸들러가 끝난 뒤 처리됩니다. 호출 직후 자식 상태를 읽어도
아직 바뀌지 않았을 수 있습니다.

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

부모가 재렌더하면서 다른 props를 넘기거나 `send_update()`를 호출하면 LiveComponent의 `update()`
콜백이 호출됩니다. 템플릿 경로에서는 값이 달라진 props만, `send_update()`에서는 넘긴 assigns 전부가
전달됩니다.

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

`update()`는 **부모가 호출하는 콜백이지 이벤트 핸들러가 아닙니다.** 오버라이드해도
클라이언트가 `{% on %}`으로 직접 부를 수 없습니다. `send_to_parent()`도 마찬가지로
서버에서만 호출합니다 — 클라이언트가 부를 수 있으면 자식이 보낸 것처럼 위장한 부모
이벤트를 만들 수 있기 때문입니다.

이벤트 핸들러로 노출되는 것은 `_`로 시작하지 않으면서 **사용자 코드에서 정의한 이름**
뿐입니다. `wireview` 패키지와 Pydantic이 소유한 이름(`mount`, `joined`, `update`,
`send_to_parent`, `model_post_init`, `model_dump` ...)은 서브클래스에서 오버라이드해도
노출되지 않습니다. 부모에 알릴 일이 있으면 사용자 핸들러 안에서 `send_to_parent()`를
호출하세요.

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
| 기타 | ❌ | 부모가 넘기는 props. 처음엔 초기값, 이후엔 달라진 것만 `update()`로 |

### {% live_component_block %}

슬롯을 전달하는 블록 형태입니다. `{% fill %}`과 `{% render_slot %}`, `let:` 바인딩은
[Slots](./slots.md)와 같습니다.

```html
{% live_component_block "Modal" id="m1" title="설정" %}
    {% fill header %}<h2>{{ page_title }}</h2>{% endfill %}
    본문은 기본 슬롯이 됩니다.
{% endlive_component %}
```

- `let:` 없는 fill과 기본 슬롯은 **부모의 컨텍스트**에서 부모 렌더 때 렌더되고, 그 텍스트가 자식에게
  전달됩니다. 부모가 재렌더해 이 텍스트가 바뀌면 자식도 다시 렌더됩니다. 바뀌지 않으면 자식은
  건드리지 않습니다. props와 같은 규칙입니다.
- `let:` fill은 자식이 렌더될 때 `{% render_slot %}`이 넘긴 값으로 렌더됩니다. 자식의 컨텍스트에서
  렌더되므로 부모 변수는 보이지 않습니다.
- 자식이 자기 이벤트로 재렌더돼도 슬롯 내용은 유지됩니다.
- `_slots`에 `required: True`로 선언한 슬롯이 빠지면 `TemplateSyntaxError`입니다.

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
- `data-state`: 서명된 상태. 재연결 때 부모의 join에 실려 자식의 상태를 복원한다
- `data-is-live`: live 여부
- `data-parent`: 부모 컴포넌트 ID
- `wireview-component`, `wireview-live`: 불리언 속성(CSS 클래스가 아니다). `wireview-live`가 있는 요소는
  클라이언트가 따로 join하지 않는다

---

## Component vs LiveComponent

한 페이지의 모든 컴포넌트는 WebSocket 연결 하나를 공유합니다. 차이는 연결이 아니라 **누가 수명을
쥐는가**입니다.

| 특성 | Component | LiveComponent |
|------|-----------|---------------|
| 수명 | 클라이언트가 join·leave | 부모가 그리는 동안 |
| 상태 | 독립 | 독립. 부모가 넘기는 props는 부모가 진실 |
| 템플릿 안 중첩 | 가능 (`{% component %}`) | 가능 (`{% live_component %}`) |
| 초기화 | 자기 join 뒤 `joined()` | 부모 렌더 뒤 `joined()`, 부모의 render 메시지에 함께 |
| 부모 통신 | 없음 | `send_to_parent()`, `send_update()` |

### 선택 가이드

- **Component**: 페이지 최상위, 또는 부모와 무관하게 스스로 join·leave해야 하는 조각
- **LiveComponent**: 부모가 만들고 props를 넘기는 재사용 가능한 상태 컴포넌트

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

```html
<!-- 부모 템플릿: 본문을 기본 슬롯으로 넘긴다 -->
{% live_component_block "Modal" id="settings-modal" title="설정" %}
    <p>{{ this.settings_help }}</p>
{% endlive_component %}
```

---

## 제한사항

1. **ID 필수**: 모든 LiveComponent는 고유한 `id`가 필요합니다. 같은 id를 다른 클래스가 쓰면 이전
   인스턴스는 `leaving()` 뒤 교체됩니다.
2. **중첩 깊이**: LiveComponent 안의 LiveComponent도 같은 절차로 초기화·렌더됩니다(손자식은 부모의
   `render` 메시지에 평면으로 함께 옵니다). 깊이는 8까지이며, 그보다 깊으면 로그를 남기고 더 그리지
   않습니다.
3. **슬롯**: 심플 태그 `{% live_component %}`는 슬롯을 전달하지 않습니다. 슬롯이 필요하면
   `{% live_component_block %}`을 씁니다.

---

## 참고

- [Phoenix LiveComponent](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveComponent.html)
- [설계 문서](../design/live-component.md)

---

*마지막 업데이트: 2026-09-09*
