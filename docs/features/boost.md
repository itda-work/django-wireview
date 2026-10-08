# 페이지 이동 boost (`BOOST_PAGES`)

같은 사이트 안의 링크 이동을 전체 페이지 로드 없이 처리한다. WebSocket 연결은 끊기지 않고, 새 페이지의
컴포넌트가 그 연결로 join한다.

> 설정: `WIREVIEW = {"BOOST_PAGES": True}`. 기본은 꺼져 있다.

## 개요

켜면 같은 사이트 안의 링크 이동이 전체 로드가 아니다. 다음 페이지를 fetch로 받아 `<body>`를 morph하고,
history에 항목을 남긴다. htmx의 `hx-boost`, Turbo Drive와 같은 생각이다. WebSocket 연결은 이어지고,
새 페이지의 컴포넌트가 그 연결로 join한다.

## 무엇이 boost되나

| 무엇 | boost | 비고 |
|------|:----:|------|
| 같은 사이트의 링크(왼쪽 클릭, 수정키 없음, `target` 없음) | ✅ | 지금 주소와 같은 주소면 기록 항목을 늘리지 않고 그 자리에서 가져온다. 브라우저가 자기 주소로 가는 링크를 그렇게 다룬다 |
| 같은 페이지 안의 조각 링크(`<a href="#section">`, 경로와 쿼리가 같고 `#`이 있다) | — | 브라우저의 것이다. 스크롤하고 기록 항목을 만든다. 가져오지 않고 컴포넌트에 알리지도 않는다. 그 항목 사이의 뒤로·앞으로 가기도 같다(#170) |
| `{% on "click.prevent" %}`가 처리한 링크 | — | 컴포넌트 이벤트다 |
| `wire-boost`를 단 폼 | ✅ | 아래 |
| `wire-boost`가 없는 폼 | — | 평소의 제출 |
| `wireview.visit(url)` | ✅ | 코드에서 부르는 이동. 조각만 다른 주소(`"#top"`)는 링크처럼 브라우저가 한다 |

`live_session` 경계를 넘는 이동은 boost하지 않고 전체 로드로 바꾼다. 응답을 받은 뒤에 판단하므로
리다이렉트가 경계 밖에서 끝나도 잡는다([live_session](./live-session.md)).

### 가져오기가 실패하면

서버의 답이 오류 페이지(404, 500)면 그 페이지를 그 주소 아래 그린다. **답이 오지 않으면**(네트워크가 끊겼다)
그 이동을 브라우저에 넘긴다(#170). 링크, `push_to`·`replace_to`·`redirect_to`, 뒤로·앞으로 가기, GET 폼은 그 주소를
보통의 페이지 로드로 다시 연다 — 주소창은 이미 그 주소이고, 브라우저가 무엇이 잘못됐는지 그 주소 아래 보여 준다.
1.1까지는 처리되지 않은 rejection이 나고 주소창만 새 주소인 채 화면은 옛 페이지로 남았다.

POST 같은 GET이 아닌 폼은 **다시 보내지 않는다.** 주소창은 움직이지 않았으므로 페이지가 그대로 남고, `document`에
`wireview:navigation-failed` 이벤트가 간다. `detail`은 `{ url, method, answered }`다.

- `answered: false` — 답이 오지 않았다(네트워크). 요청이 서버에 닿았는지는 알 수 없다.
- `answered: true` — 서버가 폼을 받아 **다른 출처로 리다이렉트했다**(결제 페이지, SSO). 폼은 처리됐다. boost가
  보낸 요청은 그 주소를 볼 수 없어 따라가지 못한다. 사이트 밖을 거쳐 이 사이트로 돌아오는 리다이렉트(SSO 왕복)도
  `answered: true`다 — 마지막 페이지가 이 사이트여도 한 번 밖을 거친 응답은 열어 볼 수 없다. 사이트 밖으로
  리다이렉트하는 폼에는 `wire-boost`를 달지 않는다.

```javascript
document.addEventListener("wireview:navigation-failed", (e) => {
  if (!e.detail.answered) alert(`보내지 못했습니다: ${e.detail.url}`);
});
```

폼은 `no-cors` 모드로 보낸다. 그래야 같은 출처 안의 리다이렉트는 평소처럼 따라가 읽고(post/redirect/get이 그대로
그려진다), 다른 출처로 가는 리다이렉트는 네트워크 오류가 아니라 열어 볼 수 없는 응답으로 돌아와 둘을 가를 수 있다.
`redirect: "manual"`은 같은 출처 리다이렉트의 주소까지 숨긴다. 링크·GET 폼처럼 다시 가져와도 되는 이동이 다른
출처로 리다이렉트되면, 위처럼 브라우저가 그 주소를 다시 열어 리다이렉트를 따라간다.

중단된 요청(`AbortError`, Firefox에서 사용자가 중지를 누른 가져오기)은 실패가 아니다. 브라우저의 중지처럼
화면에 있던 페이지에 머문다(#170).

- 링크·`push_to`·GET 폼: 이동이 이미 만든 기록 항목에서 물러나 주소창이 화면의 페이지로 돌아간다.
- `replace_to`·`redirect_to`: 바꿔 쓴 지금 항목이 원래 주소를 되찾는다.
- 뒤로·앞으로 가기: 기록은 이미 그 항목으로 옮겨 갔고 얼마나 옮겼는지 페이지는 알 수 없으므로 주소창은 그대로다.
  캐시 사본을 그렸다면 그 사본이 그 항목의 페이지로 착지한다. 사본이 없으면 그 항목을 boost 없이 연다.
- 폼은 그대로 남고 `wireview:navigation-failed`를 보내지 않는다.
- 문서 자체가 떠나는 중(브라우저가 다른 주소로 가며 가져오기를 끊었다)이면 기록을 건드리지 않는다.

다른 이동이 앞지른 이동의 가져오기가 늦게 실패해도 아무것도 하지 않는다 — 화면은 뒤의 이동 것이다. Chromium의
`window.stop()`은 끊긴 연결과 같은 `TypeError`로 가져오기를 끝내므로 구별되지 않고, 그 주소를 boost 없이 연다.

뒤로·앞으로 가기 캐시(bfcache)가 이동 도중에 얼어붙은 문서를 되살리면, 그 문서는 주소창이 가리키는 항목에 다시
도착한다(그 항목으로 가는 popstate처럼). Chromium은 열린 WebSocket이 있는 페이지를 그 캐시에 넣지 않는다.

## 폼은 스스로 청한다 (`wire-boost`)

```html
<form method="post" action="{% url 'comments:add' %}" wire-boost>
  {% csrf_token %}
  ...
</form>
```

**폼은 기본으로 boost하지 않는다.** boost한 이동은 WebSocket을 그대로 쓰는데, 로그인·로그아웃처럼 누가
로그인했는지를 바꾸는 폼이 boost되면 그 연결은 옛 신분으로 남는다. 안전한 폼만 `wire-boost`로 청한다.

- **GET 폼**: 필드를 쿼리로 붙인 주소로 이동한다. 브라우저가 가는 곳과 같다.
- **POST 폼**: fetch로 보낸다. 응답이 리다이렉트로 끝나면(post/redirect/get) 그 주소로 history 항목을
  만든다. 리다이렉트 없이 페이지로 답하면(오류가 있는 폼) 주소는 그대로다. 새로고침해도 폼을 다시 보내지
  않는다.
- method는 브라우저처럼 읽는다. `get`·`post`·`dialog` 말고는(`put`, `delete`, `patch`, 빈 값) GET이다 —
  `method="put"` 폼은 브라우저가 보내듯 쿼리를 단 GET 이동이 된다(#170). `dialog` 폼은 boost하지 않는다(대화상자를
  닫을 뿐 어디로도 가지 않는다).
- 누른 버튼의 `formaction`·`formmethod`와 `name`/`value`를 따른다. `target`이 다른 창이면 boost하지 않는다.
- 컴포넌트가 `{% on "submit.prevent" %}`로 처리한 폼은 건드리지 않는다.

## 코드에서 이동하기 (`wireview.visit`)

```javascript
wireview.visit("/rooms/3/");                    // 새 history 항목
wireview.visit("/rooms/3/", { replace: true }); // 지금 항목을 바꾼다
```

boost가 켜져 있고 같은 사이트면 링크처럼 이동하고, 아니면 평소처럼 페이지를 연다. 전체 로드로 넘어갔거나
`wireview:before-navigate`가 막았으면 `false`로 끝나는 Promise를 돌려준다. 숨긴 `<a>`를 만들어 `click()`하던 우회가 필요 없다.

## 이동을 건너 살아남기 (`sticky`)

boost 이동은 body를 새 페이지로 morph하고, 두 페이지에 다 있는 컴포넌트도 새 페이지의 상태로 다시 join한다.
음악 플레이어, 채팅 창, 진행 중인 업로드처럼 **페이지를 가로질러 이어져야 하는 컴포넌트**는 `sticky`로 선언한다.

```python
class Player(Component):
    class Meta:
        template_name = "player.html"
        sticky = True
```

```html
{# 두 페이지의 레이아웃에. id를 주지 않으면 클래스에서 만든 id가 붙는다 #}
{% component 'Player' %}
```

- **짝은 id로 맞춘다.** `id=`를 주지 않은 sticky 컴포넌트는 클래스의 전체 경로에서 만든 id를 받는다
  (`myapp.live.Player` → `sticky-myapp-live-Player`). 어느 페이지에서 그려도 같은 id라 그대로 짝이 맞는다.
  sticky가 아닌 컴포넌트는 전처럼 렌더마다 새 id(`rx-<uuid>`)다.
- **같은 sticky 클래스를 한 페이지에 둘 이상 두면 각자 id를 준다.** id 없이 두 번 그리면 둘째부터는 새 id를
  받아 이동을 건너지 못하고, `wireview` 로거에 경고가 남는다(#128). 두 요소가 id 하나를 나누면 페이지에는
  컴포넌트 하나로 보이기 때문에 같은 id를 줄 수는 없다. 이 판단은 페이지의 첫 렌더(HTTP)에서 한다 —
  라이브 렌더에서는 같은 id가 "같은 인스턴스를 다시 그림"이라 둘째 인스턴스와 구별되지 않는다.
- 다음 페이지에도 **같은 id**로 있으면 요소를 건드리지 않는다. 서버 인스턴스와 상태, 구독, 요소의 DOM,
  그 안의 훅이 그대로 이어진다. 새 페이지가 그 컴포넌트에 준 HTML과 값은 쓰이지 않는다.
- 다음 페이지에 없으면 보통 컴포넌트처럼 떠난다(`leaving()`). 그 뒤 다시 나오면 새로 시작한다.
- **`live_session` 경계를 넘는 이동은 전체 로드라서 살아남지 않는다.** 경계는 인증 가정이 바뀌는 곳이고,
  그곳에서는 모든 것이 새 핸드셰이크를 탄다([live-session](./live-session.md)). `BOOST_PAGES`가 꺼져 있어도
  모든 이동이 전체 로드라 살아남지 않는다.
- **이어지는 것은 sticky 요소 안뿐이다.** 그 밖은 `<body>`의 속성과 class까지 새 페이지의 것으로 바뀐다. sticky 안의
  훅은 이동 중에 `mounted`·`updated`·`destroyed` 어느 것도 받지 않는다. 훅이 페이지 전체에 건 효과(body class,
  스크롤 잠금 같은 것)는 이동 뒤 풀리므로, 훅의 `navigated()`에서 다시 건다(아래 「이동을 알기」).
  페이지마다 서버 템플릿이 그리게 둘 수 있으면 그쪽이 더 단순하다.
- **이동이 다른 주소에 착지하면 sticky 컴포넌트는 그 주소로 `params_changed()`를 한 번 받는다.** 그 안에
  `{% component %}`로 그린 컴포넌트와 LiveComponent도 받는다 — 요소와 함께 그대로 건너왔기 때문이다.
  새 페이지가 화면에 놓이고 떠난 컴포넌트가 떠난 뒤다. 뒤로 가기가 떠날 때의 사본을 먼저 그리고 가져온 페이지를
  다시 놓아도 한 번이다. 다음 페이지에 없어 떠나는 sticky 컴포넌트는 듣지 않는다(#170).
- `Component`에만 쓴다. `LiveComponent`는 부모가 소유하므로 부모와 함께 간다.

## 이동을 알기 (`wireview:navigated`, `navigated()`)

boost 이동이 끝나면 — 새 페이지가 morph되고 그 컴포넌트들이 join을 보낸 뒤 — 두 가지가 **이동마다 한 번** 온다(#128).

1. 이동 전부터 페이지에 있었고 이동 뒤에도 남은 훅의 `navigated()`. sticky 컴포넌트의 훅이 이동을 아는 유일한
   길이다. 새 페이지가 가져온 훅은 `mounted()`를, 빠진 훅은 `destroyed()`를 받고 `navigated()`는 받지 않는다.
2. `document`에 `wireview:navigated` 이벤트. `detail`은 `{ url, previousUrl, kind }`이다. `url`은 리다이렉트를 따라
   도착한 주소이고, `kind`는 그 이동을 무엇이 시작했는지다 — 아래 「이동을 막기」의 표와 같은 값이다(#154).

```javascript
window.wireview.hooks.Player = {
  mounted() { document.body.classList.add("with-player"); },
  navigated() { document.body.classList.add("with-player"); },  // 새 <body>에 다시
};

document.addEventListener("wireview:navigated", (e) => {
  analytics.page(e.detail.url);
});
```

- 뒤로 가기는 캐시된 페이지를 먼저 그리고 서버의 페이지로 다시 맞추지만, 알림은 서버의 페이지가 그려진 뒤 한 번이다.
- `live_session` 경계를 넘는 이동과 `BOOST_PAGES`가 꺼진 이동은 전체 로드라서 오지 않는다. 새 문서가 처음부터 시작한다.
- 리다이렉트·다른 경로로 가는 `push_to`처럼 서버가 보낸 이동도 boost로 가면 똑같이 온다. 같은 경로의 `push_to`·`replace_to`와 그 항목 사이의 뒤로·앞으로 가기는 patch라서 오지 않는다 — 페이지가 그대로다([내비게이션](./navigation.md)).

## 이동을 막기 (`wireview:before-navigate`)

boost 이동은 `history.pushState`라서 브라우저의 `beforeunload`가 걸리지 않는다. 저장하지 않은 입력이 있을 때
이동을 막으려면 `document`의 `wireview:before-navigate`를 듣는다(#154). boost가 이동을 **시작하기 전에** 한 번 오고,
취소할 수 있다. `preventDefault()`하면 이동하지 않는다 — 주소창·기록·화면이 그대로고, 아무것도 가져오지 않으며,
서버는 `params_changed()`도 이동도 듣지 않는다. `wireview:navigated`도 오지 않는다.

`detail`은 `{ url, kind, patch }`이고 폼이면 `form`이 더 있다.

- `url` — 가는 주소(절대 주소). 뒤로·앞으로 가기는 주소창이 이미 가리키는 항목이다. POST 폼은 폼의 `action`이다.
- `patch` — 페이지는 그대로이고 params만 바뀐다(같은 경로의 `push_to`·`replace_to`, 그 항목 사이의 뒤로·앞으로
  가기). 가져오지 않고 컴포넌트가 `params_changed()`를 듣는다. 화면의 폼이 사라지지 않으므로 묻지 않아도 되면
  이것으로 거른다.
- `form` — 보내는 폼 요소. 지키는 폼 자신을 보내는 것(저장)은 막지 않게 거른다.
- `kind` — 무엇이 시작했는가.

| `kind` | 이동 | 취소하면 |
|--------|------|----------|
| `link` | boost된 링크 클릭 | 클릭이 아무 일도 하지 않는다 |
| `form` | `wire-boost` 폼(GET·POST) | 폼을 보내지 않는다 |
| `visit` | `wireview.visit(url)`, `JS().navigate(url)` | `visit()`이 `false`로 끝난다 |
| `push` | 서버의 `push_to` | 주소도 기록도 그대로, 서버는 params를 듣지 않는다 |
| `replace` | 서버의 `replace_to` | 위와 같다 |
| `redirect` | 서버의 `redirect_to` | **취소할 수 없다**(`cancelable: false`). 아래 |
| `popstate` | 뒤로·앞으로 가기(`history.back()`, 서버의 `back` 포함) | 떠난 항목으로 되돌아간다. Navigation API가 없는 브라우저에서는 취소할 수 없다. 아래 |

```javascript
// 저장하지 않은 변경이 있으면 묻는다
let dirty = false;
let leaving = false;  // 이미 허락한 이동이 전체 로드로 바뀌었을 때 다시 묻지 않게
document.addEventListener("input", (e) => {
  if (!e.target.closest("form[data-guard]")) return;
  dirty = true;
  leaving = false;  // 다시 고쳤으면 다시 지킨다
});
document.addEventListener("wireview:before-navigate", (e) => {
  if (!dirty || !e.cancelable) return;
  if (e.detail.form?.matches("[data-guard]")) return;  // 그 폼을 보내는 것은 저장이다
  if (!confirm("저장하지 않은 변경이 있습니다. 떠날까요?")) {
    e.preventDefault();
  } else if (!e.detail.patch) {
    leaving = true;  // patch는 페이지에 남으므로 지킴을 끄지 않는다
  }
});
window.addEventListener("beforeunload", (e) => {
  if (dirty && !leaving) e.preventDefault();  // boost하지 않는 이동(새로고침, 다른 사이트)
});
document.addEventListener("wireview:navigated", () => {
  dirty = false;
  leaving = false;
});
document.addEventListener("wireview:navigation-failed", () => {
  leaving = false;  // 폼이 가지 못해 페이지가 그대로다
});
```

- **뒤로·앞으로 가기**는 이벤트가 올 때 주소창이 이미 옮겨 가 있다. 취소하면 떠난 항목으로 되돌아가고(두 칸을
  갔으면 두 칸), 그 되돌아감은 이벤트도 params도 보내지 않는다. 어느 항목을 떠났는지는 브라우저의 Navigation API
  (`navigation.currententrychange`)가 알려 주고, 돌아가는 것은 그 항목으로의 `navigation.traverseTo()`다 — 페이지
  코드가 `history.pushState`로 직접 만든 항목 사이에서도 같다. History API만으로는 어느 쪽으로 몇 칸 갔는지 알 수
  없으므로, **Navigation API가 없는 브라우저에서는 뒤로·앞으로 가기가 `cancelable: false`로 온다**(알리기만 한다).
- 되돌아가는 동안 또 뒤로·앞으로 가기가 일어나면(빠른 연타) 되돌림이 그것을 덮는다. 페이지는 떠나지 않은 항목에
  남고, 덮인 이동은 묻지 않는다. 그 사이에 브라우저가 연타를 따로 보여 주는지(Chromium) 되돌림에 합치는지(WebKit)는
  브라우저마다 다르고, 어느 쪽이든 결과는 같다.
- 되돌림이 거부되면 — 페이지 코드의 Navigation API `navigate` 리스너가 `preventDefault()`했거나 브라우저가 그
  이동을 버렸다 — 페이지는 주소창이 가리키는 항목으로 간다. 막을 수 없던 이동이므로 같은 이동이 `cancelable: false`로
  한 번 더 온다. 가드는 그것으로 페이지가 결국 떠났음을 안다.
- **`redirect_to`는 알리기만 한다.** 서버는 `redirect_to`를 보낸 컴포넌트를 얼려(freeze) 더는 렌더하지 않는다.
  남은 페이지의 그 컴포넌트는 이벤트를 받고도 답하지 않으므로 막을 수 없다. 막아야 하는 이동이면 서버에서 다른
  경로로 가는 `push_to`를 쓴다.
- **이벤트가 오지 않는 이동.** 조각 링크(`#section`)와 그 항목 사이의 이동은 브라우저의 것이다. boost하지 않는 이동
  — `BOOST_PAGES`가 꺼진 링크·`visit()`·`redirect_to`, 다른 사이트 — 은 전체 로드라서 `beforeunload`가 맡는다.
  bfcache가 되살린 문서도 묻지 않는다(브라우저가 이미 떠났다 돌아왔다).
- **허락한 이동이 전체 로드로 바뀔 수 있다.** `live_session` 경계를 넘거나 가져오기가 답을 받지 못하면 boost는
  그 주소를 보통의 페이지 로드로 연다. 그때 `beforeunload`도 걸리므로 위 예시처럼 이미 허락했는지 기억해 두 번
  묻지 않는다. 그 기억은 페이지를 떠나는 이동에만 둔다 — patch를 허락해도 페이지와 폼은 남으므로, 그 뒤의
  새로고침은 다시 물어야 한다. 허락한 이동이 페이지를 바꾸지 못하면(`wireview:navigation-failed`) 기억을 지우고,
  사용자가 중지 버튼으로 멈춘 이동처럼 알림 없이 남은 경우는 다음 입력이 지킴을 다시 켠다.
- 이벤트는 이동마다 한 번이다. 지금 주소와 같은 주소의 링크(그 자리에서 다시 가져온다)도, POST 폼이 리다이렉트로
  끝나도 한 번이다. 예외는 위의 거부된 되돌림 하나다.

## 리다이렉트

이동이 리다이렉트를 따라가면 주소창은 **도착한 주소**다. 요청한 주소가 남아 있으면 새로고침이
리다이렉트하는 뷰를 다시 돌린다(#104).

## 관련 기능

- [live_session](./live-session.md) — 경계를 넘는 이동은 전체 로드
- [JavaScript Hooks](./hooks.md) — 이동으로 빠지는 컴포넌트의 훅은 `destroyed()`를 받는다
