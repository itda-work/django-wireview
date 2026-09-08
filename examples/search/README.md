# search

**디바운스한 입력과 키보드로 고르는 결과 목록**

## 무엇을 보여주나

- `{% on 'input.debounce.300' %}` — 타이핑마다 질의하지 않는다
- 위/아래 키로 결과를 순환 (`navigate`), Enter로 선택
- `push_js(JS()...)`로 입력값을 비우는 등 DOM만의 일은 클라이언트에

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/search/>.

```bash
make test ARGS="-k search"
```

테스트는 `examples/search/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/12-live-search.md](../../docs/tutorials/12-live-search.md)
- 핵심 파일: live.py · templates/search/live_search.html
