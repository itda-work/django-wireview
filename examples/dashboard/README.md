# dashboard

**AsyncResult — 느린 조회가 첫 렌더를 막지 않는다**

## 무엇을 보여주나

- `assign_async()`로 카드마다 자기 데이터를 따로 불러온다
- 여러 컴포넌트를 한 페이지에 조합 (카드·피드·탭)
- `wire.params`로 탭과 기간을 URL에 남긴다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/dashboard/>.

```bash
make test ARGS="-k dashboard"
```

테스트는 `examples/dashboard/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/05-dashboard.md](../../docs/tutorials/05-dashboard.md)
- 핵심 파일: live.py · templates/dashboard/
