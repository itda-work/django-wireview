# 예제

각 디렉터리가 **개념 하나**를 가르치는 작은 Django 앱이다. 읽는 순서는 위에서 아래.

| 예제 | 개념 | 튜토리얼 |
|------|------|----------|
| [todo](./todo/) | 모델 구독으로 여러 탭이 같은 목록을 함께 본다 | [03](../docs/tutorials/03-todo-app.md) |
| [poll](./poll/) | 쓰기는 핸들러가, 다시 그리기는 브로드캐스트가 | [10](../docs/tutorials/10-poll-app.md) |
| [rating](./rating/) | 잠깐 쓰는 상태와 남는 상태를 갈라 둔다 | [11](../docs/tutorials/11-rating-app.md) |
| [search](./search/) | 디바운스한 입력과 키보드로 고르는 결과 | [12](../docs/tutorials/12-live-search.md) |
| [quiz](./quiz/) | 컴포넌트 상태로 굴리는 상태 머신 | [13](../docs/tutorials/13-quiz-app.md) |
| [chat](./chat/) | Streams와 Presence | [04](../docs/tutorials/04-chat-app.md) |
| [dashboard](./dashboard/) | AsyncResult로 느린 조회를 미룬다 | [05](../docs/tutorials/05-dashboard.md) |
| [notifications](./notifications/) | 이름 붙인 채널로 컴포넌트끼리 알린다 | [14](../docs/tutorials/14-notifications.md) |
| [livecomp](./livecomp/) | 연결을 공유하는 중첩 컴포넌트 | [15](../docs/tutorials/15-live-components.md) |
| [slots](./slots/) | 내용을 호출자가 채우는 레이아웃 컴포넌트 | [기능 문서](../docs/features/slots.md) |

## 예제는 테스트다

예제마다 `tests.py`가 있고 `make test`가 그것을 함께 돌린다. CI가 매 푸시마다 실행하므로,
라이브러리가 바뀌어 예제가 깨지면 문서가 아니라 **빌드가** 먼저 알려 준다. 실제로 이
디렉터리로 옮기면서 테스트를 붙였을 때 네 개의 예제가 이미 고장 나 있었다.

예제는 `tests/`의 Django 프로젝트(`testproj`) 위에서 돈다. 설정과 URLconf는 거기에 있고,
여기에는 앱만 있다.

## 돌려보기

```bash
make install          # 의존성
make build-js         # wireview.min.js (gitignore 대상이라 clone 직후 필수)
make run-daphne       # http://localhost:8000
```

WebSocket이 필요하므로 `runserver`가 아니라 daphne로 띄운다.

```bash
make test                        # 예제 테스트까지 전부
make test ARGS="-k quiz"         # 예제 하나
make test-e2e LAYER=memory       # 브라우저 (todo, livecomp, bookmarks)
```
