# CSP (Content Security Policy)

> 이벤트 바인딩이 인라인 스크립트가 아니라 데이터 속성이라, `'unsafe-inline'` 없는 정책과 함께 돈다.

## 개요

`{% on %}`은 `onclick="…"` 같은 인라인 이벤트 속성을 만들지 않는다. 대신 이벤트와 수정자를 이름에, 할 일을 JSON
값에 담은 속성을 렌더한다.

```html
{% on "input.debounce.300" "search" %}
{# → wire-on-input.debounce.300="{&quot;h&quot;:&quot;search&quot;}" #}
```

번들이 `<html>` 요소에 이벤트 종류마다 리스너를 하나씩 달고, 이벤트가 오면 대상에서 위로 올라가며 `wire-on-*`
속성을 찾아 실행한다. 속성에 코드가 없으므로 CSP의 `script-src-attr`에 걸릴 것이 없다. 이 지시어는 nonce를 받지
않으므로, 인라인 속성을 쓰는 한 `'unsafe-inline'` 말고는 우회가 없었다([#90](https://github.com/itda-work/django-wireview/issues/90)).

업로드 태그(`{% upload_input %}`, `{% upload_button %}`)도 같은 방식이다. `{% wireview_header %}`는 요청에 CSP
nonce가 있으면 `<style>`과 `<script>`에 붙인다.

## 정책 예

Django 6.0부터 내장된 CSP 미들웨어를 쓰면 이렇다.

```python
# settings.py
from django.utils.csp import CSP

MIDDLEWARE = [
    # ...
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.NONCE],
    "connect-src": [CSP.SELF],  # WebSocket(/__wireview__)과 업로드 엔드포인트
}
```

- `{% wireview_header %}`가 미들웨어가 요청에 둔 nonce(`request._csp_nonce`)를 읽어 태그에 붙인다. 템플릿에서
  따로 할 일은 없다.
- 헤더의 `<style>`은 연결이 끊겼을 때의 표시다. nonce가 없는 정책이면 이 스타일만 적용되지 않는다.
- Django 5.x나 미들웨어 없이 헤더를 직접 쓰는 경우, 요청에 `_csp_nonce`를 두면 같은 방식으로 붙는다.

검증은 `tests/test_csp_e2e.py`가 한다. 인라인 허용이 없는 정책으로 페이지를 띄우고 모든 바인딩 모양을 실행한 뒤
브라우저가 거부한 것이 없는지 본다.

## 동작

### 수정자

수정자는 **왼쪽부터** 적용되는 조건과 효과의 줄이다. 예전의 인라인 코드와 같은 의미다.

- `keyup.enter.prevent`: Enter일 때만 `preventDefault()` 하고 보낸다.
- `keyup.prevent.enter`: 모든 keyup에 `preventDefault()` 하고, Enter일 때만 보낸다.
- `debounce`는 나머지를 타이머로 넘긴다. 그래서 `debounce` 뒤의 `prevent`는 너무 늦게 실행된다. `prevent`는 앞에 둔다.

### 같은 이벤트를 여러 번

`keyup.enter`와 `keyup.esc`는 속성 이름이 달라서 한 요소에 함께 걸린다. 예전에는 둘 다 `onkeyup`이 되어 브라우저가
두 번째를 버렸다. 이름이 완전히 같은 두 바인딩(`{% on "click" "a" %}`와 `{% on "click" "b" %}`)은 여전히 하나만
남는다. 클라이언트 동작과 서버 호출을 함께 하려면 `JS().…push("b")`처럼 한 체인으로 묶는다.

### 대상 요소

로딩 클래스, `wire-disabled-with`, `JS()` 명령의 기준은 **바인딩이 달린 요소**다. 버튼 안의 `<span>`을 눌러도
버튼이 비활성화된다. 예전에는 `event.target`(가장 안쪽 요소)이었다.

### debounce와 throttle

타이머는 요소와 바인딩마다 하나다. 예전에는 페이지 전체에 하나라서, debounce가 걸린 두 입력칸을 번갈아 치면 서로의
전송을 지웠다.

### 라이브가 아닐 때

서버 핸들러를 부르는 바인딩은 그 컴포넌트가 라이브일 때만 실행된다. 라이브란 소켓이 열려 있고 컴포넌트가 join한
상태다. 아니면 수정자까지 아무것도 하지 않는다. `prevent`도 실행하지 않으므로 폼은 `action`으로 제출되고 링크는
이동한다. 번들이 로드되지 않은 페이지와 같은 결과다. 소켓이 연결되기 전의 아주 짧은 순간의 클릭도 브라우저 기본
동작이 된다.

`JS()` 체인은 클라이언트만의 동작이라 라이브 여부와 상관없이 실행된다.

### 전파

리스너가 `<html>`에 있으므로, 요소와 `<html>` 사이에 다른 스크립트가 단 리스너는 wireview 바인딩보다 먼저 돈다.
`.stop`은 그 위(`document`, `window`)로의 전파와 더 바깥 요소의 wireview 바인딩을 막는다. boost의 링크 가로채기는
`document`에 있으므로 `.prevent`가 먼저 적용된다.

## 주의사항

- **속성 이름은 소문자가 된다.** HTML 파서가 속성 이름을 소문자로 바꾸므로 `{% on "myEvent" … %}`는 `myevent`에만
  반응한다. 사용자 정의 이벤트는 소문자 이름을 쓴다.
- **`.inlinejs` 수정자는 없다.** 태그로는 원래 동작하지 않았고(명령을 핸들러 이름으로 먼저 확인한다), 인라인 코드는
  CSP가 막는다. 템플릿에 들어갈 클라이언트 동작은 `JS()` 체인이나 [훅](./hooks.md)으로 쓴다.
- **직접 쓴 인라인 핸들러는 여전히 막힌다.** `onclick="wireview.feedback.touch(…)"`처럼 템플릿에 직접 쓴 코드는
  wireview가 만든 것이 아니다. 훅으로 옮긴다.
- **업로드 태그의 추가 속성**은 이제 이스케이프되고, `_`는 `-`가 된다(`data_testid="x"` → `data-testid="x"`).
- **옛 번들 캐시.** 새 서버의 HTML은 옛 번들이 모르는 `wire-on-*`를 쓴다. `{% wireview_header %}`는 번들 URL의
  쿼리(`?v=2`)를 올려 캐시를 깬다. 번들을 직접 서빙하는 경우 캐시를 비운다.

## 관련

- 설계와 버린 대안: [csp-event-binding.md](../design/csp-event-binding.md)
- [JS 명령](../implementation/js-commands.md), [훅](./hooks.md)
