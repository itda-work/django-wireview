# 02. Counter 컴포넌트

이 튜토리얼에서는 카운터 컴포넌트를 만들며 wireview의 핵심 개념을 학습합니다.

## 학습 목표

- Component 클래스 구조 이해
- 이벤트 바인딩과 핸들러
- Pydantic 기반 상태 관리
- 이벤트 수정자 (modifiers)
- URL 상태 저장

## 1. 기본 카운터

### 컴포넌트 정의

`myapp/live.py`:

```python
from wireview.component import Component


class XCounter(Component):
    """숫자를 증가/감소시키는 카운터 컴포넌트"""

    _template_name = 'myapp/counter.html'

    count: int = 0

    async def increment(self):
        """카운터 증가"""
        self.count += 1

    async def decrement(self):
        """카운터 감소"""
        self.count -= 1
```

### 템플릿

`myapp/templates/myapp/counter.html`:

```html
{% load wireview %}
<div {% tag_header %} class="counter">
  <h2>Count: {{ count }}</h2>

  <div class="buttons">
    <button {% on "click" "decrement" %}>-</button>
    <button {% on "click" "increment" %}>+</button>
  </div>
</div>
```

### 사용

```html
{% load wireview %}
{% component 'XCounter' %}
{% component 'XCounter' count=100 %}  <!-- 초기값 지정 -->
```

## 2. 이벤트 인자 전달

핸들러에 인자를 전달할 수 있습니다:

### 컴포넌트

```python
class XCounter(Component):
    _template_name = 'myapp/counter.html'

    count: int = 0

    async def change_by(self, amount: int):
        """지정된 양만큼 변경"""
        self.count += amount

    async def set_to(self, value: int):
        """특정 값으로 설정"""
        self.count = value
```

### 템플릿

```html
{% load wireview %}
<div {% tag_header %} class="counter">
  <h2>Count: {{ count }}</h2>

  <div class="buttons">
    <button {% on "click" "change_by" amount=-10 %}>-10</button>
    <button {% on "click" "change_by" amount=-1 %}>-1</button>
    <button {% on "click" "change_by" amount=1 %}>+1</button>
    <button {% on "click" "change_by" amount=10 %}>+10</button>
  </div>

  <button {% on "click" "set_to" value=0 %}>Reset</button>
</div>
```

**타입 힌트 중요성:**
- `amount: int`로 선언하면 문자열 "10"이 자동으로 정수 10으로 변환됩니다
- Pydantic이 자동 타입 변환 및 검증을 수행합니다

## 3. 이벤트 수정자

이벤트 수정자로 이벤트 처리를 세밀하게 제어합니다.

### 기본 수정자

```html
<!-- 기본 클릭 -->
<button {% on "click" "submit" %}>Submit</button>

<!-- preventDefault() 호출 -->
<button {% on "click.prevent" "submit" %}>Submit</button>

<!-- stopPropagation() 호출 -->
<button {% on "click.stop" "submit" %}>Submit</button>

<!-- 둘 다 -->
<button {% on "click.prevent.stop" "submit" %}>Submit</button>
```

### 키보드 수정자

```html
<!-- Enter 키에만 반응 -->
<input {% on "keypress.enter" "search" %}>

<!-- Ctrl+Enter -->
<textarea {% on "keypress.ctrl.enter" "submit" %}></textarea>

<!-- 특정 키 -->
<input {% on "keydown.key.escape" "cancel" %}>
```

### 디바운스와 쓰로틀

```html
<!-- 300ms 디바운스 - 입력이 멈춘 후 실행 -->
<input {% on "input.debounce.300" "search" %}>

<!-- 100ms 쓰로틀 - 100ms마다 최대 1회 실행 -->
<div {% on "scroll.throttle.100" "on_scroll" %}>
```

### 조합 예제

```html
<!-- Enter 키로 검색, 기본 동작 방지, 300ms 디바운스 -->
<input
  name="query"
  {% on "keypress.enter.prevent.debounce.300" "search" %}
>
```

## 4. 폼 입력 처리

### 암시적 인자

폼 요소의 값은 자동으로 핸들러에 전달됩니다:

```html
{% load wireview %}
<div {% tag_header %}>
  <input name="amount" type="number" value="1">
  <button {% on "click" "change_by" %}>Add</button>
</div>
```

```python
async def change_by(self, amount: int):
    # input[name="amount"]의 값이 자동으로 전달됨
    self.count += amount
```

### 명시적 인자 우선

명시적으로 전달한 인자가 암시적 인자보다 우선합니다:

```html
<input name="amount" type="number" value="1">
<button {% on "click" "change_by" amount=5 %}>Add 5</button>
<!-- amount=5가 사용됨 -->
```

## 5. URL 상태 저장

카운터 값을 URL에 저장하여 새로고침해도 유지되게 합니다:

### 컴포넌트

```python
from wireview.component import Component
from wireview.core.meta import WireviewMeta


class XCounter(Component):
    _template_name = 'myapp/counter.html'

    count: int = 0

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        # URL에서 count 파라미터 읽기
        kwargs.setdefault("count", wire.params.get("count", 0))
        return cls(wire=wire, **kwargs)

    async def change_by(self, amount: int):
        self.count += amount
        # URL 쿼리 파라미터 업데이트
        self.wire.params["count"] = self.count

    async def set_to(self, value: int):
        self.count = value
        self.wire.params["count"] = self.count
```

### 동작

1. 초기 로드: URL의 `?count=X` 파라미터로 초기화
2. 값 변경: URL이 자동으로 업데이트 (`?count=5`)
3. 새로고침: URL에서 값을 복원

### 복잡한 값 저장

리스트나 딕셔너리는 `.json` 접미사를 사용합니다:

```python
# 리스트 저장
self.wire.params["items.json"] = [1, 2, 3]

# 읽기
items = self.wire.params.get("items.json", [])
```

## 6. CSS 스타일링

### 로딩 상태

서버 요청 중 자동으로 추가되는 CSS 클래스:

```css
/* 요청 중인 모든 요소 */
.wireview-loading {
  opacity: 0.6;
  pointer-events: none;
}

/* 클릭 이벤트 요청 중 */
.wireview-click-loading {
  cursor: wait;
}

/* 제출 이벤트 요청 중 */
.wireview-submit-loading {
  cursor: wait;
}
```

### 조건부 클래스

```html
{% load wireview %}
<div {% tag_header %}>
  <span {% class {'positive': count > 0, 'negative': count < 0, 'zero': count == 0} %}>
    {{ count }}
  </span>
</div>
```

## 7. 완성된 예제

### 컴포넌트 (live.py)

```python
from wireview.component import Component
from wireview.core.meta import WireviewMeta


class XCounter(Component):
    """기능이 풍부한 카운터 컴포넌트"""

    _template_name = 'myapp/counter.html'

    count: int = 0
    step: int = 1
    min_value: int | None = None
    max_value: int | None = None

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        # URL에서 상태 복원
        kwargs.setdefault("count", wire.params.get("count", 0))
        return cls(wire=wire, **kwargs)

    async def increment(self):
        new_value = self.count + self.step
        if self.max_value is None or new_value <= self.max_value:
            self.count = new_value
            self._sync_url()

    async def decrement(self):
        new_value = self.count - self.step
        if self.min_value is None or new_value >= self.min_value:
            self.count = new_value
            self._sync_url()

    async def set_to(self, value: int):
        if self.min_value is not None:
            value = max(value, self.min_value)
        if self.max_value is not None:
            value = min(value, self.max_value)
        self.count = value
        self._sync_url()

    async def set_step(self, step: int):
        self.step = max(1, step)

    def _sync_url(self):
        self.wire.params["count"] = self.count
```

### 템플릿 (counter.html)

```html
{% load wireview %}
<div {% tag_header %} class="counter-widget">
  <div class="counter-display">
    <span {% class {'positive': count > 0, 'negative': count < 0, 'zero': count == 0} %}>
      {{ count }}
    </span>
  </div>

  <div class="counter-controls">
    <button
      {% on "click" "decrement" %}
      {% cond {'disabled': min_value is not None and count <= min_value} %}
    >
      -{{ step }}
    </button>

    <button
      {% on "click" "increment" %}
      {% cond {'disabled': max_value is not None and count >= max_value} %}
    >
      +{{ step }}
    </button>
  </div>

  <div class="counter-settings">
    <label>
      Step:
      <input
        type="number"
        name="step"
        value="{{ step }}"
        min="1"
        {% on "change" "set_step" %}
      >
    </label>

    <button {% on "click" "set_to" value=0 %}>Reset</button>
  </div>
</div>

<style>
  .counter-widget {
    padding: 1rem;
    border: 1px solid #ddd;
    border-radius: 8px;
    max-width: 300px;
  }

  .counter-display {
    font-size: 3rem;
    text-align: center;
    margin: 1rem 0;
  }

  .counter-display .positive { color: green; }
  .counter-display .negative { color: red; }
  .counter-display .zero { color: gray; }

  .counter-controls {
    display: flex;
    gap: 1rem;
    justify-content: center;
  }

  .counter-controls button {
    padding: 0.5rem 1rem;
    font-size: 1.2rem;
    cursor: pointer;
  }

  .counter-controls button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .counter-settings {
    margin-top: 1rem;
    display: flex;
    gap: 1rem;
    align-items: center;
    justify-content: center;
  }

  .counter-settings input {
    width: 60px;
  }

  .wireview-loading {
    opacity: 0.6;
  }
</style>
```

### 사용 예

```html
{% load wireview %}

<!-- 기본 카운터 -->
{% component 'XCounter' %}

<!-- 범위 제한 카운터 -->
{% component 'XCounter' min_value=0 max_value=100 %}

<!-- 큰 스텝 카운터 -->
{% component 'XCounter' step=10 %}
```

## 연습 문제

1. **타이머 컴포넌트**: 시작/정지/리셋 버튼이 있는 타이머를 만들어보세요
2. **온도 변환기**: 섭씨/화씨 변환 컴포넌트를 만들어보세요
3. **별점 입력**: 1-5점 별점 입력 컴포넌트를 만들어보세요

## 다음 단계

카운터를 통해 기본적인 상태 관리와 이벤트 핸들링을 배웠습니다.

다음 튜토리얼에서는 실제 데이터베이스와 연동하는 Todo 앱을 만들어봅니다.

[← 이전: 01. 시작하기](01-getting-started.md) | [다음: 03. Todo 앱 →](03-todo-app.md)
