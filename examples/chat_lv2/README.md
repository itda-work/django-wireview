# chat_lv2

**누가 말하는가를 서버가 정한다 — 로그인 사용자, 방 멤버십, `live_session` 경계**

채팅 예제 시리즈의 둘째 단계다([#197](https://github.com/itda-work/django-wireview/issues/197)).
[chat](../chat/)은 이름을 `?username=`으로 받아서 누구나 남의 이름으로 말할 수 있었고, 방 주소만 알면 아무나
들어왔다. 여기서는 그 둘을 막는다.

## chat에서 바뀐 것

- 이름은 로그인 사용자(`self.user`)에서만 읽는다. `?username=`, 상태 필드 `username`, 핸들러의 이름 인자가 모두 없다.
  메시지는 `author = FK(User)`로 남는다
- 데모 로그인: 고정 사용자 alice·bob·carol 중 하나로 POST 한 번에 로그인한다(로그인 폼이 아니다)
- `Membership(room, user)`: 방 목록에서 들어가기(Join)·나가기(Leave). 방을 만든 사람은 첫 멤버다
- 방 페이지는 `live_session("chat_lv2")` 안에 있고, 세 컴포넌트가 `Meta.live_sessions = {"chat_lv2"}`를 선언한다
- 멤버십 판정은 `access.py`의 `is_member()` 하나다. 뷰(첫 바이트 전), 경계의 `on_mount` 훅(모든 join), 메시지를 쓰는
  핸들러가 모두 이것을 부른다
- 접속자 표시의 id는 `user.pk`다. 같은 이름을 가진 두 사람이 한 사람으로 합쳐지지 않는다
- 방에서 나가면 그 사용자가 열어 둔 그 방의 페이지들이 방 목록으로 돌아간다
- 컴포넌트 이름(`Lv2ChatRoom`, `Lv2MessageList`, `Lv2OnlineUsers`), 앱 label, 템플릿 디렉터리(`chat_lv2/`),
  URL(`/chat-lv2/`, namespace `chat_lv2`), 토픽 접두사(`chat_lv2.`)가 chat과 겹치지 않는다

전체 차이는 저장소 루트에서 이렇게 본다.

```bash
diff -r -x __pycache__ -x '*.pyi' examples/chat examples/chat_lv2
```

## 무엇을 보여주나

**작성자는 연결이 정한다.** `send_message(content)`는 내용만 받는다. 작성자는 `self.user`이고, 브라우저가
`username`·`author` 같은 값을 실어 보내도 바뀌지 않는다. 이름을 상태 필드에 두지 않는 것도 같은 이유다 — 필드는
브라우저가 들고 있다가 돌려보내는 상태다.

**경계가 아는 것과 모르는 것.** `live_session`의 `authorize`는 `LiveSessionContext`(사용자와 세션)만 받는다.
어느 방의 페이지인지는 모르므로, `authorize`는 "로그인했는가"까지만 본다. 방은 각 컴포넌트가 자기 서명 상태에
들고 있는 `room`이고, 경계의 `on_mount` 훅(`RoomMembersOnly`)이 그것으로 멤버십을 묻는다. URL에서 방을 읽지 않는
이유는, join에서 컴포넌트가 쓰는 방은 URL이 아니라 서명 상태에서 오기 때문이다 — 둘이 다르면 URL로 인가한 것과
다른 방이 마운트된다.

| 어디서 | 무엇이 거절하나 | 거절의 모양 |
|------|------|------|
| 방 페이지 요청, 로그인 안 함 | `authorize`(`signed_in`) | 방 목록의 로그인으로, `?next=`에 방 주소 |
| 방 페이지 요청, 멤버 아님 | 뷰의 `is_member()` | 방 목록으로(안내 메시지) |
| WebSocket join, 멤버 아님 | 경계의 훅 `RoomMembersOnly` | 아무것도 그리지 않고(`remove`) 방 목록으로 |
| 이미 열린 페이지의 메시지 보내기 | 핸들러의 `is_member()` | 쓰지 않는다 |

마지막 줄이 따로 있는 것은 경계가 이미 마운트된 컴포넌트의 이벤트를 다시 인가하지 않기 때문이다
([live_session](../../docs/features/live-session.md#로그아웃과-기존-연결)). 방을 나가면 열린 페이지에 알려 목록으로
돌려보내지만, 그 알림은 최선 노력이라 핸들러가 한 번 더 묻는다.

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/chat-lv2/>를 열고 alice로 로그인해 방을 만든다. 시크릿 창에서 같은 주소를
열고 bob으로 로그인한다 — 쿠키를 공유하는 창은 같은 사용자다. bob이 alice의 방 주소로 바로 가면 방 목록으로
돌아오고, Join을 누르면 들어간다. `/chat-lv2/sign-in/<이름>/`은 데모용이다.

```bash
uv run pytest examples/chat_lv2 -m "not e2e"                   # 단위·소켓
WIREVIEW_TEST_LAYER=memory ./tests/e2e.sh examples/chat_lv2    # 브라우저 둘로 alice와 bob
```

테스트는 `examples/chat_lv2/tests.py`. 비멤버의 join은 뷰를 거치지 않는 실제 소켓(프로젝트의 ASGI 스택과 그
사용자의 쿠키)으로 확인한다. `make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 이 예제는
조용히 낡지 않는다.

## 더 읽기

- 이전 단계: [chat](../chat/) — Streams, Broadcast, Presence
- 경계: [live_session](../../docs/features/live-session.md), 훅: [라이프사이클 훅](../../docs/features/lifecycle-hooks.md)
- 핵심 파일: access.py · live_sessions.py · live.py · views.py
