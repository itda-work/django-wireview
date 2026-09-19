# CSP와 함께 도는 이벤트 바인딩 — 인라인 속성 대신 위임 리스너

> 2026-09-19. [#90](https://github.com/itda-work/django-wireview/issues/90) 설계 메모. 이슈가 제안한 방향(데이터
> 속성 + 위임 리스너)을 그대로 따르고, 여기서는 그 방향이 남긴 선택들과 바꾸면서 드러난 기존 결함을 적는다.
>
> **구현 완료.** 사용법은 [features/csp.md](../features/csp.md). 구현하며 §4에 결함 넷이 더 붙었다 — 그중 둘은
> 이 기능 밖에서 오래 숨어 있던 것이다(업로드 태그가 브라우저에서 한 번도 동작하지 않았다는 것, 아무것도 바꾸지 않는
> 핸들러가 버튼을 영원히 비활성으로 남긴다는 것).

---

## 0. 요약

`{% on "click" "save" %}`는 지금 `onclick="wireview.send(event.target, 'save', {}, 'click')"`를 렌더한다. 인라인 이벤트
속성은 CSP의 `script-src-attr`에 걸리고, 이 지시어는 nonce를 받지 않는다. 그래서 nonce 기반 CSP를 강제하는 순간 모든
컴포넌트의 이벤트가 죽는다. 우회는 `'unsafe-inline'`뿐이고, 그것은 속성 XSS 방어를 통째로 푼다.

바꾸면 이렇게 된다.

```html
<!-- 지금 -->
<input oninput="wireview.debounce(250)(() => {wireview.send(event.target, 'search', {}, 'input')})()">
<!-- 제안 -->
<input wire-on-input.debounce.250="{&quot;h&quot;:&quot;search&quot;}">
```

속성 이름이 템플릿의 `{% on %}` 첫 인자 그대로이고, 값은 JSON이다(핸들러 `h`, 인자 `a`, LiveComponent 대상 `t`, 또는 JS
명령 `js`). 번들은 `<html>` 요소에 이벤트 종류마다 리스너를 하나씩 달고, 이벤트가 오면 대상부터 위로 걸어 올라가며 해당
속성을 찾는다. 스크립트 코드가 속성에 없으므로 CSP는 `script-src 'self'`(또는 nonce)만으로 충분하다.

## 1. 속성의 모양

**이름에 수정자를 담는다: `wire-on-<이벤트>[.<수정자>…]`.** 값에 담는 대안(`wire-on-keyup="{…, m: ["enter"]}"`)은
한 요소에 같은 이벤트를 두 번 걸 수 없다. `keyup.enter`와 `keyup.esc`를 한 입력칸에 거는 것은 흔한데, **지금도
안 된다** — 둘 다 `onkeyup`이 되어 브라우저가 두 번째 속성을 버린다. 이름에 담으면 둘은 다른 속성이다.

- 이름에 쓸 수 있는 문자는 영문자·숫자·`_`·`.`·`:`·`-`로 제한하고, 벗어나면 템플릿 오류로 알린다. HTML 속성 이름으로
  안전하고, 사용자가 쓴 문자열이 속성 이름을 깨고 나올 수 없다.
- 값은 `DjangoJSONEncoder`로 만든 JSON을 `format_html`이 이스케이프한 것이다. `&quot;`로 부풀지만, 지금의 인라인
  코드보다 짧다(`wireview.send(event.target, …)`가 없다).

## 2. 수정자의 의미 — 지금과 같게

지금 트랜스파일러는 수정자를 **오른쪽부터 안쪽으로** 감싼다. `keyup.debounce.300.enter`는 "debounce가 바깥, 그 안에서
Enter 검사"다. 즉 왼쪽부터 차례로 실행되는 조건·효과의 줄이다. 클라이언트는 이것을 왼쪽부터 해석한다.

| 수정자 | 인자 | 동작 |
|---|---|---|
| `prevent`, `stop` | — | `preventDefault()`, 전파 중단 |
| `ctrl`, `alt`, `shift`, `meta` | — | 그 키가 눌렸을 때만 계속 |
| `key` | 다음 토큰 | `event.key`(소문자)가 같을 때만 |
| `key_code` | 다음 토큰 | `event.keyCode`가 같을 때만 |
| `enter`, `tab`, `delete`, `backspace`, `esc`, `space`, `up`, `down`, `left`, `right` | — | 해당 키일 때만 |
| `debounce`, `throttle` | 다음 토큰(ms) | 나머지를 늦추거나 거른다 |
| 그 밖 | — | 무시(지금도 무시한다) |

`debounce` 뒤에 오는 `prevent`는 지금처럼 늦게 실행되어 효과가 없다. 의미를 바꾸지 않는다 — 문서에 "prevent는
debounce보다 앞에"라고 적는다.

## 3. 위임 리스너

- **`<html>` 요소에 단다.** `document`에 달면 먼저 등록된 boost의 링크 가로채기(`wireview-boost.js`, `document`의 click,
  `defaultPrevented`를 본다)가 우리보다 먼저 돌아 `.prevent`가 늦는다. 버블 단계에서 `<html>`은 `document`보다 먼저다.
  `.stop`도 `document`와 `window`로 가는 전파를 막아 지금의 의미(요소에서 멈춤)에 가깝다.
- **버블하는 이벤트**는 대상에서 `<html>`까지 걸어 올라가며 바인딩을 실행한다(버블을 흉내 낸다). `.stop`이 실행되면
  더 위의 바인딩으로 가지 않는다. **버블하지 않는 이벤트**(`focus`, `blur`, `mouseenter` …)는 캡처 단계에서 받되
  `event.target` 자신의 바인딩만 실행한다 — 그 이벤트가 원래 닿는 곳이다.
- **어떤 이벤트 종류에 리스너를 달지**는 DOM에 있는 `wire-on-*` 속성에서 배운다. 시작할 때 한 번 훑고, 그 뒤는
  `MutationObserver`가 새 요소와 새 속성을 본다(morph, 스트림 삽입, boost 이동이 모두 여기로 온다). 한 종류에 리스너는
  하나다.
- 차이가 하나 남는다: 요소와 `<html>` 사이의 **다른 스크립트의 리스너**는 이제 우리 핸들러보다 먼저 돈다. 전에는
  인라인 핸들러가 요소에서 먼저 돌았다. `.stop`으로 그들을 막던 코드가 있다면 달라진다. 저장소 안에는 없다.

## 4. 바꾸면서 고치는 기존 결함

바인딩을 옮기면서 같은 코드에 있던 결함 셋이 드러났다. 새 구조에서는 고치는 쪽이 오히려 코드가 짧다.

1. **debounce·throttle의 타이머가 페이지 전체에 하나다.** `wireview.debounce`가 모듈 전역 `debounceTimeout` 하나를 쓴다.
   debounce가 걸린 두 입력칸을 번갈아 치면 서로의 전송을 지운다. 새 구조에서는 타이머가 **요소 × 바인딩**마다 하나다.
2. **대상이 `event.target`이다.** `<button wire-disabled-with="저장 중…"><span>저장</span></button>`에서 `<span>`을
   누르면 `wire-disabled-with`를 `<span>`에서 찾으므로 동작하지 않고, 로딩 클래스도 `<span>`에 붙는다. 새 구조에서는
   **바인딩이 달린 요소**가 대상이다. `JS()` 명령의 기준 요소도 같다.
3. **같은 이벤트의 두 번째 바인딩이 버려진다.** §1. `examples/search`의 입력칸은 `keydown`을 넷 걸어 두었는데
   첫 번째(ArrowDown)만 살아 있었다.

구현하며 더 드러난 것 넷. 모두 새 CSP E2E(`tests/test_csp_e2e.py`)가 처음 실행한 경로다.

4. **아무것도 바꾸지 않는 핸들러가 버튼을 영원히 비활성으로 남긴다.** 로딩 클래스와 `wire-disabled-with`는 render가
   도착할 때 지워지는데, diff가 없으면 서버가 render를 보내지 않았다. 이제 사용자 이벤트에는 diff가 없어도
   `{"id", "diff": null}`을 보내고, 클라이언트는 그것을 받으면 로딩 상태를 지운다. 옛 클라이언트는 이 메시지를
   이미 알고(자식만 바뀐 경우와 같은 모양) 무시한다.
5. **업로드 태그가 브라우저에서 한 번도 동작하지 않았다.** 서버가 보내는 `upload_op config`에 컴포넌트 id가 없었고,
   클라이언트는 "이미 그 업로드를 가진 컴포넌트"를 찾아 config를 적용했다 — 처음이라 그런 컴포넌트는 없다. 업로드가
   들어온 커밋(fdad1c8)부터였고, 브라우저로 업로드하는 테스트가 저장소에 하나도 없어서 드러나지 않았다. 이제 config가
   id를 싣는다.
6. **업로드 태그의 추가 속성.** `upload_input`·`upload_button`은 `class="btn"`을 `class=&quot;btn&quot;`으로 두 번
   이스케이프했고, `upload_preview`는 거꾸로 이스케이프하지 않았다 — `alt=entry.client_name`처럼 **클라이언트가 정한
   파일 이름**이 속성을 닫고 `onerror`를 더할 수 있었다(XSS). `upload_preview`는 또 조건식 우선순위 때문에
   UploadEntry의 ref를 늘 빈 문자열로 읽어 미리보기가 파일을 찾지 못했다.
7. **튜토리얼의 `{% upload_input "photos" multiple %}`는 템플릿 오류다.** 태그는 위치 인자를 받지 않고, `multiple`은
   `max_entries`에서 자동으로 붙는다.

## 5. 진행형 향상 — 라이브가 아니면 브라우저에 맡긴다

이슈의 두 번째 문제: `onsubmit="event.preventDefault(); wireview.send(…)"`는 번들이 로드됐지만 소켓이 막힌 상태에서도
`preventDefault()`까지 실행하므로 폼이 아무 데도 가지 않는다.

**서버 핸들러 바인딩(`h`)은 그 컴포넌트가 라이브일 때만 실행한다.** 라이브란 소켓이 열려 있고 컴포넌트가 join을 마친
상태다. 아니면 수정자까지 포함해 아무것도 하지 않는다 — `preventDefault()`도 없으므로 폼은 `action`으로 제출되고 링크는
이동한다. 번들이 아예 없으면 리스너가 없으므로 같은 결과다.

- `JS()` 명령 바인딩(`js`)은 클라이언트만의 동작이라 라이브 여부와 상관없이 실행한다(`push`가 섞여 있으면 그 명령만
  지금처럼 소켓이 없어 실패한다).
- 대가: 페이지가 뜨고 소켓이 join을 마치기 전(보통 100 ms 안쪽)의 클릭도 브라우저 기본 동작이 된다. 지금은 그 창의
  클릭이 조용히 사라진다. 폼이 GET으로 새로고침되는 쪽이 사라지는 쪽보다 낫다고 본다. `action`이 없는 폼은 현재 URL로
  제출되므로, 페이지가 같은 뷰로 돌아온다.

## 6. 그 밖의 인라인 코드

| 곳 | 지금 | 바꾼 뒤 |
|---|---|---|
| `{% upload_input %}` | `onchange="wireview.addFiles(…)"` | 이미 있는 `wire-upload` 속성으로 위임 `change`가 처리 |
| `{% upload_button %}` | `onclick="wireview.selectFiles(…)"` | `wire-upload-select="<이름>"`, 위임 `click` |
| `{% wireview_header %}`의 `<style>` | nonce 없음 → `style-src`에 걸림 | 요청에 CSP nonce가 있으면 `<style>`과 `<script>`에 단다(Django 6.0의 `request._csp_nonce`, 없으면 생략) |
| `.inlinejs` 수정자 | README는 "핸들러를 리터럴 JavaScript로"라고 적지만 **태그로는 한 번도 동작하지 않았다** — `{% on %}`이 명령을 컴포넌트 메서드로 먼저 확인하므로 `{% on "click.inlinejs" "alert(1)" %}`는 AssertionError다 | **명확한 템플릿 오류로 바꾸고 `JS()`를 안내한다.** 되살리려면 인라인이거나 `eval`이어야 하고 둘 다 CSP가 막는다. 트랜스파일러의 수정자는 LSP 메타데이터용으로 남는다 |

## 7. 호환성

- **템플릿은 바뀌지 않는다.** `{% on %}`의 문법과 수정자는 그대로다.
- **렌더된 HTML이 바뀐다.** 생성된 `onclick=` 문자열을 검사하던 앱의 테스트는 깨진다(저장소 안에서는
  `tests/test_event_transpiler.py`가 트랜스파일러를 직접 부르고, 트랜스파일러는 `.inlinejs`와 LSP가 계속 쓴다).
- **새 서버 + 옛 번들**: 옛 번들은 `wire-on-*`를 모르므로 이벤트가 죽는다. HTML과 번들은 같은 응답에서 오므로 섞이는
  경우는 CDN이나 브라우저 캐시가 옛 `wireview.min.js`를 들고 있을 때다. 헤더의 `?v=1`을 `?v=2`로 올려 캐시를 깬다.
- `window.wireview.send`·`debounce`·`throttle`·`exec`는 공개 API로 남는다(사용자 스크립트와 훅이 쓴다).

## 8. 검증

- 수정자 해석은 순수 모듈(`wireview/static/wireview/events.mjs`)로 빼서 node 테스트한다.
- 서버: `{% on %}` 출력의 모양, 이름 문자 제한, `inlinejs`의 예외, 헤더 nonce.
- E2E: **CSP를 강제하는 페이지**(`script-src 'self'`, `style-src 'self' 'nonce-…'`, 인라인 허용 없음)에서 click·
  debounce·키 수정자·submit·`.stop`·LiveComponent `myself`·`JS()`·업로드 버튼이 모두 동작하고
  `securitypolicyviolation`이 0건인 것. 소켓을 막은 페이지에서 `submit.prevent` 폼이 `action`으로 제출되는 것.
  §4의 결함 셋은 각각 되돌리면 실패하는 E2E로.
- 기존 E2E 스위트 전체가 새 바인딩 위에서 돈다 — 모든 예제의 이벤트가 이 경로를 지난다.

## 9. 버린 것

- **값에 수정자를 담기** — 한 요소에 같은 이벤트를 두 번 걸 수 없다(§1).
- **`document` 캡처 단계에서 전부 처리** — 먼저 돌 수는 있지만 `.stop`이 캡처 단계에서 전파를 끊으면 대상 요소 자신의
  리스너(훅, 서드파티 위젯)가 이벤트를 받지 못한다.
- **`.inlinejs`를 인라인 속성이나 `new Function`으로 되살리기** — CSP를 풀어야 하고(`unsafe-inline`·`unsafe-eval`), 태그로는 원래
  동작하지 않던 기능이라 되살릴 사용자가 없다.
- **소켓이 없어도 서버 바인딩의 `prevent`는 실행** — 이슈가 지적한 바로 그 상태다(§5).
