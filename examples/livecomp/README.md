# livecomp

**부모의 연결을 공유하면서 자기 상태를 갖는 중첩 컴포넌트**

## 무엇을 보여주나

- `{% live_component %}` + `{% live_tag_header %}`
- `myself=True`로 자기 자신에게 이벤트를 보낸다
- `send_to_parent()`로 부모에게 알리고, 부모가 자식을 갱신한다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/livecomp/>.

```bash
make test ARGS="-k livecomp"
```

테스트는 `examples/livecomp/tests.py`이고 브라우저 테스트가 함께 들어 있다 (`make test-e2e LAYER=memory ARGS="-k livecomp"`). CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/15-live-components.md](../../docs/tutorials/15-live-components.md)
- 핵심 파일: components.py · templates/livecomp/
