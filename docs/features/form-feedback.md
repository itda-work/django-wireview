# 폼 피드백

검증 오류를 **언제** 보여 줄지 통제한다. 사용자가 아직 건드리지도 않은 필드에 빨간 글씨를 띄우지
않기 위한 장치다.

동작은 셋으로 나뉜다.

1. 어떤 필드를 "건드렸는지"(touched) 추적한다 — 포커스 후 벗어났거나, 값을 바꿨거나
2. 건드린 필드의 오류만 보여 준다
3. 건드리지 않은 필드의 오류는 CSS로 감춘다

## 검증 뒤 HTTP로 제출하기

로그인처럼 Django 뷰가 세션 쿠키를 써야 하는 폼은 먼저 컴포넌트에서 검증한 다음 브라우저에 HTTP 제출을 요청할 수 있다.
폼에 `id="login"`, `method="post"`, 뷰를 가리키는 `action`, `{% csrf_token %}`, `{% on "submit.prevent" "check" %}`를 둔다.
검증 오류가 있으면 핸들러가 오류를 그린다. 성공하면 같은 핸들러에서 다음을 호출한다.

```python
await self.wire.submit_form("#login")
```

현재 컴포넌트 안에서 선택자와 일치하는 첫 폼을 제출한다. 기본 선택자는 `form`이다.
브라우저는 응답 렌더를 적용한 다음 실제 HTTP 요청을 보낸다. 제출 버튼의 이름·값과 `formaction`·`formmethod` 설정도 적용한다.
목적지는 현재 페이지와 같은 출처의 HTTP(S) URL이어야 한다. POST는 일반 Django CSRF 검사를 받으며, 뷰에서도 입력과 권한을 검증한다.
이 지시는 프로토콜 14부터 지원한다. 이전 번들은 새로 고침해서 갱신해야 한다.

제출 응답을 기다리는 동안 텍스트 입력과 textarea는 읽기 전용이 되고 버튼은 비활성화된다. 서버가 응답하거나 연결이 끊기면 원래 상태로 돌아간다.
`auto_upload=False`인 파일이 있으면 그 파일의 업로드부터 기다린다. [업로드와 폼 제출](./chunked-uploads.md)

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

- 포커스했다가 벗어났다(blur). 렌더가 포커스된 칸을 지우거나 옮겨 생긴 blur는 치지 않는다
- 값이 바뀌었다(`change`. select, checkbox, radio, 그리고 값을 고친 뒤 벗어난 칸). 렌더가 값을 고친 포커스 칸을 지울 때
  브라우저가 보내는 `change`도 치지 않는다
- 그 필드가 든 폼을 제출했다 — 건드리지 않은 필드의 오류도 이때 보인다
- 코드로 직접 표시했다

건드림은 **폼마다** 기억한다(#244). 필드는 자기 폼에서 건드린 것이 된다 — 폼 안에 있거나 `form="id"`로 그 폼에
연결된 칸이면 그 폼, 어느 폼에도 속하지 않으면 폼 밖. 한 페이지의 두 폼에 `email`이 있어도 한쪽을 건드리면 그 폼의
`email`만 건드린 것이다. 페이지에서 사라진 폼의 건드림은 함께 잊는다.

### 폼을 리셋하면

폼의 reset(`<button type="reset">`, `form.reset()`)은 칸들을 페이지가 그린 값으로 되돌린다. wireview는 이어서

1. 그 폼의 건드림을 지운다 — 그 폼의 피드백이 다시 감춰진다. 다른 폼은 그대로다
2. 그 폼에서 `input`과 `change`를 차례로 보낸다 — 폼의 `{% on "change" %}`·`{% on "input" %}` 바인딩이 되돌린 값으로
   불린다. 사용자가 칸을 되돌린 것과 같다. 이것이 없으면 컴포넌트는 리셋 전의 값을 들고 있다

reset 버튼은 이벤트에 실리지 않는다. `reset` 이벤트를 취소하면(`{% on "reset.prevent" ... %}`나 리스너의
`preventDefault()`) 둘 다 하지 않는다. 되돌리는 값은 **마지막 렌더가 그린 값**이다: 칸의 값을 상태에서 그리는
컴포넌트(`value="{{ this.title }}"`)라면 마지막으로 확정된 값이다.

### 피드백 표시

`wire-feedback-for="필드이름"`을 단 엘리먼트는

1. 처음에는 `wire-no-feedback` 클래스를 갖는다 (감춰짐)
2. 대응하는 필드를 건드리면 그 클래스가 제거된다 (보임)
3. DOM이 갱신되어 새 피드백 엘리먼트가 들어와도 건드림 상태를 그대로 따른다

폼 안의 피드백 엘리먼트는 **그 폼의** 필드를 따른다. 어느 폼에도 들지 않은 피드백 엘리먼트(폼 밖에 모아 둔 오류
요약)는 그 이름의 필드가 어디서든 건드려지면 보인다.

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

    email: str = ""  # 서명 상태에 실려 브라우저에 그대로 보인다. 비밀번호는 담지 않는다
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
        self.email = email
        values = {"email": email, "password": password}
        errors = {name: error for name, error in self.errors.items() if name != field}
        if error := self._field_error(field, values[field]):
            errors[field] = error
        self.errors = errors

    async def save(self, email: str = "", password: str = ""):
        """Handle the form submission."""
        self.email = email
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
const form = document.getElementById("signup");

// 그 폼의 email을 건드린 것으로 표시한다
wireview.feedback.touch("email", form);
// 폼을 주지 않으면 email이 있는 모든 폼과 폼 밖에서
wireview.feedback.touch("email");

// 그 폼에서 건드렸는지, 폼을 주지 않으면 어디서든 건드렸는지
if (wireview.feedback.isTouched("email", form)) {
  console.log("Email field has been touched");
}
```

`form` 자리에 `null`을 주면 어느 폼에도 속하지 않은 필드다.

### 건드린 필드 전부 보기

```javascript
const touched = wireview.feedback.getTouched(form); // 그 폼의 것, 폼을 빼면 페이지 전체
console.log("Touched fields:", touched);
```

### 상태 초기화

제출에 성공했으면 건드림 상태를 지우고 싶을 수 있다. 폼의 reset은 그 폼의 것을 스스로 지운다
([폼을 리셋하면](#폼을-리셋하면)) — 1.6까지 쓰던 `reset` 리스너 훅은 필요 없다.

```javascript
// 그 폼의 것을 초기화한다 (그 폼의 오류 메시지를 감춘다)
wireview.feedback.reset(form);
// 페이지 전체
wireview.feedback.reset();
```

## 이벤트에 실리는 칸

폼 안의 요소에서 난 이벤트(`submit`, `change`, 폼 안 버튼의 `click` 등)는 그 폼의 칸을 싣는다. 폼 밖이면 컴포넌트 안의
`name` 있는 요소 전부다. 중첩 컴포넌트의 칸은 그 컴포넌트의 것이라 빠진다. 무엇이 실리는지는 브라우저가 폼을 제출할 때와
같다(`FormData`, #244).

- **disabled 칸은 빠진다.** `disabled`인 칸, `<fieldset disabled>` 안의 칸(그 첫 `<legend>` 안은 아니다) 모두다.
- **버튼은 그 이벤트를 보낸 submit 버튼 하나만** 이름과 값이 실린다. 저장·삭제 버튼이 `name="action"`을 나눠 쓰면 누른
  쪽의 값이 온다. 제출이 아닌 이벤트(`change`, 다른 칸의 `click`)에는 버튼이 실리지 않는다. `type="reset"`·`type="button"`은
  실리지 않는다. `<input type="image">`는 `이름.x`·`이름.y`가 `"0"`으로 실린다(브라우저는 누른 자리의 좌표를 싣는다).
- **`form="id"`로 연결한 칸도 그 폼의 것이다.** 폼 밖에 둔 칸·버튼도 실리고, 폼 안에 있어도 다른 폼을 가리키는 칸은 그
  폼으로 간다. 같은 컴포넌트 안의 것만이다.
- checkbox·radio는 체크된 것만(값이 없으면 `"on"`), select multiple은 고른 값들, 같은 이름이 여럿이면 핸들러는 마지막
  값을 받는다. 점과 숫자 인덱스가 든 이름(`items.0`)은 지금처럼 서버가 풀어 준다.

`window.wireview.send(el, name, args, {submitter})`로 보낼 때는 `submitter`가 실을 버튼이다. 주지 않으면 `el`이 submit
버튼일 때 그것이다. 1.6까지는 disabled 칸과 이름 있는 버튼이 모두 실렸다 — [업그레이드 가이드](../UPGRADING.md#16에서-17로).

## 핸들러가 물을 수 있는 것

칸의 값 말고도 이벤트가 알려 주는 것이 셋 있다(1.7부터, #244). 핸들러가 **그 이름의 인자를 적었을 때만** 받는다.
`**data`로는 오지 않으므로, 받은 폼을 그대로 `Model(**data)`에 넘기는 핸들러는 그대로 둬도 된다.

| 인자 | 값 |
|------|----|
| `_target` | 이벤트가 난 칸의 이름. 칸이 아닌 곳에서 났으면(제출, 버튼 클릭, 리셋) `None` |
| `_unused` | 이 이벤트가 싣는 칸 가운데 아직 아무도 바꾸거나 떠나지 않은 것의 이름. `_target`인 칸은 빠진다 |
| `_bad_input` | 브라우저가 값으로 읽지 못한 칸의 이름. number 칸에 `1e`처럼 숫자가 아닌 것이 들어 있으면 브라우저는 `""`를 보낸다 |

```python
class Signup(Component):
    errors: dict[str, str] = {}

    async def validate(self, _target: str | None = None, _unused: list[str] = [], _bad_input: list[str] = [], **data):
        form = SignupForm(data)
        form.is_valid()
        self.errors = {
            name: (["숫자를 입력하세요."] if name in _bad_input else errors)[0]
            for name, errors in form.errors.items()
            if name not in _unused
        }
```

칸은 이벤트에 실리는 `input`·`select`·`textarea`다. hidden 칸과 버튼, disabled 칸은 아니다. 쓰였는지는
[필드 추적](#필드-추적)의 건드림과 같은 기록이고 폼마다다. 그래서 제출 뒤에는 그 폼의 `_unused`가 비고, 리셋 뒤에는 다시 전부다.
옛 클라이언트(1.6 이하의 번들로 열린 페이지)나 칸 없이 보낸 이벤트에서는 `None`과 빈 목록이 온다 — 기본값을 둔다.

테스트 헬퍼(`input()`·`change()`·`blur()`·`submit()`·`click()`)도 같은 값을 보낸다([테스트](./testing.md)).

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

    values: dict[str, str] = {}
    errors: dict[str, list[str]] = {}

    async def save(self, **data):
        form = ContactForm(data)
        # 쓴 값을 상태에 둔다. 폼이 아는 필드만 — 이벤트가 실어 온 다른 키는 버린다
        self.values = {name: str(data.get(name, "")) for name in form.fields}

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

템플릿은 입력마다 `value`를 상태에서 그리고, 필드마다 오류를 `wire-feedback-for` 아래 둔다. `this.errors`의 키가 필드 이름이다.

```html
{% load wireview %}

<form {% on "submit.prevent" "save" %}>
  <input name="email" value="{{ this.values.email }}">
  {% if this.errors.email %}
    <ul wire-feedback-for="email" class="errorlist wire-no-feedback">
      {% for error in this.errors.email %}<li>{{ error }}</li>{% endfor %}
    </ul>
  {% endif %}

  <input name="name" value="{{ this.values.name }}">
  {% if this.errors.name %}
    <ul wire-feedback-for="name" class="errorlist wire-no-feedback">
      {% for error in this.errors.name %}<li>{{ error }}</li>{% endfor %}
    </ul>
  {% endif %}

  <button type="submit">보내기</button>
</form>
```

이 형태는 `tests/testproj/formprobe/`가 브라우저에서 그대로 돌린다.

### 쓴 값을 상태에 두는 이유

**제출에 답하는 렌더는 입력의 값을 서버의 값으로 바꾼다.** 사용자가 고친 칸은 보통 렌더가 와도 그 값을 지키지만
(다른 칸의 이벤트, 브로드캐스트), 제출·change·Enter처럼 값을 확정하는 동작에 대한 답은 예외다 — 그 답이 곧 결과이기
때문이다(Enter 뒤 비워지는 할 일 입력). 판단은 `wireview/static/wireview/values.mjs`의 `keepsUserValue`에 있다. 그래서
`value=`를 그리지 않는 입력은 오류와 함께 돌아온 렌더에서 **비어 버린다** — 필수 칸을 비우고 다른 칸에 긴 글을 쓴 뒤
제출하면 그 글이 사라진다. 쓴 값을 상태(`values`)에 두고 `value="{{ this.values.name }}"`로 그리면 답의 값이 곧 쓴 값이다.
재연결 뒤의 다시 join도 서명 상태에서 그리므로 값이 남는다.

- **폼이 아는 필드만 담는다.** `**data`는 이벤트가 실어 온 모든 이름이다. `data`를 통째로 상태에 두거나
  `setattr(self, k, v)`로 펼치면, 클라이언트가 보낸 아무 키나 상태가 된다.
- **비밀번호처럼 보이면 안 되는 값은 담지 않는다.** 서명 상태는 암호화가 아니다 — 브라우저에서 그대로 읽힌다
  ([HTML Diff](./html-diff.md)). 그런 칸은 제출에 실패하면 다시 입력받는다.

회귀 테스트는 `tests/test_forms_e2e.py`의 `test_what_was_typed_stays_when_the_form_refuses_it`과 재연결 판이다.

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

폼을 제출하면 그 폼의 필드가 모두 건드린 것이 되어, 아직 손대지 않은 필드의 오류도 보인다.
이는 wireview의 클라이언트 피드백 규칙이다. Phoenix 1.2.12의 서버 측 `used_input?`와는 구분한다.

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

값 없이 다는 형태는 폼의 change를 재실행한다. Phoenix와 달리 복구하려는 폼에 속성을 명시해야 한다. 그 폼에 `{% on "change" %}` 바인딩이
있어야 한다. 핸들러 이름을 Phoenix처럼 `validate`로 짓지 않는다 — Pydantic `BaseModel`이 가진 이름이라
클라이언트가 부를 수 없고, `{% on %}`이 렌더 때 거절한다(`manage.py check`의 `wireview.W018`이 미리 알린다). 이름이 여러 값을 가지면(체크박스) `form_data`에서 리스트다.

## Phoenix LiveView 대응

[Phoenix 1.2.12 폼](https://phoenix-live-view.hexdocs.pm/1.2.12/form-bindings.html)은 `_unused_` 파라미터와
`used_input?`로 오류 표시 여부를 서버에서 판단한다. wireview는 `wire-feedback-for`·`wire-no-feedback`로 브라우저에서
표시를 제어하고, 서버에서 판단하려는 핸들러는 `_unused`를 인자로 받는다([핸들러가 물을 수 있는 것](#핸들러가-물을-수-있는-것)).
건드린 필드는 폼마다 이름으로 기억한다.

## Phoenix LiveView와 다른 점

- `{% on "change" %}`는 DOM change, 즉 보통 값 확정 시점에 반응한다. 타이핑마다 검사하려면 `input`을 바인딩한다.
  Phoenix의 `phx-change`와 이벤트 발생 시점이 같지 않다.
- 일반 이벤트는 가까운 폼 또는 컴포넌트의 이름 있는 요소를 수집한다. 개별 input 바인딩도 그 필드만 보내는 것이 아니다.
  `_target`·`_unused`·`_bad_input`은 폼 파라미터에 섞이지 않고 그 이름의 인자를 적은 핸들러만 받는다. `_target`은
  Phoenix의 경로 목록(`["user", "email"]`)이 아니라 칸의 이름 그대로(`"user.email"`)이고, `_unused`는 `_unused_<이름>`
  키가 아니라 이름 목록이다. 제출 버튼은 그 이름과 값으로 실린다([이벤트에 실리는 칸](#이벤트에-실리는-칸)).
  `{% on %}`의 `_target=`(LiveComponent 대상 지정)과 핸들러의 `_target`은 별개다.
- 중첩 이름은 wireview의 점 경로와 숫자 인덱스 규칙을 따른다. Phoenix의 `user[email]` 형식을 그대로 옮기지 않는다.
  실리는 칸은 HTML의 성공한 폼 컨트롤 규칙(`FormData`)을 따른다. 다만 폼 밖의 이벤트는 컴포넌트 안의 `name` 있는 요소를 모은다.
- `phx-trigger-action`에 해당하는 동작은 핸들러에서 `await self.wire.submit_form()`으로 요청한다.
- 복구는 `wire-auto-recover`를 단 폼의 소켓 재연결에만 적용한다. change 폼의 기본 자동 복구와 컴포넌트 오류 후
  재마운트 복구는 제공하지 않는다. `wire-auto-recover="ignore"`도 비활성화 값이 아니라 핸들러 이름으로 해석된다.
- 폼 reset 뒤에는 그 폼의 `input`·`change`를 보낸다(Phoenix처럼). 그 이벤트의 `_target`은 `None`이다.
  잘못된 number 입력은 `""`로 실리고 `_bad_input`에 이름이 오며, 그 change를 억제하지는 않는다.
- 제출 중 버튼에는 `wire-disabled-with`로 대기 문구를 표시할 수 있다. [Optimistic UI](./optimistic-ui.md)

Phoenix 1.2의 `phx-no-unused-field`는 used-input 전송을 끄는 옵션이다. wireview는 `_unused`를 폼 파라미터에
섞지 않고 인자를 적은 핸들러에만 주므로 대응 옵션이 없다. `phx-patch-focused`처럼 포커스된 폼 컨트롤에 서버 패치를
우선시키는 선택 속성도 없다. 기본 입력 보존과 IME 처리는 이미 지원한다.
