# todo

**모델 구독으로 여러 탭이 같은 목록을 함께 본다**

## 무엇을 보여주나

- `_subscriptions = {"todo.item"}` + `mutation()` — ORM 변경이 곧 화면 갱신
- 중첩 컴포넌트: 목록·항목·카운터가 각자의 컴포넌트
- `skip_render()` / `force_render()`로 렌더 횟수 제어
- `{% on 'keypress.enter' %}`, `{% class %}`, `{% cond %}`

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/>.

```bash
make test ARGS="-k todo"
```

테스트는 `examples/todo/tests.py`이고 브라우저 테스트가 함께 들어 있다 (`make test-e2e LAYER=memory ARGS="-k todo"`). CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/03-todo-app.md](../../docs/tutorials/03-todo-app.md)
- 핵심 파일: live.py · templates/todo/ · models.py
