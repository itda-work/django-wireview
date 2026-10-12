# rating

**잠깐 쓰는 상태(hover)와 남는 상태(점수)를 갈라 둔다**

![rating: 별 위를 지나가는 미리보기는 잠깐 쓰는 상태, 누른 별점은 저장되어 통계가 바뀐다](media/rating.webp)

<sub>별 위를 지나가는 미리보기는 잠깐 쓰는 상태, 누른 별점은 저장되어 통계가 바뀐다. 마지막 화면은 [media/rating.png](media/rating.png), 다시 만들려면 `make example-media ARGS="--only rating"`.</sub>

## 무엇을 보여주나

- hover는 컴포넌트 상태에만, 점수는 DB에 (`aupdate_or_create`)
- 키보드 이벤트와 `{% on 'mouseenter' %}`
- 세션 키는 페이지가 넘겨준다 — 소켓은 Django 세션을 들고 있지 않다

## 돌려보기

처음이면 [시작하기](../README.md#시작하기)를 먼저 따라 한다(uv 설치, 의존성, JS 빌드, migrate). 그 뒤로는 서버만 띄운다. WebSocket이 필요하므로 `runserver`가 아니라 daphne다.

```shell
uv run --directory tests daphne testproj.asgi:application
```

브라우저에서 <http://localhost:8000/rating/>.

```shell
uv run pytest examples/rating -m "not e2e"
```

테스트는 `examples/rating/tests.py`. `make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/11-rating-app.md](../../docs/tutorials/11-rating-app.md)
- 핵심 파일: live.py · views.py · templates/rating/
