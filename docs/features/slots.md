# Slots - 컴포넌트 콘텐츠 합성

> Phoenix LiveView 스타일의 슬롯 시스템

---

## 개요

Slots는 컴포넌트에 콘텐츠를 유연하게 전달하는 기능입니다. 재사용 가능한 레이아웃 컴포넌트(Card, Modal, Tabs 등)를 만들 때 필수적입니다.

```html
{% component_block "Card" %}
    {% fill header %}<h1>제목</h1>{% endfill %}
    <p>본문 내용</p>
    {% fill footer %}<button>저장</button>{% endfill %}
{% endcomponent %}
```

---

## 기본 사용법

### 1. 컴포넌트 클래스 정의

```python
# myapp/components.py
from wireview.component import Component

class Card(Component):
    _template_name = "myapp/card.html"
    _slots = {
        "header": {"required": False, "doc": "카드 헤더 영역"},
        "footer": {"required": False, "doc": "카드 푸터 영역"},
    }

    title: str = ""
    variant: str = "default"  # default, primary, danger
```

### 2. 컴포넌트 템플릿 작성

```html
<!-- templates/myapp/card.html -->
{% load wireview %}
<div {% tag_header %} class="card card-{{ variant }}">
    {% if slots.header %}
        <header class="card-header">
            {% render_slot "header" %}
        </header>
    {% elif title %}
        <header class="card-header">
            <h3>{{ title }}</h3>
        </header>
    {% endif %}

    <div class="card-body">
        {% render_slot %}
    </div>

    {% if slots.footer %}
        <footer class="card-footer">
            {% render_slot "footer" %}
        </footer>
    {% endif %}
</div>
```

### 3. 컴포넌트 사용

```html
<!-- templates/myapp/page.html -->
{% load wireview %}

{% component_block "Card" variant="primary" %}
    {% fill header %}
        <h1>{{ page_title }}</h1>
        <span class="badge">New</span>
    {% endfill %}

    <p>카드 본문 내용입니다.</p>
    <p>여러 줄도 가능합니다.</p>

    {% fill footer %}
        <button class="btn btn-primary">저장</button>
        <button class="btn">취소</button>
    {% endfill %}
{% endcomponent %}
```

---

## Template Tags 레퍼런스

### `{% component_block %}`

슬롯을 지원하는 블록 형태의 컴포넌트 태그입니다.

```html
{% component_block "ComponentName" attr1=value1 attr2=value2 %}
    ...content...
{% endcomponent %}
```

**파라미터:**
- 첫 번째 인자: 컴포넌트 이름 (문자열)
- 나머지: 컴포넌트 속성 (key=value 형태)

### `{% live_component_block %}`

LiveComponent용 블록 태그입니다. 슬롯 규칙은 같고 `id`가 필수이며 `{% endlive_component %}`로
닫습니다. 슬롯 내용은 자식의 렌더에만 들어가고 부모 diff에는 참조만 남습니다. 자세한 것은
[LiveComponent](./live-component.md#-live_component_block-).

```html
{% live_component_block "Modal" id="m1" %}
    {% fill header %}<h2>{{ page_title }}</h2>{% endfill %}
    본문
{% endlive_component %}
```

### `{% fill %}`

부모 컴포넌트의 슬롯에 콘텐츠를 전달합니다.

```html
{% fill slotname %}
    ...content...
{% endfill %}
```

```html
{% fill slotname let:var1 let:var2 %}
    ...content using var1 and var2...
{% endfill %}
```

**파라미터:**
- 첫 번째 인자: 슬롯 이름 (따옴표 있거나 없어도 됨)
- `let:varname`: 컴포넌트에서 전달받을 변수 바인딩

### `{% render_slot %}`

컴포넌트 템플릿에서 슬롯 콘텐츠를 렌더링합니다.

```html
{% render_slot %}                           {# 기본 슬롯 #}
{% render_slot "header" %}                  {# 명명된 슬롯 #}
{% render_slot "item" item=obj index=i %}   {# 변수 전달 #}
```

---

## 고급 기능

### let: 변수 바인딩

컴포넌트에서 슬롯으로 변수를 전달할 수 있습니다. 리스트 렌더링에 유용합니다.

**컴포넌트 정의:**

```python
class ProductList(Component):
    _template_name = "products/list.html"
    _slots = {
        "item": {"required": False, "doc": "각 상품 아이템 템플릿"},
    }

    products: list[dict] = []
```

**컴포넌트 템플릿:**

```html
<!-- templates/products/list.html -->
{% load wireview %}
<ul {% tag_header %} class="product-list">
    {% if slots.item %}
        {% for product in products %}
            <li>
                {% render_slot "item" product=product index=forloop.counter %}
            </li>
        {% endfor %}
    {% else %}
        {% for product in products %}
            <li>{{ product.name }}</li>
        {% endfor %}
    {% endif %}
</ul>
```

**사용:**

```html
{% component_block "ProductList" products=products %}
    {% fill item let:product let:index %}
        <div class="product-card">
            <span class="number">{{ index }}</span>
            <h3>{{ product.name }}</h3>
            <p class="price">{{ product.price|intcomma }}원</p>
        </div>
    {% endfill %}
{% endcomponent %}
```

### Required Slots (필수 슬롯)

특정 슬롯을 필수로 지정할 수 있습니다:

```python
class Modal(Component):
    _template_name = "components/modal.html"
    _slots = {
        "title": {"required": True, "doc": "모달 제목 - 필수"},
        "body": {"required": False, "doc": "모달 본문"},
        "actions": {"required": False, "doc": "액션 버튼"},
    }

    is_open: bool = False
```

필수 슬롯을 제공하지 않으면 에러가 발생합니다:

```
TemplateSyntaxError: Component 'Modal' requires slot 'title' (모달 제목 - 필수).
Add: {% fill title %}...{% endfill %}
```

### 슬롯 존재 여부 확인

```html
{% if slots %}
    {# 슬롯이 하나라도 전달되었는지 확인 #}
{% endif %}

{% if slots.header %}
    <header>{% render_slot "header" %}</header>
{% endif %}

{% if slots.footer %}
    <footer>{% render_slot "footer" %}</footer>
{% else %}
    <footer>기본 푸터</footer>
{% endif %}
```

### 중첩 컴포넌트

컴포넌트를 중첩해서 사용할 수 있습니다:

```html
{% component_block "Card" %}
    {% fill header %}카드 제목{% endfill %}

    {% component_block "Alert" variant="info" %}
        {% fill icon %}ℹ️{% endfill %}
        알림 메시지 내용
    {% endcomponent %}

    {% fill footer %}
        <button>확인</button>
    {% endfill %}
{% endcomponent %}
```

---

## 컨텍스트 접근

### 부모 컨텍스트 접근

슬롯 콘텐츠는 부모 템플릿의 변수에 접근할 수 있습니다:

```html
<!-- page.html -->
{% with user=request.user %}
    {% component_block "Card" %}
        {% fill header %}
            {{ user.username }}님의 프로필
        {% endfill %}
    {% endcomponent %}
{% endwith %}
```

### 컴포넌트 속성 접근

슬롯 콘텐츠에서는 컴포넌트의 속성(예: `this.title`)에 직접 접근할 수 없습니다. 대신:

1. 부모 템플릿의 변수를 사용하거나
2. `let:` 바인딩을 통해 컴포넌트가 전달하는 변수를 사용하세요.

---

## 기존 API와의 호환성

### 심플 태그 (변경 없음)

```html
{% component "Counter" count=10 %}
```

슬롯이 필요 없는 컴포넌트는 기존 심플 태그를 계속 사용할 수 있습니다.

### 심플/블록 혼용

```html
{% component "Alert" message="간단한 알림" %}

{% component_block "Card" %}
    {% fill header %}복잡한 헤더{% endfill %}
    복잡한 콘텐츠
{% endcomponent %}
```

---

## 실전 예제

### Tab 컴포넌트

```python
class Tabs(Component):
    _template_name = "components/tabs.html"
    _slots = {
        "tab": {"required": True, "doc": "탭 버튼들"},
        "panel": {"required": True, "doc": "탭 패널들"},
    }

    active_tab: str = ""
```

```html
<!-- templates/components/tabs.html -->
{% load wireview %}
<div {% tag_header %} class="tabs">
    <div class="tab-list" role="tablist">
        {% render_slot "tab" %}
    </div>
    <div class="tab-panels">
        {% render_slot "panel" %}
    </div>
</div>
```

```html
{% component_block "Tabs" active_tab="tab1" %}
    {% fill tab %}
        <button role="tab" {% on "click" "set_tab" tab="tab1" %}>Tab 1</button>
        <button role="tab" {% on "click" "set_tab" tab="tab2" %}>Tab 2</button>
    {% endfill %}

    {% fill panel %}
        <div role="tabpanel" id="tab1">패널 1 내용</div>
        <div role="tabpanel" id="tab2">패널 2 내용</div>
    {% endfill %}
{% endcomponent %}
```

### Accordion 컴포넌트

```python
class Accordion(Component):
    _template_name = "components/accordion.html"
    _slots = {
        "item": {"required": True, "doc": "아코디언 아이템"},
    }

    items: list[dict] = []  # [{"id": "1", "title": "...", "open": True}]
```

```html
<!-- templates/components/accordion.html -->
{% load wireview %}
<div {% tag_header %} class="accordion">
    {% for item in items %}
        <div class="accordion-item">
            <h3>
                <button {% on "click" "toggle" item_id=item.id %}>
                    {{ item.title }}
                </button>
            </h3>
            {% if item.open %}
                <div class="accordion-content">
                    {% render_slot "item" item=item %}
                </div>
            {% endif %}
        </div>
    {% endfor %}
</div>
```

---

## 문제 해결

### "Component 'X' requires slot 'Y'" 에러

필수 슬롯이 누락되었습니다. `{% fill slotname %}...{% endfill %}`을 추가하세요.

### 자기 이벤트로 재렌더된 뒤 슬롯이 비었다

이전 버전의 한계였습니다. 중첩 컴포넌트가 자기 이벤트로 다시 렌더될 때 부모가 넘긴 슬롯을 잃었습니다.
지금은 부모가 넘긴 슬롯 내용을 컴포넌트가 기억하므로(`wire.slots`) 자기 렌더에서도 유지됩니다.

### 슬롯이 렌더링되지 않음

1. 컴포넌트 템플릿에 `{% render_slot "slotname" %}`이 있는지 확인
2. `{% if slots.slotname %}` 조건이 올바른지 확인
3. 슬롯 이름이 정확히 일치하는지 확인

### 변수가 undefined로 표시됨

`let:` 바인딩을 사용할 때:
1. `{% render_slot "name" var=value %}`에서 변수를 전달하는지 확인
2. `{% fill name let:var %}`에서 `let:` 선언이 있는지 확인

---

## 참고

- [Phoenix LiveView Slots](https://hexdocs.pm/phoenix_live_view/Phoenix.Component.html#module-slots)
- [Issue #50 - GAP-002](https://github.com/itda-work/django-wireview/issues/50)

---

*마지막 업데이트: 2025-12-09*
