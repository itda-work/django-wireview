# chat

**Streams와 Presence — 큰 목록을 상태에 담지 않고, 누가 있는지 보여준다**

## 무엇을 보여주나

- `stream()` / `stream_insert()`로 메시지를 개별 렌더
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

테스트는 `examples/chat/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/04-chat-app.md](../../docs/tutorials/04-chat-app.md)
- 핵심 파일: live.py · templates/chat/message_list_item.html (스트림 아이템)
