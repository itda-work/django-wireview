# 페이지 이동 boost (`BOOST_PAGES`)

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
{# 두 페이지의 레이아웃에 같은 id로 #}
{% component 'Player' id="player" %}
```

- **id를 반드시 명시한다.** 짝은 id로만 맞춘다. `id=`를 빼면 렌더마다 새 id(`rx-<uuid>`)가 붙어 다음 페이지의
  컴포넌트와 짝이 맞지 않고, `sticky = True`가 **아무 신호 없이 꺼진다** — 경고도 `manage.py check`도 없이 보통
  컴포넌트처럼 morph되고 다시 join한다(#128).
- 다음 페이지에도 **같은 id**로 있으면 요소를 건드리지 않는다. 서버 인스턴스와 상태, 구독, 요소의 DOM,
  그 안의 훅이 그대로 이어진다. 새 페이지가 그 컴포넌트에 준 HTML과 값은 쓰이지 않는다.
- 다음 페이지에 없으면 보통 컴포넌트처럼 떠난다(`leaving()`). 그 뒤 다시 나오면 새로 시작한다.
- **`live_session` 경계를 넘는 이동은 전체 로드라서 살아남지 않는다.** 경계는 인증 가정이 바뀌는 곳이고,
  그곳에서는 모든 것이 새 핸드셰이크를 탄다([live-session](./live-session.md)). `BOOST_PAGES`가 꺼져 있어도
  모든 이동이 전체 로드라 살아남지 않는다.
- **이어지는 것은 sticky 요소 안뿐이다.** 그 밖은 `<body>`의 속성과 class까지 새 페이지의 것으로 바뀐다. sticky 안의
  훅은 이동 중에 `mounted`·`updated`·`destroyed` 어느 것도 받지 않으므로, 훅이 페이지 전체에 건 효과(body class,
  스크롤 잠금 같은 것)는 이동 뒤 풀려도 훅이 알 길이 없다. 그런 효과는 페이지마다 서버 템플릿이 그리게 둔다.
- 이동한 뒤 쿼리가 바뀌었으면 sticky 컴포넌트도 `params_changed()`를 받는다.
- `Component`에만 쓴다. `LiveComponent`는 부모가 소유하므로 부모와 함께 간다.

## 리다이렉트

이동이 리다이렉트를 따라가면 주소창은 **도착한 주소**다. 요청한 주소가 남아 있으면 새로고침이
리다이렉트하는 뷰를 다시 돌린다(#104).

## 관련 기능

- [live_session](./live-session.md) — 경계를 넘는 이동은 전체 로드
- [JavaScript Hooks](./hooks.md) — 이동으로 빠지는 컴포넌트의 훅은 `destroyed()`를 받는다
