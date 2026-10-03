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
| 같은 사이트의 링크(왼쪽 클릭, 수정키 없음, `target` 없음) | ✅ | |
| `{% on "click.prevent" %}`가 처리한 링크 | — | 컴포넌트 이벤트다 |
| `wire-boost`를 단 폼 | ✅ | 아래 |
| `wire-boost`가 없는 폼 | — | 평소의 제출 |
| `wireview.visit(url)` | ✅ | 코드에서 부르는 이동 |

`live_session` 경계를 넘는 이동은 boost하지 않고 전체 로드로 바꾼다. 응답을 받은 뒤에 판단하므로
리다이렉트가 경계 밖에서 끝나도 잡는다([live_session](./live-session.md)).

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
- **그 밖의 폼**: fetch로 보낸다. 응답이 리다이렉트로 끝나면(post/redirect/get) 그 주소로 history 항목을
  만든다. 리다이렉트 없이 페이지로 답하면(오류가 있는 폼) 주소는 그대로다. 새로고침해도 폼을 다시 보내지
  않는다.
- 누른 버튼의 `formaction`·`formmethod`와 `name`/`value`를 따른다. `target`이 다른 창이면 boost하지 않는다.
- 컴포넌트가 `{% on "submit.prevent" %}`로 처리한 폼은 건드리지 않는다.

## 코드에서 이동하기 (`wireview.visit`)

```javascript
wireview.visit("/rooms/3/");                    // 새 history 항목
wireview.visit("/rooms/3/", { replace: true }); // 지금 항목을 바꾼다
```

boost가 켜져 있고 같은 사이트면 링크처럼 이동하고, 아니면 평소처럼 페이지를 연다. 전체 로드로 넘어갔으면
`false`로 끝나는 Promise를 돌려준다. 숨긴 `<a>`를 만들어 `click()`하던 우회가 필요 없다.

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
- **이동이 다른 주소에 착지하면 sticky 컴포넌트는 그 주소로 `params_changed()`를 한 번 받는다**(그 LiveComponent도).
  새 페이지가 화면에 놓이고 떠난 컴포넌트가 떠난 뒤다. 뒤로 가기가 떠날 때의 사본을 먼저 그리고 가져온 페이지를
  다시 놓아도 한 번이다. 다음 페이지에 없어 떠나는 sticky 컴포넌트는 듣지 않는다(#170).
- `Component`에만 쓴다. `LiveComponent`는 부모가 소유하므로 부모와 함께 간다.

## 이동을 알기 (`wireview:navigated`, `navigated()`)

boost 이동이 끝나면 — 새 페이지가 morph되고 그 컴포넌트들이 join을 보낸 뒤 — 두 가지가 **이동마다 한 번** 온다(#128).

1. 이동 전부터 페이지에 있었고 이동 뒤에도 남은 훅의 `navigated()`. sticky 컴포넌트의 훅이 이동을 아는 유일한
   길이다. 새 페이지가 가져온 훅은 `mounted()`를, 빠진 훅은 `destroyed()`를 받고 `navigated()`는 받지 않는다.
2. `document`에 `wireview:navigated` 이벤트. `detail`은 `{ url, previousUrl }`이다. `url`은 리다이렉트를 따라
   도착한 주소다.

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

## 리다이렉트

이동이 리다이렉트를 따라가면 주소창은 **도착한 주소**다. 요청한 주소가 남아 있으면 새로고침이
리다이렉트하는 뷰를 다시 돌린다(#104).

## 관련 기능

- [live_session](./live-session.md) — 경계를 넘는 이동은 전체 로드
- [JavaScript Hooks](./hooks.md) — 이동으로 빠지는 컴포넌트의 훅은 `destroyed()`를 받는다
