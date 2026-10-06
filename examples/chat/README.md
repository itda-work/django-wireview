# chat

**Streams와 Presence — 큰 목록을 상태에 담지 않고, 누가 있는지 보여준다**

## 무엇을 보여주나

- `stream()`으로 메시지를 개별 렌더하고, 새 메시지는 `Broadcast`로 그 방의 모든 페이지에 넣는다 — 보내는 곳에서 한 번
  렌더하고, 받는 페이지에서는 코드가 돌지 않는다([Broadcast](../../docs/features/broadcast.md))
- 목록은 DOM에서 최신이 앞이고 `flex-direction: column-reverse`로 아래부터 그린다. 새 메시지를 `at=0`에 넣으면
  페이지마다 스크롤 명령 없이 맨 아래에 붙어 있다
- `PresenceMixin`, `PresenceTrackerMixin`으로 접속자·타이핑 표시
- 메시지 목록과 입력창을 분리해 구독 범위를 좁힌다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/chat/>.

```bash
make test ARGS="-k chat"
```

테스트는 `examples/chat/tests.py`. `make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/04-chat-app.md](../../docs/tutorials/04-chat-app.md)
- 핵심 파일: live.py · templates/chat/message_list_item.html (스트림 아이템)
