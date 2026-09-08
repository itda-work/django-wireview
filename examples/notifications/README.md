# notifications

**이름 붙인 채널로 컴포넌트끼리 알린다**

## 무엇을 보여주나

- `broadcast("notifications-refresh")` → 종 아이콘의 `notification()`
- 목록은 Streams, 배지는 별도 컴포넌트
- `JS()` 체이닝으로 사라지는 애니메이션

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/notifications/>.

```bash
make test ARGS="-k notifications"
```

테스트는 `examples/notifications/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/14-notifications.md](../../docs/tutorials/14-notifications.md)
- 핵심 파일: live.py · templates/notifications/
