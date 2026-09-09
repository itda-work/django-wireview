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
- 코드로 직접 표시했다

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
from pydantic import field_validator


class XRegistrationForm(Component):
    _template_name = "registration/form.html"

    email: str = ""
    password: str = ""
    errors: dict[str, str] = {}

    @field_validator("email")
    def validate_email(cls, v):
        if v and "@" not in v:
            raise ValueError("Invalid email address")
        return v

    async def validate_field(self, field: str, value: str):
        """Validate one field as the user types."""
        errors = {}

        if field == "email":
            if not value:
                errors["email"] = "이메일을 입력하세요"
            elif "@" not in value:
                errors["email"] = "이메일 형식이 아닙니다"
        elif field == "password":
            if len(value) < 8:
                errors["password"] = "비밀번호는 8자 이상이어야 합니다"

        self.errors = {**self.errors, **errors}
        if not errors.get(field):
            self.errors.pop(field, None)

    async def save(self, email: str, password: str):
        """Handle the form submission."""
        self.errors = {}

        if not email:
            self.errors["email"] = "이메일을 입력하세요"
        if not password:
            self.errors["password"] = "비밀번호를 입력하세요"
        elif len(password) < 8:
            self.errors["password"] = "비밀번호는 8자 이상이어야 합니다"

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
    _template_name = "contact/page.html"

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

템플릿:

```html
{% load wireview %}

<form {% on "submit" "save" %}>
  {% for field in form %}
    <div class="field">
      {{ field.label_tag }}
      {{ field }}

      {% if field.name in this.errors %}
        <ul wire-feedback-for="{{ field.name }}" class="errorlist wire-no-feedback">
          {% for error in this.errors|get_item:field.name %}
            <li>{{ error }}</li>
          {% endfor %}
        </ul>
      {% endif %}
    </div>
  {% endfor %}

  <button type="submit">보내기</button>
</form>
```

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
        await self.wire.push_event("form:success", {})
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

### 4. 제출 시에는 모든 오류를 보여 준다

제출 순간에는 건드림 여부와 무관하게 전부 보여 주고 싶을 수 있다.

```html
<button
  type="submit"
  onclick="['email', 'password', 'name'].forEach(f => wireview.feedback.touch(f))"
>
  제출
</button>
```

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

## Phoenix LiveView 대응

| 기능 | Phoenix LiveView | django-wireview |
|------|------------------|-----------------|
| 속성 | `phx-feedback-for` | `wire-feedback-for` |
| 감추는 클래스 | `phx-no-feedback` | `wire-no-feedback` |
| 계기 | blur, change | blur, change |
| JavaScript API | 없음 | `wireview.feedback.*` |
