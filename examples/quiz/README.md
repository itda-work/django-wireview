# quiz

**컴포넌트 상태로 굴리는 상태 머신**

## 무엇을 보여주나

- `QuizState` (intro → playing → results) 전이를 서버가 소유
- 정답 판정은 서버에서 — 클라이언트가 보낸 값을 믿지 않는다
- 제출 결과가 리더보드 컴포넌트의 구독을 깨운다

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/quiz/>.

```bash
make test ARGS="-k quiz"
```

테스트는 `examples/quiz/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/13-quiz-app.md](../../docs/tutorials/13-quiz-app.md)
- 핵심 파일: live.py · templates/quiz/
