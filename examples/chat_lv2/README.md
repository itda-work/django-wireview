# chat_lv2

**누가 말하는가를 서버가 정한다 — 로그인 사용자, 방 멤버십, `live_session` 경계**

채팅 예제 시리즈의 둘째 단계다([#197](https://github.com/itda-work/django-wireview/issues/197)).
[chat](../chat/)은 이름을 `?username=`으로 받아서 누구나 남의 이름으로 말할 수 있었고, 방 주소만 알면 아무나
들어왔다. 여기서는 그 둘을 막는다. 다만 Join은 로그인한 누구나 누를 수 있다 — 막는 것은 익명과 Join하지 않은
사람이고, 초대제 방은 다음 단계의 일이다.

## chat에서 바뀐 것

- 이름은 로그인 사용자(`self.user`)에서만 읽는다. `?username=`, 상태 필드 `username`, 핸들러의 이름 인자가 모두 없다.
  메시지는 `author = FK(User)`로 남는다
- 데모 로그인: 고정 사용자 alice·bob·carol 중 하나로 POST 한 번에 로그인한다(로그인 폼이 아니다)
- `Membership(room, user)`: 방 목록에서 들어가기(Join)·나가기(Leave). 방을 만든 사람은 첫 멤버다
- 방 페이지는 `live_session("chat_lv2")` 안에 있고, 세 컴포넌트가 `Meta.live_sessions = {"chat_lv2"}`를 선언한다
- 멤버십 판정은 `access.py`의 `is_member()` 하나다. 뷰(첫 바이트 전), 경계의 `on_mount` 훅(모든 join), 메시지를 쓰는
  핸들러와 typing을 알리는 핸들러, 멤버십이 끝났다는 알림을 받은 컴포넌트가 모두 이것을 부른다
- 접속자 표시의 id는 `user.pk`다. id가 브라우저가 보낸 값이 아니라 로그인한 계정에서 나온다
- 방에서 나가면 그 사용자가 열어 둔 그 방의 페이지들이 서버에서 그 방의 구독을 놓고, 방 목록으로 돌아간다
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
| 이미 열린 페이지의 메시지 보내기, typing | 핸들러의 `is_member()` | 쓰지 않고 알리지 않는다 |
| 이미 열린 페이지가 받는 새 메시지·접속자 | 멤버십 알림을 받은 각 컴포넌트의 `is_member()` | 방의 토픽 구독을 놓고, 방 목록으로 보낸다 |

아래 두 줄이 따로 있는 것은 경계가 이미 마운트된 컴포넌트를 다시 인가하지 않기 때문이다
([live_session](../../docs/features/live-session.md#로그아웃과-기존-연결)). 훅은 마운트를 막을 뿐이고, 열린 페이지는
멤버십이 끝난 뒤에도 그 방을 듣고 있다.

**나가면 서버에서 끊는다.** 방 페이지의 세 컴포넌트는 방의 토픽과 함께 그 사용자의 멤버십 토픽
(`membership_topic`)을 듣는다. Leave(목록의 것이든 방 안의 것이든)는 멤버십을 지우고 `broadcast()`로 그 토픽에
알린다. `broadcast()`는 트랜잭션 안이면 커밋된 뒤에 보내므로([broadcast](../../docs/features/broadcast.md)) 알림을 받은
쪽이 다시 물을 때 삭제가 이미 보인다. `abroadcast()`는 트랜잭션과 상관없이 즉시 보내므로 async 코드에서 커밋 전에
부르면 안 된다. 알림은 "다시 물어보라"는 뜻일 뿐이다 — 받은 컴포넌트가 `is_member()`를
다시 묻고, 아니라면 `get_subscriptions()`에서 방의 토픽을 뺀다. 세션은 알림을 전한 뒤 모든 컴포넌트의
`get_subscriptions()`를 다시 읽어 아무도 원하지 않는 토픽의 구독을 해제하므로, 그때부터 그 소켓에는 방의 새
메시지도 접속자 변화도 오지 않는다. 방 컴포넌트는 접속자 목록에서도 빠지고 페이지를 방 목록으로 보낸다.
redirect는 브라우저에게 가 달라는 요청이라, 무시하고 소켓을 붙든 클라이언트도 이 끊김을 피하지 못한다.

**한계.** 끊기는 알림이 닿아야 일어난다.

- 알림이 닿지 않으면(채널 레이어가 메시지를 버리거나, 다른 프로세스인데 InMemory 레이어를 쓰는 경우) 열린 페이지는
  다음 연결까지 방의 새 메시지와 접속자를 계속 받는다. 쓰기와 typing은 핸들러가 다시 묻기 때문에 그래도 막힌다.
- 멤버십을 Leave 말고 다른 길로 지우면(admin, 셸의 ORM, 다른 앱 코드) 알림이 나가지 않는다. 그 길도
  `live.end_membership()`을 부르거나 `membership_topic`에 직접 알려야 한다. 결정적으로 끊는 공개 API는
  `invalidate_authentication()`뿐인데, 그것은 그 사용자의 모든 소켓(다른 방의 탭까지)을 닫는다.
- 알림이 닿아도 즉시는 아니다. Leave가 커밋된 뒤 열린 페이지가 알림을 처리할 때까지(밀리초 단위)는 그 사이에 쓴
  메시지가 아직 닿을 수 있다. 새 메시지와 알림은 다른 그룹으로 가므로, 알림 뒤에 쓴 메시지가 먼저 도착할 수도 있다.

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
- 다음 단계: [chat_lv3](../chat_lv3/) — 위로 스크롤해 과거 불러오기, 내 메시지 수정·삭제
- 경계: [live_session](../../docs/features/live-session.md), 훅: [라이프사이클 훅](../../docs/features/lifecycle-hooks.md)
- 핵심 파일: access.py · live_sessions.py · live.py · views.py
