# 폼 피드백

검증 오류를 **언제** 보여 줄지 통제한다. 사용자가 아직 건드리지도 않은 필드에 빨간 글씨를 띄우지
않기 위한 장치다.

동작은 셋으로 나뉜다.

1. 어떤 필드를 "건드렸는지"(touched) 추적한다 — 포커스 후 벗어났거나, 값을 바꿨거나
2. 건드린 필드의 오류만 보여 준다
3. 건드리지 않은 필드의 오류는 CSS로 감춘다

## 기본 사용

### HTML 구조

```html
<form {% on "submit" "save" %}>
  <div class="field">
    <label for="email">이메일</label>
    <input type="email" name="email" id="email" value="{{ this.email }}">

    <!-- wire-feedback-for를 단 오류 메시지 -->
    {% if this.errors.email %}
      <span wire-feedback-for="email" class="error wire-no-feedback">
        {{ this.errors.email }}
      </span>
    {% endif %}
  </div>

  <div class="field">
    <label for="password">비밀번호</label>
    <input type="password" name="password" id="password">

    {% if this.errors.password %}
      <span wire-feedback-for="password" class="error wire-no-feedback">
        {{ this.errors.password }}
      </span>
    {% endif %}
  </div>

  <button type="submit">가입</button>
</form>
```

### 필요한 CSS

건드리지 않은 필드의 오류를 감추는 규칙은 앱이 정의한다.

```css
/* 건드리기 전까지 피드백을 감춘다 */
.wire-no-feedback {
  display: none !important;
}
```

부드럽게 나타나게 하려면:

```css
[wire-feedback-for] {
  opacity: 1;
  max-height: 100px;
  transition: opacity 0.2s, max-height 0.2s;
}

.wire-no-feedback {
  opacity: 0;
  max-height: 0;
  overflow: hidden;
}
```

## 작동 방식

### 필드 추적

다음 중 하나면 그 필드는 "건드린" 것이 된다.

- 포커스했다가 벗어났다(blur)
- 값이 바뀌었다 (select, checkbox, radio)
- 그 필드가 든 폼을 제출했다 — 건드리지 않은 필드의 오류도 이때 보인다
- 코드로 직접 표시했다

건드림 상태는 필드 이름 단위이고 페이지 전체에서 하나다. 한 페이지의 두 폼에 같은 이름의 필드가 있으면
한쪽을 건드리면 다른 쪽도 건드린 것이 된다.

### 피드백 표시

`wire-feedback-for="필드이름"`을 단 엘리먼트는

1. 처음에는 `wire-no-feedback` 클래스를 갖는다 (감춰짐)
2. 대응하는 필드를 건드리면 그 클래스가 제거된다 (보임)
3. DOM이 갱신되어 새 피드백 엘리먼트가 들어와도 건드림 상태를 그대로 따른다

### 필드 이름 매칭

`wire-feedback-for`는 다음 순서로 대응 필드를 찾는다.

- 필드의 `name` 속성 (우선)
- 필드의 `id` 속성 (대체)

## 컴포넌트 예

```python
from wireview import Component


class XRegistrationForm(Component):
    class Meta:
        template_name = "registration/form.html"

    email: str = ""
    password: str = ""
    errors: dict[str, str] = {}

    @staticmethod
    def _field_error(field: str, value: str) -> str | None:
        """필드 하나의 오류 문구. 입력 중 검사와 제출이 같은 규칙을 쓴다."""
        if field == "email":
            if not value:
                return "이메일을 입력하세요"
            if "@" not in value:
                return "이메일 형식이 아닙니다"
        elif field == "password":
            if not value:
                return "비밀번호를 입력하세요"
            if len(value) < 8:
                return "비밀번호는 8자 이상이어야 합니다"
        return None

    async def validate_field(self, field: str, email: str = "", password: str = ""):
        """Validate one field as the user types."""
        # 폼 안의 필드 값은 name대로 인자에 실려 온다. field는 {% on %}이 넘긴 값이다
        self.email, self.password = email, password
        values = {"email": email, "password": password}
        errors = {name: error for name, error in self.errors.items() if name != field}
        if error := self._field_error(field, values[field]):
            errors[field] = error
        self.errors = errors

    async def save(self, email: str = "", password: str = ""):
        """Handle the form submission."""
        self.email, self.password = email, password
        values = {"email": email, "password": password}
        self.errors = {name: error for name, value in values.items() if (error := self._field_error(name, value))}

        if self.errors:
            return

        # 사용자 저장...
        await self.wire.redirect_to("/welcome")
```

템플릿:

```html
{% load wireview %}

<form {% on "submit" "save" %} class="registration-form">
  <div class="field">
    <label for="email">이메일</label>
    <input
      type="email"
      name="email"
      id="email"
      value="{{ this.email }}"
      {% on "input.debounce.300" "validate_field" field="email" %}
    >
    {% if this.errors.email %}
      <span wire-feedback-for="email" class="error wire-no-feedback">
        {{ this.errors.email }}
      </span>
    {% endif %}
  </div>

  <div class="field">
    <label for="password">비밀번호</label>
    <input
      type="password"
      name="password"
      id="password"
      {% on "input.debounce.300" "validate_field" field="password" %}
    >
    {% if this.errors.password %}
      <span wire-feedback-for="password" class="error wire-no-feedback">
        {{ this.errors.password }}
      </span>
    {% endif %}
  </div>

  <button type="submit">가입</button>
</form>
```

## JavaScript API

### 필드를 직접 건드림 처리

```javascript
// 건드린 것으로 표시한다
wireview.feedback.touch("email");

// 건드렸는지 확인한다
if (wireview.feedback.isTouched("email")) {
  console.log("Email field has been touched");
}
```

### 건드린 필드 전부 보기

```javascript
const touched = wireview.feedback.getTouched();
console.log("Touched fields:", touched);
```

### 상태 초기화

폼을 제출했거나 리셋했으면 건드림 상태를 지우고 싶을 수 있다.

```javascript
// 전부 초기화한다 (오류 메시지를 모두 감춘다)
wireview.feedback.reset();
```

훅에서 부를 수 있다.

```javascript
window.wireview.hooks.FormReset = {
  mounted() {
    this.el.addEventListener("reset", () => {
      wireview.feedback.reset();
    });
  }
};
```

## Django 폼과 함께 쓰기

```python
from django import forms
from wireview import Component


class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)


class XContactPage(Component):
    class Meta:
        template_name = "contact/page.html"

    form_data: dict = {}
    errors: dict[str, list[str]] = {}

    async def save(self, **data):
        form = ContactForm(data)

        if form.is_valid():
            # 폼 처리...
            await self.wire.redirect_to("/thank-you")
        else:
            # Django의 오류를 dict로 옮긴다
            self.errors = {
                field: list(errors)
                for field, errors in form.errors.items()
            }
```

템플릿은 필드마다 오류를 `wire-feedback-for` 아래 둔다. `this.errors`의 키가 필드 이름이다.

```html
{% load wireview %}

<form {% on "submit.prevent" "save" %}>
  <input name="email">
  {% if this.errors.email %}
    <ul wire-feedback-for="email" class="errorlist wire-no-feedback">
      {% for error in this.errors.email %}<li>{{ error }}</li>{% endfor %}
    </ul>
  {% endif %}

  <input name="name">
  {% if this.errors.name %}
    <ul wire-feedback-for="name" class="errorlist wire-no-feedback">
      {% for error in this.errors.name %}<li>{{ error }}</li>{% endfor %}
    </ul>
  {% endif %}

  <button type="submit">보내기</button>
</form>
```

이 형태는 `tests/testproj/formprobe/`가 브라우저에서 그대로 돌린다.

## 권장 사항

### 1. `wire-no-feedback`을 처음부터 붙인다

```html
<!-- 좋음: 클래스가 처음부터 있다 -->
<span wire-feedback-for="email" class="error wire-no-feedback">
  오류 메시지
</span>

<!-- 나쁨: 클래스가 없어 처음부터 보인다 -->
<span wire-feedback-for="email" class="error">
  오류 메시지
</span>
```

### 2. 필드 이름을 일치시킨다

```html
<!-- input의 name과 feedback-for가 같다 -->
<input name="user_email" ...>
<span wire-feedback-for="user_email">...</span>
```

### 3. 제출에 성공하면 상태를 초기화한다

```python
async def save(self, **data):
    if not self.errors:
        # 성공했으니 피드백 상태를 지운다
        await self.push_event("form:success", {})
```

```javascript
window.wireview.hooks.FormHandler = {
  mounted() {
    this.handleEvent("form:success", () => {
      wireview.feedback.reset();
    });
  }
};
```

### 4. 제출하면 전부 보인다

폼을 제출하면 그 폼의 필드가 모두 건드린 것이 되어, 아직 손대지 않은 필드의 오류도 보인다. Phoenix의
`phx-feedback-for`와 같은 동작이라 따로 할 일이 없다.

## CSS 예

### Bootstrap 스타일

```css
.wire-no-feedback {
  display: none !important;
}

.invalid-feedback {
  color: #dc3545;
  font-size: 0.875em;
  margin-top: 0.25rem;
}

input.is-invalid {
  border-color: #dc3545;
}
```

### Tailwind CSS

```html
<span
  wire-feedback-for="email"
  class="text-red-500 text-sm mt-1 wire-no-feedback"
>
  {{ this.errors.email }}
</span>
```

```css
.wire-no-feedback {
  @apply hidden;
}
```

## 재연결 뒤 폼 복구 (`wire-auto-recover`)

연결이 끊겼다 다시 붙으면 서버는 마지막 렌더의 상태만 가지고 있다. 서명된 `data-state`가 join에서
그것을 되돌린다. 그 사이 사용자가 폼에 입력한 것은 페이지에는 남아 있지만 서버에는 없다.
`wire-auto-recover`를 단 폼은 컴포넌트가 다시 join한 뒤 그 값을 서버에 돌려준다.

```html
<!-- 핸들러를 적으면 그 핸들러가 폼의 값을 form_data로 받는다 -->
<form wire-auto-recover="recover_draft">
  <textarea name="body"></textarea>
</form>

<!-- 값 없이 달면 폼의 change 이벤트를 다시 일으켜 폼 자신의 바인딩이 돈다 -->
<form wire-auto-recover {% on "change" "check_email" %}>
  <input name="email">
</form>
```

```python
async def recover_draft(self, form_data: dict):
    self.body = form_data.get("body", "")

async def check_email(self, email: str = ""):
    self.email = email
```

값 없이 다는 형태는 Phoenix의 `phx-auto-recover` 기본 동작과 같다. 그 폼에 `{% on "change" %}` 바인딩이
있어야 한다. 핸들러 이름을 Phoenix처럼 `validate`로 짓지 않는다 — Pydantic `BaseModel`이 가진 이름이라
클라이언트가 부를 수 없고, `{% on %}`이 렌더 때 거절한다(`manage.py check`의 `wireview.W018`이 미리 알린다). 이름이 여러 값을 가지면(체크박스) `form_data`에서 리스트다.

## Phoenix LiveView 대응

| 기능 | Phoenix LiveView | django-wireview |
|------|------------------|-----------------|
| 속성 | `phx-feedback-for` | `wire-feedback-for` |
| 감추는 클래스 | `phx-no-feedback` | `wire-no-feedback` |
| 계기 | blur, change | blur, change |
| JavaScript API | 없음 | `wireview.feedback.*` |
