# rating

**잠깐 쓰는 상태(hover)와 남는 상태(점수)를 갈라 둔다**

## 무엇을 보여주나

- hover는 컴포넌트 상태에만, 점수는 DB에 (`aupdate_or_create`)
- 키보드 이벤트와 `{% on 'mouseenter' %}`
- 세션 키는 페이지가 넘겨준다 — 소켓은 Django 세션을 들고 있지 않다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/rating/>.

```bash
make test ARGS="-k rating"
```

테스트는 `examples/rating/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/11-rating-app.md](../../docs/tutorials/11-rating-app.md)
- 핵심 파일: live.py · views.py · templates/rating/
