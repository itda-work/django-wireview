# JavaScript 없는 첫 렌더 (dead view)

JavaScript가 꺼져 있거나 번들이 아직 오지 않은 브라우저, 검색 엔진, 일부 접근성 도구가 보는 것은 서버의 첫
HTTP 렌더뿐이다. wireview가 그 상태에서 약속하는 것과 약속하지 않는 것이다. Phoenix의 dead view에 해당한다
(GAP-034). `tests/test_dead_view_e2e.py`가 JavaScript를 끈 브라우저로 이 약속을 지킨다.

## 약속하는 것

- **첫 렌더는 완전한 HTML이다.** 컴포넌트는 서버에서 끝까지 그려져 온다. 필드로 가진 것은 모두 보인다.
- **주소의 쿼리가 반영되어 있다.** 쿼리가 있는 주소(`/search/?q=장고`)를 열면 서버는 컴포넌트마다 마운트 훅 뒤에
  `params_changed()`를 부르고 나서 그린다. Phoenix의 dead render와 같은 mount → handle_params → render 순서다(#177).
  검색 결과, 필터한 목록, 고른 탭이 JavaScript 없이도 보이고, 검색엔진이 그 주소를 그대로 색인한다. `params_changed()`는
  join에서 한 번 더 돌므로 두 번 돌아도 같은 상태를 내야 한다. join은 첫 응답이 그린 상태가 아니라 마운트 상태에서
  params를 다시 듣는다 — `data-state`에는 `params_changed()` 전의 상태가 서명된다([내비게이션](./navigation.md#첫-응답과-join)).
  `examples/search/tests.py`가 JavaScript를 끈 브라우저로 `?q=`가 있는 주소를 열어 본다.
- **링크는 링크다.** `BOOST_PAGES`는 JavaScript가 링크를 가로챌 때만 동작하므로, 없으면 보통의 이동이다.
- **`action`과 `method`를 적은 폼은 그 뷰로 간다.** 같은 폼이 JavaScript가 있으면 `{% on "submit.prevent" %}`
  핸들러로, 없으면 브라우저의 기본 제출로 간다. 이벤트 바인딩이 인라인 스크립트가 아니라 속성이라(#90) 막는
  것이 없다.

```html
<form method="post" action="{% url 'notes:add' %}" {% on "submit.prevent" "add" %}>
  {% csrf_token %}
  <input name="text">
  <button>추가</button>
</form>
```

```python
# JavaScript가 있을 때
class Notes(Component):
    async def add(self, text: str = ""):
        ...

# 없을 때: 같은 일을 하는 보통의 뷰. 끝나면 리다이렉트해서 새로고침이 다시 보내지 않게 한다
def add(request):
    Note.objects.create(text=request.POST["text"])
    return redirect("notes:index")
```

컴포넌트 템플릿의 `{% csrf_token %}`은 그 폼이 실제로 통과하는 토큰을 그린다 — 첫 HTTP 렌더에서도, JavaScript가
다시 그린 뒤에도. 아래 [폼의 CSRF 토큰](#폼의-csrf-토큰)이 어디서 오는지 적는다.

## 약속하지 않는 것

- **이벤트 핸들러.** `{% on "click" ... %}`만 단 버튼은 아무 일도 하지 않는다. 폼으로 대신할 수 있는 동작만
  폼으로 쓴다.
- **스트림 항목.** 첫 HTTP 렌더에서 `stream()`·`stream_insert()`·`stream_delete()`는 아무것도 하지 않는다 — 항목을
  그리지도 목록을 읽지도 않는다. 받을 소켓이 없고, 그리던 시절에는 그 렌더가 요청의 DB 연결을 닫았다(#190). 그래서
  `params_changed()`에서 `stream()`해도 첫 렌더의 컨테이너는 비어 있고, join이 다시 들은 params로 채운다.
  JavaScript 없이도 보여야 하는 항목은 필드에 담아 템플릿이 그린다 — `<ul wire-stream="items">{% for item in this.initial %}…`.
- **비동기 작업의 결과.** 첫 렌더에서 `assign_async`·`start_async`로 시작한 작업은 연결이 없어 취소되고, 그
  자리는 로딩 상태로 그려진다. join이 마운트 상태에서 `params_changed()`를 다시 돌며 작업을 다시 시작한다. JavaScript 없이도 보여야 하는 값은 `params_changed()`에서
  기다려 필드에 담는다.
- **실시간 갱신, 훅, 업로드, 플래시.** 모두 연결이 있어야 한다.
- **WebSocket만 막힌 환경.** JavaScript는 있고 소켓이 막히면 페이지는 첫 렌더 그대로 남는다. 롱폴링 폴백은
  설계상 제외다(GAP-012). 그 경우도 위의 폼과 링크는 동작한다.

## 폼의 CSRF 토큰

컴포넌트 템플릿은 페이지의 `RequestContext`가 아니라 컴포넌트의 컨텍스트(필드·property·`this`)로 그려진다. 그래도
`{% csrf_token %}`과 `{{ csrf_token }}`은 Django 페이지에서처럼 동작한다(#206). 1.5까지는 컴포넌트 안에서 빈 값이라
일반 POST 폼이 403을 받았다.

| 렌더 | 토큰 |
|------|------|
| 첫 HTTP 렌더 | 페이지의 것 — Django `csrf` 컨텍스트 프로세서의 lazy 값 그대로. 템플릿이 읽을 때에야 `get_token()`이 돌아, `{% csrf_token %}`을 그린 페이지만 CSRF 쿠키(`Set-Cookie`, `Vary: Cookie`)를 받는다 |
| 라이브 렌더 (join, 이벤트·알림 뒤, LiveComponent, 함수 컴포넌트, 스트림 항목) | 연결이 WebSocket 핸드셰이크에서 받은 CSRF 쿠키의 시크릿으로 만든 것. `CSRF_USE_SESSIONS = True`면 세션의 시크릿 |
| 테스트의 [`mount()`](./testing.md#주의사항) | 쿠키를 가진 페이지처럼, `mount()`마다 만든 시크릿의 것. `render()`는 부를 때마다 새로 마스킹하고 라이브 렌더는 하나를 함께 쓴다 |

- **컴포넌트 렌더의 라이브 토큰은 연결마다 하나다.** 시크릿을 연결이 처음 그릴 때 한 번 마스킹해 그 연결의 모든
  렌더에 쓴다. 렌더마다 마스크를 바꾸면 정적 부분이 매번 달라져 부분 diff가 사라진다. Phoenix LiveView도 프로세스마다
  한 번 마스킹한 토큰을 쓴다. 그 토큰이 소켓에 다시 실리는 때는 이렇다.
  - 변수가 있는 `{% if %}`·`{% for %}` 블록 안의 폼은 블록의 정적 부분이라, 처음 그려질 때 한 번 가고 그 뒤 부분
    diff에는 다시 실리지 않는다.
  - 변수가 없는 조건 블록(`{% if %}` 안이 고정 마크업)은 블록 전체가 하나의 동적 문자열이라 분기가 바뀔 때마다 다시 간다.
  - 새로 그려진 컴포넌트·블록의 첫 렌더는 정적 부분(토큰)과 동적 부분(필드 값)을 한 메시지에 싣는다.
  - **스트림 항목**은 diff가 아니라 HTML 통째로 삽입마다 간다. 그래서 항목의 토큰은 삽입마다 새로 마스킹한다.
- **압축한 소켓에서는 연결 안의 BREACH 완화가 없다.** Django는 응답마다 마스크를 바꿔 압축된 응답에서 토큰을
  추측하지 못하게 한다(BREACH). 위의 경우 연결의 토큰은 같은 값으로 여러 메시지에, 다른 사용자가 정한 텍스트(채팅
  메시지 같은 필드 값) 옆에 실린다. 서버가 WebSocket 압축(permessage-deflate)을 협상하면 그 메시지들이 한 압축
  문맥에 들어가고, 경로를 엿보는 쪽이 피해자 화면에 텍스트를 반사시킬 수 있으면 길이로 토큰을 한 글자씩 맞혀 볼
  수 있다. 운영에서는 압축을 끈다 — uvicorn은 기본으로 켜므로 `--ws-per-message-deflate false`([배포](../DEPLOYMENT.md#권장-uvicorn--uvloop)).
  daphne는 기본으로 협상하지 않는다.
- **보는 사람의 것이다.** 토큰은 그 연결의 시크릿에만 맞는다. 여러 연결이 렌더를 함께 쓰는
  [`Meta.shared_render`](./shared-render.md)의 공유 렌더와 [`Broadcast`](./broadcast.md) 항목에는 넣지 않는다 —
  검사(`VERIFY_SHARED_RENDER`, `Broadcast`는 언제나)에서는 읽으면 오류이고, 템플릿에 직접 쓰면 `wireview.W019`가
  그 클래스를 공유에서 뺀다(그러면 렌더는 연결 자기 것이라 자기 토큰을 받는다).
- **시크릿이 바뀌면(로그인의 `rotate_token`)** 이미 열린 연결은 옛 시크릿의 토큰을 계속 그린다. 그 폼은 403이다 —
  로그인 전에 그려 둔 Django 페이지의 폼과 같다. 로그인 폼은 boost하지 않으므로([boost](./boost.md#폼은-스스로-청한다-wire-boost))
  로그인 뒤의 새 페이지와 새 연결은 새 시크릿을 받는다. `CSRF_USE_SESSIONS`이면 경계([live_session](./live-session.md))에
  들어갈 때 세션을 다시 읽으므로 그 뒤 렌더는 새 시크릿을 따른다. `BOOST_PAGES`에서 **다른 탭**이 로그인하거나 시크릿을
  바꾼 뒤 이 탭이 boost 이동하면, 새로 받은 페이지의 컴포넌트는 join하기 전에 새 소켓을 연다 — 서명 상태가 그 페이지의
  시크릿 지문을 싣고, 연결의 것과 다르면 서버가 `renew`로 답한다
  ([다른 탭이 신분을 바꾼 뒤의 이동](./live-session.md#다른-탭이-신분을-바꾼-뒤의-이동), #209). 그러지 않으면 새로
  받은 페이지에서 Django가 그린 폼은 통과하고 컴포넌트가 join하며 그린 폼은 옛 시크릿의 토큰으로 403이다. 이동하지
  않고 열려 있는 페이지는 지금도 옛 시크릿이다 — Django가 그려 둔 그 페이지의 폼도 같다.
- **연결에 시크릿이 없으면** 토큰은 비고, 서버 로그에 경고가 한 번 남는다. 첫 렌더가 `{% csrf_token %}`을 그리지
  않아 쿠키를 받은 적이 없는 페이지 — 폼이 이벤트 뒤에야 나타나는 페이지 — 가 그렇다. 그 뷰에
  `django.views.decorators.csrf.ensure_csrf_cookie`를 붙인다. 쿠키가 있는데도 그렇다면 `CSRF_COOKIE_PATH`·
  `CSRF_COOKIE_DOMAIN`이 WebSocket 주소(`/__wireview__`)를 덮지 않아 브라우저가 핸드셰이크에 싣지 않은 것이다. Django가 받아 주지 않을 쿠키(형식이 틀린 것)도 없는 것과
  같다. 시크릿은 Django의 `CsrfViewMiddleware`가 요청에서 읽듯 읽는다(쿠키 이름, `CSRF_USE_SESSIONS`, 4.0 이전의
  마스킹된 쿠키).
- 같은 이름의 필드나 property(`csrf_token`)를 가진 컴포넌트는 그것을 읽는다.

`tests/test_csrf_token_e2e.py`가 이 렌더들이 그린 폼을 토큰을 검사하는 뷰로 제출해 보고, `tests/test_csrf_token.py`가
연결마다의 토큰과 공유 렌더를 본다.

## 첫 렌더의 훅과 데이터베이스

첫 HTTP 렌더의 마운트 훅·`params_changed()`·async property는 `async_to_sync`로 불린다. async 본문은 따로 띄운
이벤트 루프에서 돌지만, 그것이 기다리는 thread-sensitive 동기 작업(Django의 async ORM, `sync_to_async`, Channels의
`database_sync_to_async`)은 템플릿을 그리는 스레드, 곧 **요청의 스레드로 돌아와 요청의 연결과 트랜잭션을 쓴다.**
그래서 `ATOMIC_REQUESTS`에서 뷰가 렌더 전에 쓴 행을 훅의 ORM 호출이 읽을 수 있다.

Channels의 `database_sync_to_async`는 앞뒤로 `close_old_connections()`를 부른다. 워커 스레드를 위한 정리지만
여기서는 요청의 연결이고, 트랜잭션 안이면 `CONN_MAX_AGE`와 상관없이 닫혀 뷰의 쓰기가 예외 없이 롤백되고
`on_commit`도 사라졌다. wireview는 그 다리를 건너는 동안 그 스레드의 연결을 닫지 않게 붙잡는다(#190). 훅이
`database_sync_to_async`를 직접 써도 된다.

`thread_sensitive=False`로 다른 스레드에 보낸 작업은 다른 연결을 쓰므로 뷰가 아직 커밋하지 않은 쓰기를 보지 못한다.
async 뷰가 `render()`를 이벤트 루프에서 바로 부르면 훅은 도우미 스레드의 자기 루프에서 돌고 자기 연결을 연다.
`tests/test_http_render_connections.py`가 동기 뷰와 ASGI 아래 동기 뷰에서 이 약속을 파일 SQLite로 지킨다(메모리 SQLite는 `close()`를 무시한다).

## 정책이 거절한 컴포넌트

`Meta.on_mount` 훅이 halt하거나 `Meta.live_sessions`가 그 페이지를 허용하지 않은 컴포넌트는 첫 렌더에서도
**아무것도 그리지 않는다**(#58). 그 자리는 비어 있다. 훅이 리다이렉트를 걸었으면 `<meta http-equiv="refresh">`가
나간다. 그러니 dead view가 약속하는 것은 정확히는 "정책을 통과한 컴포넌트의 첫 HTML"이다. JavaScript가 없는
방문자에게 거절을 설명해야 한다면 뷰가 먼저 판단해서 다른 페이지를 그린다 — `@session.view`가 그 자리다.
