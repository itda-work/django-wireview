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

- `{% wireview_header %}`가 미들웨어가 요청에 둔 nonce를 Django의 공개 함수 `django.middleware.csp.get_nonce()`로
  읽어 태그에 붙인다. 템플릿에서 따로 할 일은 없다. nonce는 처음 읽힐 때 만들어지므로, 헤더가 페이지에서 처음 읽어도
  정책에 들어간다.
- 헤더는 **정책이 nonce를 쓸 때만** nonce를 만든다(#272). 아직 아무것도 읽지 않은 nonce는 설정의 정책(`SECURE_CSP`·
  `SECURE_CSP_REPORT_ONLY`, django-csp는 `CONTENT_SECURITY_POLICY`·`CONTENT_SECURITY_POLICY_REPORT_ONLY`의 `DIRECTIVES`)
  어딘가에 nonce가 있을 때만 만들고, 없으면 태그에 아무것도 붙이지 않는다. 그래서 nonce 없는 정책의 페이지는 요청마다
  같은 HTML이고 ETag 재검증(304)이 된다. 다른 무엇이 이미 만든 nonce(템플릿의 `{{ csp_nonce }}` 등)는 정책과 무관하게
  그대로 붙인다.
- 뷰 단위 정책(Django의 `csp_override`, django-csp의 데코레이터)은 페이지를 렌더하는 동안 알 수 없다. 설정에는 nonce가
  없고 그 뷰의 정책만 nonce를 쓰면, 헤더보다 먼저 nonce를 읽는다(예: `<head>`에서 헤더 앞의 `{{ csp_nonce }}`).
- [django-csp](https://django-csp.readthedocs.io/)(`csp.middleware.CSPMiddleware`)의 nonce(`request.csp_nonce`)도
  같은 방식으로 읽는다. 정책의 디렉티브에 `NONCE`를 적으면 헤더의 태그가 허용된다.
- 헤더의 `<style>`은 연결이 끊겼을 때의 표시다. nonce가 없는 정책이면 이 스타일만 적용되지 않는다.
- 두 미들웨어 모두 없는 요청에는 nonce를 만들지 않고 태그에 아무것도 붙이지 않는다. Django 5.x에서 미들웨어 없이 헤더를
  직접 쓰는 경우, 요청에 `_csp_nonce`(문자열)를 두면 같은 방식으로 붙는다.

검증은 `tests/test_csp_e2e.py`가 한다. 인라인 허용이 없는 정책으로 페이지를 띄우고 모든 바인딩 모양을 실행한 뒤
브라우저가 거부한 것이 없는지 본다.

공개 캐시(CDN)에 담기는 페이지는 응답마다 바뀌는 nonce를 쓸 수 없다. nonce 없이 헤더의 `<style>`을 해시로 허용하는
정책은 [React 섬과 함께 쓰기](./react-islands.md#공개-캐시에-담기는-페이지)에 있다.

## 동작

### 수정자

수정자는 **왼쪽부터** 적용되는 조건과 효과의 줄이다. 예전의 인라인 코드와 같은 의미다.

- `keyup.enter.prevent`: Enter일 때만 `preventDefault()` 하고 보낸다.
- `keyup.prevent.enter`: 모든 keyup에 `preventDefault()` 하고, Enter일 때만 보낸다.
- `debounce`는 나머지를 타이머로 넘긴다. 그래서 `debounce` 뒤의 `prevent`는 너무 늦게 실행된다. `prevent`는 앞에 둔다.

수정자는 `prevent`, `stop`, `ctrl`, `alt`, `shift`, `meta`, `debounce.<ms>`, `throttle.<ms>`, `key.<이름>`, `key_code.<n>`과
단축키 `enter`, `tab`, `delete`, `backspace`, `esc`, `space`, `up`, `down`, `left`, `right`, 그리고 듣는 곳을 바꾸는 `window`와
`away`다([창과 요소 밖](#창과-요소-밖-window-away), `wireview/event_transpiler.py`의 `MODIFIERS`, 클라이언트는 `events.mjs`).
**그 밖의 이름은 `{% on %}`이 렌더 때 `ValueError`로 거절한다.** 클라이언트는 모르는
수정자를 건너뛰므로, 거절하지 않으면 `click.outside`·`click.once`·`click.self`는 조건 없는 click이 되고 `keydown.escape`는
모든 키에 반응한다. 인자가 필요한 수정자에 인자가 없거나(`keydown.key`, `input.debounce`), `debounce`·`throttle`·`key_code`의
인자가 정수가 아니어도(`input.debounce.abc`, 순서를 틀린 `keydown.debounce.enter`) 같은 `ValueError`다. 클라이언트에서는
`keydown.key`가 어떤 키에도 반응하지 않고, 지연이 `NaN`인 `debounce`·`throttle`은 아무것도 늦추지 않는다. `.once`·`.capture`·`.passive`·`.self`는 지원하지 않지만,
이 점 표기 수정자들은 Phoenix LiveView의 공개 바인딩 문법도 아니다. Phoenix 기능 갭과 혼동하지 않는다.

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

서버 핸들러를 부르는 바인딩은 그 컴포넌트가 라이브일 때만 서버로 간다. 라이브란 소켓이 열려 있고 컴포넌트가 join한
상태다. 라이브가 아닐 때는 페이지가 **한 번이라도 연결된 적이 있는가**로 나뉜다.

- **첫 연결 전**: 수정자까지 아무것도 하지 않는다. `prevent`도 실행하지 않으므로 폼은 `action`으로 제출되고 링크는
  이동한다. 번들이 로드되지 않은 페이지와 같은 결과다. 소켓이 연결되기 전의 아주 짧은 순간의 클릭도 브라우저 기본
  동작이 된다.
- **연결됐다가 끊긴 뒤**: `prevent`와 `stop`은 적용하고 서버로는 보내지 않는다(#97). 연결이 잠깐 끊긴 사이에 입력칸에서
  Enter를 눌러도 폼이 브라우저 기본 방식으로 제출돼 페이지가 다시 로드되지 않는다. 마우스는 헤더 CSS가 끊긴 컴포넌트의
  포인터 이벤트를 막지만 키보드는 막지 못하므로 이 규칙이 필요하다. join에 실패해 `wireview-error`가 붙은 컴포넌트도
  같다([errors](./errors.md)).

연결이 끊긴 동안 소켓으로 보내려던 메시지(훅의 `pushEvent`, 업로드 메시지)는 버린다. 재연결하면 모든 컴포넌트가 DOM의
상태로 다시 join하고 URL도 다시 보내므로, 옛 인스턴스를 향한 메시지는 갈 곳이 없다. 첫 연결 전에 보낸 것만 연결되면
보낸다.

`JS()` 체인은 클라이언트만의 동작이라 라이브 여부와 상관없이 실행된다.

### 전파

리스너가 `<html>`에 있으므로, 요소와 `<html>` 사이에 다른 스크립트가 단 리스너는 wireview 바인딩보다 먼저 돈다.
`.stop`은 그 위(`document`, `window`)로의 전파와 더 바깥 요소의 wireview 바인딩을 막는다. boost의 링크 가로채기는
`document`에 있으므로 `.prevent`가 먼저 적용된다.

### 창과 요소 밖 (window, away)

`window`와 `away`는 이벤트 바로 뒤에 적는다. 바인딩이 달린 요소가 아니라 창에서 듣는다(#234).

```django
<div class="menu" {% on "click.away" "close_menu" %}>…</div>
<div {% on "keydown.window.esc" "close_dialog" %}
     {% on "keydown.window.ctrl.key.k.prevent" "open_search" %}></div>
```

- **`window`**: 페이지 어디서 났든 창까지 올라온 이벤트와 창 자신의 이벤트를 듣는다. 포커스가 아무 데도 없을 때의
  키, 다른 컴포넌트의 입력칸에서 친 키, 탭이 포커스를 잃는 `blur.window`·얻는 `focus.window`가 그렇다. 요소의 `blur`처럼
  버블링하지 않는 이벤트는 창에 오지 않으므로 듣지 않는다. 요소의 바인딩이 모두 돈 **뒤에** 돌고, 그중 하나가 `.stop`했으면
  듣지 않는다. 이벤트를 막아야 하는 단축키(`Ctrl+K`)는 `.prevent`를 붙인다.
- **`away`**: 요소 **밖**에서 난 이벤트만 듣는다. 요소 자신과 자손에서 난 것은 듣지 않고, 요소가 보이지 않으면(`hidden`,
  `display: none`, 숨은 조상, 닫힌 `<dialog>`·`<details>` 안 — 브라우저의 `checkVisibility()`) 듣지 않는다. 자기 상자가 없는
  `display: contents` 래퍼는 안의 것이 보이면 보인다. 닫힌 메뉴가 페이지의 모든 클릭을 서버로 보내지
  않게 하려는 것이다. 이벤트가 페이지의 어느 리스너에 닿기 **전에**(창의 capture 단계) 누가 들을지 정한다. 그래서 메뉴를
  여는 클릭이 그 클릭으로 막 열린 메뉴를 다시 닫지 않는다. 열린 메뉴의 토글 버튼을 누르면 그것은 메뉴 밖의 클릭이라
  `away`가 닫고 버튼의 바인딩이 다시 연다 — 버튼과 메뉴를 한 요소로 감싸 그 요소에 `away`를 단다. `away`에는 `.prevent`·`.stop`을
  붙일 수 없다(`ValueError`): 그 이벤트는 밖의 요소의 것이라, 막으면 밖의 링크가 이동하지 않는다.

둘 다 바인딩이 달린 요소의 컴포넌트로 간다. 중첩 컴포넌트·LiveComponent 안에 있으면 그 컴포넌트, `myself=True`면 그
LiveComponent다. 실리는 필드도 그 컴포넌트의 것이다. 다만 이벤트가 난 곳은 그 요소가 아니므로 커밋하는 동작이 아니고(입력
중인 칸을 그 응답이 덮지 않는다) `_target`도 없다 — 바인딩이 입력칸에 달렸어도 그 칸이 아니다. 키 조건과
`debounce`·`throttle`은 요소의 바인딩과 같다.

wireview가 폼을 위해 스스로 보내는 이벤트(재연결 뒤 [폼 복구](./form-feedback.md)의 `change`, 폼 reset 뒤의 `input`·`change`)는
그 폼의 바인딩만 듣는다. 페이지에서 일어난 일이 아니므로 `change.window`·`input.away`는 듣지 않는다.

**IME가 조합 중인 키는 듣지 않는다.** `window`는 입력칸에서 친 키도 듣는다. 한글을 조합하는 동안의 키(`isComposing`)와
조합을 시작하는 키(Chromium의 `keyCode` 229)는 페이지의 단축키가 아니다. 키 조건 없는 `keydown.window`도 그 키에는 돌지 않는다.

리스너는 요소마다가 아니라 이벤트 종류마다 창에 하나이고, 이벤트가 올 때 그 바인딩을 가진 요소를 문서 순서로 찾는다. 렌더가
지운 요소는 듣지 않고, 다시 그리거나 재연결 뒤 다시 join해도 한 번만 듣는다. 해제할 것이 없다. 그 대신 그 종류의 이벤트마다
문서를 한 번 찾는다(`away`는 찾은 요소마다 보이는지도 본다). click·key·focus에는 문제가 없지만 `mousemove.away`·`scroll.window`처럼
초당 수십 번 나는 이벤트에는 걸지 않는다 — 그런 것은 [훅](./hooks.md)이 필요할 때만 리스너를 단다.

속성은 `wire-window-keydown.esc`·`wire-away-click`처럼 `wire-on-` 밖의 이름으로 렌더된다. 이 기능 전의 번들은 그 속성을
모르므로 **아무것도 하지 않는다** — `wire-on-click.away`였다면 옛 번들은 `away`를 모르는 수정자로 건너뛰어 요소 **안**의
클릭에 반응했을 것이다. 업그레이드 뒤에도 옛 번들을 쥔 탭(배포 전에 연 탭)은 새로고침할 때까지 이 바인딩을 듣지 않는다.

### 렌더가 일으킨 포커스 이벤트

렌더(morph, 스트림 연산, 컴포넌트 제거)가 포커스된 요소를 지우거나 옮기면 브라우저가 그 요소에 `blur`·`focusout`을
보낸다. 포커스한 뒤 값을 고친 칸이면 그 앞에 `change`도 보낸다(Chromium). 그 이벤트는 사용자가 한 일이 아니므로 바인딩을
실행하지 않고 [폼 피드백](./form-feedback.md)의 "건드림"으로도 치지 않는다. 렌더를 적용하는 동안 생긴
`blur`·`focusout`·`focus`·`focusin`·`change`가 모두 그렇다 — 적용은 동기라 그 사이에 사용자 입력이 끼어들 수 없다.
전에는 Escape의 `cancel_edit`이 편집 칸을 지우면 그 칸의 `{% on "blur" "save_edit" %}`가 입력한 값으로 나가, 취소가
저장이 됐다. `{% on "change" %}`도 같았다.

사용자가 렌더가 오기 전에 직접 다른 곳을 클릭한 blur는 그대로 간다. 그래서 "이미 끝난 편집"에 온 저장은 핸들러가
상태로 거른다(Todo 앱 튜토리얼의 `if not self.editing: return`).

## 주의사항

- **속성 이름은 소문자가 된다.** HTML 파서가 속성 이름을 소문자로 바꾸므로 `{% on "myEvent" … %}`는 `myevent`에만
  반응한다. 사용자 정의 이벤트는 소문자 이름을 쓴다.
- **`.inlinejs` 수정자는 없다.** 태그로는 원래 동작하지 않았고(명령을 핸들러 이름으로 먼저 확인한다), 인라인 코드는
  CSP가 막는다. 템플릿에 들어갈 클라이언트 동작은 `JS()` 체인이나 [훅](./hooks.md)으로 쓴다.
- **직접 쓴 인라인 핸들러는 여전히 막힌다.** `onclick="wireview.feedback.touch(…)"`처럼 템플릿에 직접 쓴 코드는
  wireview가 만든 것이 아니다. 훅으로 옮긴다.
- **업로드 태그의 추가 속성**은 이제 이스케이프되고, `_`는 `-`가 된다(`data_testid="x"` → `data-testid="x"`).
- **옛 번들 캐시.** 새 서버의 HTML은 옛 번들이 모르는 `wire-on-*`를 쓴다. `{% wireview_header %}`는 번들 URL의
  쿼리에 패키지 버전(`?v=1.0.0`)을 붙여, 업그레이드하면 URL이 바뀌어 캐시가 깨진다. 번들을 직접 서빙하는 경우 캐시를 비운다.

## 관련

- 설계와 버린 대안: [csp-event-binding.md](../design/csp-event-binding.md)
- [JS 명령](../implementation/js-commands.md), [훅](./hooks.md)

## Phoenix LiveView와 다른 점

[Phoenix 1.2.12 바인딩](https://phoenix-live-view.hexdocs.pm/1.2.12/bindings.html)을 그대로 옮길 수는 없다.

- `phx-click-away`는 `{% on "click.away" %}`, `phx-window-keydown`·`keyup`·`focus`·`blur`는 `{% on "keydown.window" %}`처럼
  적는다(#234). `away`는 어느 이벤트에나 붙고 보이는 요소만 듣는다. `window`는 요소의 바인딩 뒤에 돌고 그것이 `.stop`하면
  듣지 않으며, IME 조합 키를 듣지 않는다.
- `phx-mounted`·`phx-remove`·`phx-connected`·`phx-disconnected` 같은 선언적 JS 실행 속성도 없다.
  이름을 `on`에 넣어도 컴포넌트 수명주기 이벤트가 자동 발생하지 않는다. 훅의 수명주기 콜백을 쓴다.
- 숫자 debounce·throttle과 키 필터는 지원한다. `debounce="blur"`, 생략 시 기본 지연,
  다른 입력의 change·submit 시 타이머 초기화, 서로 다른 키의 throttle 예외는 구현하지 않는다.
- 키 필터는 브라우저에서 판단할 뿐 `event.key`를 서버 인자로 자동 전송하지 않는다. 이벤트별 `metadata`
  콜백이나 `phx-value-*` 수집도 없다. 정해진 값은 `on`의 키워드 인자, 동적 이벤트 정보는 훅으로 보낸다.
- `phx-track-static`처럼 배포 전후 정적 자산을 비교하는 바인딩은 없다. 번들 URL의 버전 쿼리는 새 페이지의
  캐시를 갱신할 뿐, 이미 열린 페이지의 자산 변경 감지와는 다르다.

CSP nonce와 인라인 없는 바인딩은 이미 지원한다. 위 항목들은 CSP를 완화해서 해결할 문제가 아니다.
