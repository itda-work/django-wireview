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
