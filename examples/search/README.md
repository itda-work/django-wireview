# search

**디바운스한 입력, 주소에 남는 검색어, 키보드로 고르는 결과 목록**

## 무엇을 보여주나

- `{% on 'input.debounce.300' %}` — 타이핑마다 질의하지 않는다
- 검색어를 `push_to("?q=…")`로 주소에 남기고 결과는 `params_changed()`가 낸다 — 새로고침·공유한 링크·뒤로 가기가 같은 결과를 보여 준다
- 위/아래 키로 결과를 순환 (`navigate`), Enter로 선택
- `push_js(JS()...)`로 입력값을 비우는 등 DOM만의 일은 클라이언트에

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/search/>. 검색한 뒤 새로고침하거나 뒤로 가기를 눌러 본다.

```bash
make test ARGS="-k search"
```

테스트는 `examples/search/tests.py`. `make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 이 예제는 조용히 낡지 않는다. 브라우저로 입력·새로고침·뒤로 가기를 도는 E2E(`TestQueryInTheAddress`)는 `make test-e2e`가 돌린다.

## 더 읽기

- 튜토리얼: [docs/tutorials/12-live-search.md](../../docs/tutorials/12-live-search.md)
- 핵심 파일: live.py · templates/search/live_search.html
