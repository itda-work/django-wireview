# Function Components

> 상태 없는 재사용 가능한 컴포넌트 함수

---

## 개요

Function Components는 상태 관리가 필요 없는 간단한 UI 요소를 위한 경량 컴포넌트입니다.
일반 Component와 달리 WebSocket 연결이나 실시간 업데이트가 필요하지 않은 경우에 적합합니다.

**사용 사례:**
- UI 프리미티브 (버튼, 아이콘, 배지)
- 레이아웃 헬퍼 (카드, 그리드, 컨테이너)
- 상태가 필요 없는 재사용 요소

```python
from django.utils.html import format_html
from wireview import function_component

@function_component
def button(text: str, variant: str = "primary"):
    return format_html('<button class="btn btn-{}">{}</button>', variant, text)
```

```html
{% load wireview %}
{% func "button" text="Click me" variant="danger" %}
```

---

## 기본 사용법

### 1. 인라인 컴포넌트

가장 간단한 형태로, 함수가 직접 HTML 문자열을 반환합니다.

**반환한 문자열은 이스케이프 없이 그대로 출력됩니다.** 이스케이프는 함수의 책임입니다.
인자를 f-string으로 끼워 넣으면 `text="<script>…</script>"`가 그대로 페이지에 들어갑니다(XSS).
값은 `django.utils.html.format_html`로 넣습니다 — 자리표시자 `{}`에 들어가는 값만 이스케이프하고,
이미 안전한 값(`format_html`의 결과, `mark_safe`)은 그대로 둡니다. 마크업이 길어지면 템플릿 기반 컴포넌트를
씁니다. 템플릿의 `{{ }}`는 자동으로 이스케이프됩니다.

```python
# myapp/components.py
from django.utils.html import format_html
from wireview import function_component

@function_component
def icon(name: str, size: int = 24):
    """SVG 아이콘 컴포넌트."""
    return format_html(
        '<svg class="icon icon-{0}" width="{1}" height="{1}"><use href="#icon-{0}"></use></svg>',
        name,
        size,
    )

@function_component
def badge(text: str, color: str = "gray"):
    """상태 배지 컴포넌트."""
    return format_html('<span class="badge bg-{}">{}</span>', color, text)
```

템플릿에서 사용:

```html
{% load wireview %}

{% func "icon" name="check" size=32 %}
{% func "badge" text="New" color="green" %}
```

### 2. 템플릿 기반 컴포넌트

복잡한 HTML 구조는 템플릿 파일을 사용합니다.

```python
# myapp/components.py
from wireview import function_component

@function_component(template="myapp/components/alert.html")
def alert(message: str, type: str = "info", dismissible: bool = False):
    """알림 메시지 컴포넌트."""
    return {
        "message": message,
        "type": type,
        "dismissible": dismissible,
    }
```

```html
<!-- templates/myapp/components/alert.html -->
<div class="alert alert-{{ type }}{% if dismissible %} alert-dismissible{% endif %}" role="alert">
  {{ message }}
  {% if dismissible %}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
  {% endif %}
</div>
```

템플릿에서 사용:

```html
{% func "alert" message="저장되었습니다!" type="success" dismissible=True %}
```

---

## 슬롯 지원

Function Components도 슬롯을 통해 콘텐츠를 주입받을 수 있습니다.

### 슬롯 정의

```python
@function_component(
    template="components/card.html",
    slots={
        "header": {"required": False, "doc": "카드 헤더"},
        "footer": {"required": False, "doc": "카드 푸터"},
    }
)
def card(title: str = "", variant: str = "default"):
    return {"title": title, "variant": variant}
```

```html
<!-- templates/components/card.html -->
{% load wireview %}
<div class="card card-{{ variant }}">
  {% if slots.header %}
    <div class="card-header">{% render_slot "header" %}</div>
  {% elif title %}
    <div class="card-header">{{ title }}</div>
  {% endif %}

  <div class="card-body">
    {% render_slot %}
  </div>

  {% if slots.footer %}
    <div class="card-footer">{% render_slot "footer" %}</div>
  {% endif %}
</div>
```

### 슬롯 사용

```html
{% load wireview %}

{% func_block "card" variant="primary" %}
  {% fill header %}
    <h3>커스텀 헤더</h3>
  {% endfill %}

  <p>카드 본문 내용입니다.</p>

  {% fill footer %}
    <button class="btn btn-primary">저장</button>
  {% endfill %}
{% endfunc %}
```

### 필수 슬롯

```python
@function_component(
    template="components/modal.html",
    slots={
        "title": {"required": True, "doc": "모달 제목 - 필수"},
        "body": {"required": False},
        "actions": {"required": False},
    }
)
def modal(is_open: bool = False):
    return {"is_open": is_open}
```

필수 슬롯이 누락되면 명확한 에러 메시지가 표시됩니다:

```
TemplateSyntaxError: Function component 'modal' requires slot 'title' (모달 제목 - 필수).
Add: {% fill title %}...{% endfill %}
```

---

## 타입 변환

템플릿에서 전달되는 값은 자동으로 타입 변환됩니다.

```python
@function_component
def avatar(src: str, size: int = 40, rounded: bool = True):
    shape = "rounded-circle" if rounded else ""
    return format_html('<img src="{}" width="{}" class="avatar {}">', src, size, shape)
```

```html
<!-- 문자열 "48"이 int 48로 변환됩니다 -->
{% func "avatar" src="/img/user.jpg" size="48" %}

<!-- 문자열 "false"가 bool False로 변환됩니다 -->
{% func "avatar" src="/img/user.jpg" rounded="false" %}
```

**Bool 변환 규칙:**
- True: `"true"`, `"1"`, `"yes"`, 비어있지 않은 문자열
- False: `"false"`, `"0"`, `"no"`, `""`

---

## 컴포넌트 네이밍

### 기본 이름

함수 이름이 컴포넌트 이름이 됩니다:

```python
@function_component
def my_button(text: str):
    return format_html("<button>{}</button>", text)

# 템플릿에서: {% func "my_button" text="Click" %}
```

### 커스텀 이름

`name` 파라미터로 별도 이름 지정:

```python
@function_component(name="btn")
def create_button(text: str):
    return format_html("<button>{}</button>", text)

# 템플릿에서: {% func "btn" text="Click" %}
```

### 모듈 경로

이름 충돌 방지를 위해 모듈 경로 사용:

```python
# myapp/components.py
@function_component(name="myapp.button")
def button(text: str):
    return format_html("<button>{}</button>", text)

# 템플릿에서: {% func "myapp.button" text="Click" %}
```

---

## Component vs Function Component

| 특성 | Component | Function Component |
|------|-----------|-------------------|
| 상태 관리 | ✅ 있음 | ❌ 없음 |
| WebSocket | ✅ 실시간 업데이트 | ❌ 정적 렌더링 |
| 이벤트 핸들러 | ✅ `{% on %}` 지원 | ❌ 불가 |
| 슬롯 | ✅ 지원 | ✅ 지원 |
| 성능 | 무거움 (상태 직렬화) | 가벼움 |
| 용도 | 인터랙티브 UI | 정적 UI 조각 |

**선택 가이드:**
- 사용자 입력에 반응해야 함 → **Component**
- 서버 데이터를 실시간으로 표시 → **Component**
- 단순 레이아웃/스타일링 → **Function Component**
- 재사용 UI 조각 → **Function Component**

---

## 예제

### 버튼 시스템

```python
@function_component
def button(
    text: str,
    variant: str = "primary",
    size: str = "md",
    disabled: bool = False,
    type: str = "button",
):
    classes = f"btn btn-{variant} btn-{size}"
    disabled_attr = " disabled" if disabled else ""
    return format_html('<button type="{}" class="{}"{}>{}</button>', type, classes, disabled_attr, text)
```

```html
{% func "button" text="저장" variant="success" %}
{% func "button" text="삭제" variant="danger" disabled=True %}
{% func "button" text="제출" type="submit" %}
```

### 아이콘 버튼

```python
@function_component
def icon_button(icon: str, text: str = "", variant: str = "secondary"):
    icon_html = format_html('<i class="bi bi-{}"></i>', icon)
    text_html = format_html(" <span>{}</span>", text) if text else ""
    # format_html의 결과는 안전한 문자열이라 바깥 format_html이 다시 이스케이프하지 않는다
    return format_html('<button class="btn btn-{}">{}{}</button>', variant, icon_html, text_html)
```

```html
{% func "icon_button" icon="trash" text="삭제" variant="danger" %}
{% func "icon_button" icon="plus" %}  <!-- 아이콘만 -->
```

### 리스트 그룹

```python
@function_component(
    template="components/list_group.html",
    slots={"item": {"required": True, "doc": "각 아이템 템플릿"}}
)
def list_group(items: list, bordered: bool = True):
    return {"items": items, "bordered": bordered}
```

```html
<!-- templates/components/list_group.html -->
{% load wireview %}
<ul class="list-group{% if bordered %} list-group-bordered{% endif %}">
  {% for item in items %}
    <li class="list-group-item">
      {% render_slot "item" item=item index=forloop.counter %}
    </li>
  {% endfor %}
</ul>
```

```html
{% load wireview humanize %}  {# intcomma: django.contrib.humanize가 INSTALLED_APPS에 있어야 한다 #}
{% func_block "list_group" items=products %}
  {% fill item let:item let:index %}
    <span class="badge">{{ index }}</span>
    {{ item.name }} - {{ item.price|intcomma }}원
  {% endfill %}
{% endfunc %}
```

---

## API 레퍼런스

### `@function_component` 데코레이터

```python
def function_component(
    func=None,                    # 인자 없이 @function_component로 쓸 때의 함수
    *,
    name: str | None = None,      # 컴포넌트 이름 (기본: 함수 이름)
    template: str | None = None,  # 템플릿 경로
    slots: dict | None = None,    # 슬롯 정의
): ...
```

### Template Tags

#### `{% func %}`

심플 태그 - 슬롯 없이 렌더링:

```html
{% func "name" arg1=value1 arg2=value2 %}
```

#### `{% func_block %}`

블록 태그 - 슬롯 지원:

```html
{% func_block "name" arg1=value1 %}
  {% fill slotname %}...{% endfill %}
  기본 슬롯 내용
{% endfunc %}
```

### Python API

```python
from wireview import function_component
fc = get_function_component("button")
html = fc.render({"text": "Click", "variant": "primary"})

# 인수 검증
validated = fc.validate_args({"text": "Click"})
```

---

## 참고

- [Phoenix LiveView Function Components](https://hexdocs.pm/phoenix_live_view/Phoenix.Component.html)
- [Issue #46 - GAP-003](https://github.com/itda-work/django-wireview/issues/46)
- [Slots 문서](./slots.md)

---

*마지막 업데이트: 2026-10-01*
