# live_session — 페이지 경계

`live_session`은 **페이지 단위의 인증 경계**다. 이름 하나에 정책 하나를 묶고, 그 이름을 단 페이지에
사는 모든 컴포넌트가 그 정책을 지나게 한다. 그리고 경계를 넘는 이동을 전체 페이지 로드로 만든다.

Phoenix LiveView의 `live_session`에 해당한다(GAP-009). 다만 붙는 자리가 다르다 — Phoenix는 LiveView가
곧 라우트지만, wireview는 Django 뷰가 라우트이고 한 페이지에 컴포넌트가 여럿이다. 그래서 경계는
컴포넌트 사이가 아니라 **페이지 사이**에 그어진다.

| | `_on_mount` | `live_session` |
|---|---|---|
| 붙는 곳 | 컴포넌트 클래스 | 페이지(Django 뷰) |
| 답하는 질문 | "이 컴포넌트가 마운트될 때 무엇을 먼저 하나" | "이 페이지에 들어올 수 있는가, 어디서 나갈 때 연결을 끊어야 하나" |
| 실행 순서 | 세션 훅 **다음** | 컴포넌트 훅보다 **먼저** |
| 없으면 | 컴포넌트마다 같은 목록을 반복 선언 | 페이지 경계에서 인증이 재검증되지 않는다 |

`live_session`은 `_on_mount`를 대체하지 않고 그 위에 층을 얹는다. 훅 프로토콜은 같다.

## 30초 요약

```python
# myapp/live_sessions.py  — 앱마다 자동 임포트된다
from wireview import live_session

admin = live_session("admin", authorize=lambda ctx: ctx.user.is_staff)
```

```python
# myapp/views.py
from .live_sessions import admin

@admin.view
def dashboard(request):
    return render(request, "admin/dashboard.html")
```

```python
# myapp/live.py
class AdminPanel(Component):
    _live_sessions = {"admin"}   # 이 경계 안에서만 산다
```

이제 네 가지가 성립한다.

1. 인증되지 않은 요청은 **HTML이 만들어지기 전에** 로그인 페이지로 간다.
2. 같은 판정이 WebSocket join에도 적용된다 — 같은 술어 하나가 두 곳에서 돈다.
3. `AdminPanel`은 `admin` 밖의 페이지에서는 아예 렌더되지 않는다.
4. `admin`을 벗어나는 boost 이동은 morph가 아니라 전체 페이지 로드가 된다.

## 왜 훅만으로는 부족한가

`_on_mount`에 인증 훅을 붙이면 컴포넌트마다 붙여야 하고, 하나 빠뜨려도 아무 신호가 없다. 그보다
중요한 문제가 둘 더 있다.

**첫 HTML은 회수되지 않는다.** `{% component %}`는 HTTP 응답에 초기 렌더와 `data-state`를 같이
싣는다. join에서 거절해도 이미 나간 바이트는 돌아오지 않는다. 그래서 `@admin.view`는 **실제로
Django 인증을 거는 데코레이터**이고, 거절은 뷰가 돌기 전에 일어난다.

**위조가 필요 없는 우회가 있다.** 정책 이름만 따로 서명하면, 공개 페이지의 **정상** 서명과 보호
컴포넌트의 **정상** 상태를 함께 보내는 것으로 빈 정책이 적용된다. 그래서 정책 이름과 인증 세대는
각 컴포넌트의 상태 봉투 **안에** 들어간다([HTML Diff](./html-diff.md)의 v2 봉투).

## API

### `live_session(name, *, authorize=None, on_mount=(), login_url=None)`

경계를 선언하고 `name`으로 등록한다. 이름은 브라우저로 나가고 서명된 상태 안에 들어가므로 배포
사이에 안정적이어야 하고 프로젝트 안에서 유일해야 한다. 같은 이름을 두 번 선언하면
`ImproperlyConfigured`다.

| 인자 | 뜻 |
|------|-----|
| `authorize` | `LiveSessionContext`를 받아 bool을 돌려주는 술어. 뷰가 렌더하기 전에 한 번, join이 마운트하기 전에 한 번 돈다. `None`이면 모두 허용 |
| `on_mount` | 이 페이지의 모든 컴포넌트에 적용되는 훅 목록. 컴포넌트 자신의 `_on_mount`보다 **먼저** 돈다 |
| `login_url` | 익명 방문자를 보낼 곳. 기본은 Django의 `LOGIN_URL` |

선언은 각 앱의 `live_sessions.py`에 둔다 — `live.py`와 같은 방식으로 앱 준비 시점에 자동
임포트되므로 손으로 import할 필요가 없다.

### `LiveSessionContext`

```python
admin = live_session("admin", authorize=lambda ctx: ctx.user.is_staff and ctx.session.get("mfa"))
```

| 필드 | 뜻 |
|------|-----|
| `user` | 요청(또는 연결)의 사용자. `None`이 아니다 — 비인증이면 `AnonymousUser` |
| `session` | Django 세션의 읽기 전용 뷰 ([세션 읽기](./session.md)) |

**술어는 동기 함수다.** 뷰에서도 join에서도 같은 함수가 불려야 하기 때문이다. ORM을 건드려도 된다 —
join은 `sync_to_async`로 감싸서 부른다.

### `@session.view`

함수 뷰와 클래스 기반 뷰 둘 다 받는다.

```python
@admin.view
def dashboard(request): ...

@admin.view
class Dashboard(TemplateView): ...
```

두 가지를 한다. `authorize`가 거절하면 뷰를 부르지 않고, 통과하면 요청에 세션 이름을 남겨
`{% wireview_header %}`와 그 페이지가 발급하는 모든 `data-state`가 그것을 싣게 한다.

거절의 모양은 `django.contrib.auth`를 따른다 — 로그인하지 않은 방문자는 로그인 페이지로 보내고
(로그인이 부족한 전부일 수 있으므로), 로그인했는데도 술어를 통과하지 못하면 403이다.

### `Component._live_sessions`

```python
class AdminPanel(Component):
    _live_sessions = {"admin"}
```

"나는 이 세션 안에서만 산다"는 선언이다. 비워 두면 어디서나 산다(경계가 생기기 전의 기본값).
선언하면 **경계가 없는 페이지에서도 거절된다** — 공개 페이지가 바로 그 모습이기 때문이다.

거절은 컴포넌트를 만드는 **모든 경로**에서 일어난다: join, children 복원, 부모 렌더가 만든
LiveComponent, 재join. 거절된 컴포넌트는 HTML도 상태도 내보내지 않고 저장소에서도 지워진다 —
그 id로 이벤트를 보내도 처리되지 않는다.

## 경계를 넘는 이동

`{% wireview_header %}`가 페이지의 세션 이름을 meta로 심는다.

```html
<meta name="wireview-live-session" content="admin" />
```

클라이언트는 boost 이동마다 목적지 문서의 이름을 현재 페이지의 것과 비교하고, 다르면 morph하지
않고 **평범한 페이지 이동**을 한다. WebSocket이 끊기고 새 핸드셰이크가 지금의 쿠키로 다시 선다.

이 규칙이 없으면 이렇게 된다: 로그인 상태로 관리자 페이지를 열고, 로그아웃하고, boost로 관리자
페이지로 돌아간다 → 연결은 로그인 시점에 세운 그대로다.

검사는 세 진입점이 합류하는 곳 하나에 있다.

| 경로 | 어디서 시작하나 |
|------|-----------------|
| 링크 클릭 | boost의 클릭 인터셉터 |
| 뒤로/앞으로(`popstate`) | 브라우저. 캐시된 body를 morph 예약한 뒤 fetch한다 |
| 서버 `redirect`/`push` | `wire.push_to()` 등이 `HistoryCache`를 직접 부른다 |

셋 다 `HistoryCache.replaceContentFromUrl`로 모이고, 검사는 거기서 **응답**을 보고 한다(요청한 URL이
아니라). 리다이렉트 체인이 경계 밖에서 끝나면 그 최종 페이지가 잡힌다. popstate만은 캐시된 body를
fetch보다 먼저 morph하므로 history 항목에 기록해 둔 세션 이름으로 그 전에 판단한다.

메타가 없는 문서는 "경계 없음"으로 읽힌다. 업그레이드 전에 만들어진 페이지, 캐시된 옛 페이지,
wireview가 아닌 페이지 셋 다 그렇고, 경계 안에서 그런 곳으로 가는 것은 전체 로드다.

## 로그아웃과 기존 연결

전체 페이지 로드는 **정상 클라이언트가 문맥을 갱신하는 수단**일 뿐, 누군가 열어 둔 소켓을 폐기하지
못한다. 그래서 두 갈래가 있다.

**새 연결**: 상태 봉투에는 발급 당시의 **인증 세대**가 들어 있다. 사용자 pk가 아니라 세션 키와
`_auth_user_hash`를 함께 해시한 값이라, 로그인(`login()`이 키를 돌린다)·로그아웃(`flush()`)·비밀번호
변경에서 값이 바뀐다. 값이 다르면 join이 거절되고 클라이언트는 reload한다.

**기존 연결**: `user_logged_out` 시그널이 그 인증 세대의 토픽으로 발행하고, 그 토픽을 듣고 있던
소켓이 닫힌다. 경계 안(live_session이 있는 페이지)의 연결만 그 토픽을 구독한다 — 밖에서는 상태가
인증에 묶여 있지 않으므로 무효화할 것이 없고, 무관한 로그아웃에 공개 페이지의 소켓까지 닫힐 이유가
없다.

다중 프로세스에서는 **브로커가 필요하다**. InMemory 채널 레이어는 같은 프로세스의 연결에만 닿으므로
다른 워커가 들고 있는 소켓은 닫히지 않는다([배포](../DEPLOYMENT.md)).

**술어는 연결이 열려 있는 동안 다시 묻지 않는다.** join 시점에 한 번 판정하고, 그 뒤로는 로그아웃이
소켓을 닫는 것이 갱신 경로다. 그래서 로그아웃을 거치지 않는 권한 변경은 다음 연결에서야 반영된다 —
`is_staff`를 떼거나, `update_session_auth_hash()` 없이 비밀번호를 바꾸는 경우가 그렇다(둘 다 Django가
시그널을 내지 않는다). 즉시 끊어야 한다면 직접 부른다.

```python
from wireview.core.live_session import invalidate_authentication

user.is_staff = False
user.save()
invalidate_authentication(user, request.session)   # 그 인증 세대의 소켓을 닫는다
```

객체 하나에 대한 권한은 애초에 경계의 일이 아니다. 페이지에 들어왔다는 것이 그 안의 모든 행을
건드릴 권한을 뜻하지 않으므로, 그것은 핸들러에서 검사한다.

## 페이지 하나에 세션 하나

연결도 마찬가지다. 첫 join이 연결의 경계를 정하고, 다른 이름을 실은 join은 거절된다. 한 소켓에 두
정책을 섞는 것이 공개 정책으로 보호 컴포넌트를 덮는 방법이기 때문이다.

## 점검

`manage.py check`가 `wireview.W010`으로 둘을 본다 — 아무도 선언하지 않은 이름을 `_live_sessions`가
가리키는 경우(오타가 join 거절과 reload로 나타나 서명 문제처럼 보인다), 그리고 프로젝트가 경계를
선언했는데 `_on_mount`로만 자신을 지키는 컴포넌트가 `_live_sessions`를 선언하지 않은 경우.
[시스템 체크](./checks.md) 참고.

## 옛 페이지와의 호환

상태 봉투가 v2로 올라간다. 업그레이드 시점에 열려 있던 페이지의 v1 토큰은 거절되고 클라이언트가
reload한다 — 새 페이지는 현재 인증 문맥으로 다시 렌더되고 새 토큰을 받는다. 롤아웃 창이 필요하면
`WIREVIEW["STATE_ACCEPT_LEGACY"]`로 v1을 받을 수 있지만, v1은 경계를 모르므로 "경계 없음"으로
디코드된다 — 정책이 걸린 페이지는 그래도 거절한다.

## Phoenix LiveView 대응

| | Phoenix LiveView | django-wireview |
|---|---|---|
| 선언 | 라우터의 `live_session :admin, on_mount: [...]` | `live_session("admin", authorize=..., on_mount=[...])` |
| 적용 | 라우트 묶음 | Django 뷰(`@session.view`) |
| 소속 선언 | LiveView가 라우트에 속한다 | `Component._live_sessions` |
| 경계 이동 | live navigation이 전체 로드로 퇴화 | boost가 전체 로드로 퇴화 |
| 로그아웃 | 소켓에 disconnect 브로드캐스트 | `user_logged_out` → 인증 토픽 발행 → 소켓 닫힘 |

## 관련

- [라이프사이클 훅](./lifecycle-hooks.md) — `_on_mount`와 역할 구분
- [세션 읽기](./session.md) — `authorize`가 보는 `ctx.session`
- [시스템 체크](./checks.md) — `wireview.W010`
- [HTML Diff](./html-diff.md) — `data-state` 봉투
- [설계 메모](../design/live-session.md) — 위협 모델과 적대적 리뷰 두 라운드
