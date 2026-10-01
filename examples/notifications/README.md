# notifications

**알림은 한 사용자에게 간다. 저장하는 알림과 저장하지 않는 토스트**

## 무엇을 보여주나

- `get_subscriptions()`가 `self.user`의 채널만 부른다 — 다른 사람에게 보낸 것은 이 연결에 오지 않는다
- 알림(패턴 A): `Notification` 행을 저장하면 자동 브로드캐스트가 받는 사람의 채널
  `auth.user.<pk>.notifications`로 알리고, 목록(Streams)과 벨 배지가 바뀐다
- 토스트(패턴 B): 저장 없이 `atoast(user, ...)`로 보내고, 레이아웃의 `{% wireview_toasts %}`가 `put_flash()`로 띄운다
- 브라우저가 보낸 id는 믿지 않는다: 모든 핸들러가 소유자로 거른 쿼리에서 시작한다
- `JS()` 체이닝으로 사라지는 애니메이션

보내는 함수와 채널 이름은 `services.py` 한곳에 있다.

## 돌려보기

```bash
make build-js          # 최초 1회
make run-daphne        # WebSocket이 필요하므로 runserver가 아니다
```

브라우저에서 <http://localhost:8000/notifications/>를 열고 alice로 로그인한다. 시크릿 창에서 같은 주소를
열고 bob으로 로그인한다 — 쿠키를 공유하는 창은 같은 사용자다. alice 창을 새로고침해 받는 사람 목록에
bob이 보이면, bob에게 알림과 토스트를 보내 본다. `/notifications/sign-in/<이름>/`은 데모용이다.

```bash
make test ARGS="-k notifications"                   # 단위
make test-e2e ARGS="-k notifications"               # 브라우저 둘로 alice와 bob
```

테스트는 `examples/notifications/tests.py`. `make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 튜토리얼: [docs/tutorials/14-notifications.md](../../docs/tutorials/14-notifications.md)
- 플래시와 토스트의 경계: [docs/features/flash.md](../../docs/features/flash.md)
- 핵심 파일: live.py · services.py · templates/notifications/
