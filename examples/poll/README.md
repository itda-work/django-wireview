# poll

**쓰기는 핸들러가, 다시 그리기는 브로드캐스트가**

## 무엇을 보여주나

- 투표는 DB에 쓰고 `skip_render()` — 재렌더는 `mutation()`이 받는다
- 이미 투표했으면 아무것도 하지 않는다 (서버가 판단)
- `wire.params`로 선택을 URL에 남겨 새로고침을 견딘다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/poll/>.

```bash
make test ARGS="-k poll"
```

테스트는 `examples/poll/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/10-poll-app.md](../../docs/tutorials/10-poll-app.md)
- 핵심 파일: live.py · templates/poll/poll.html
