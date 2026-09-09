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

> **`django.template.context_processors.request`가 켜져 있어야 한다.** 템플릿 태그는 페이지의 경계를
> `context["request"]`에서 읽는다. 이 프로세서가 없으면 **기능 전체가 말없이 꺼진다** — 헤더가 빈
> 이름을 심어 브라우저가 어떤 이동도 경계 넘음으로 보지 않고, 모든 상태가 경계 없이 서명되며,
> `_live_sessions`를 선언한 컴포넌트는 자기가 속한 페이지에서 사라진다. 뷰 데코레이터는 그대로
> 동작하므로 문이 열리는 것은 아니지만, 경계의 나머지가 없어진다. `manage.py check`의
> `wireview.W010`이 잡는다.

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

**언제 도는가.** 뷰에서는 요청마다, 소켓에서는 **join마다** 돈다(페이지에 루트 컴포넌트가 여럿이면
각각). 다만 소켓의 `user`와 `session`은 connect 때 읽은 스냅샷이므로, 술어가 `ctx.user.is_staff`처럼
그 객체의 속성만 보면 연결이 열려 있는 동안 값이 갱신되지 않는다. 술어가 직접 DB를 조회하면 이후
join에서는 최신 값을 본다. 이미 마운트된 컴포넌트로 오는 **이벤트는 재인가하지 않는다** — 아래
[로그아웃과 기존 연결](#로그아웃과-기존-연결) 참고.

### `@session.view`

함수 뷰(동기·`async def` 둘 다)와 클래스 기반 뷰를 받는다.

```python
@admin.view
def dashboard(request): ...

@admin.view
async def feed(request): ...

@admin.view
class Dashboard(TemplateView): ...
```

`async def` 뷰에는 async wrapper가 붙는다. Django는 넘겨받은 호출 가능 객체를 검사해서 뷰를 어떻게
부를지 정하므로, 동기 wrapper로 감싸면 응답 자리에 await되지 않은 coroutine이 들어간다.

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
LiveComponent, 부모 템플릿 안의 중첩 `{% component %}`, 재join. 거절된 컴포넌트는 HTML도 상태도
내보내지 않고 저장소에서도 지워진다 — 그 id로 이벤트를 보내도 처리되지 않는다.

**훅이 예외를 던져도 거절이다.** 인가 조회가 DB 오류로 실패하는 것이 거절보다 통과하기 쉬워서는
안 된다. 예외는 그대로 올라가거나(join·HTTP 렌더) 로그에 남지만(LiveComponent 자식), 어느 쪽이든
컴포넌트는 렌더되지 않고 저장소에도 남지 않는다.

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

**인증 세대는 nonce다.** `login()`이 세션에 `_wireview_auth_gen`을 새로 찍고, 상태 봉투의 `a`는
사용자 pk·그 nonce·`_auth_user_hash`를 해시한 값이다. 세션 **키**는 일부러 넣지 않았다 —
signed-cookie 백엔드에서 `session_key`는 서명된 쿠키 문자열 전체라, 장바구니 하나만 써도 값이 바뀐다.
그 위에 지문을 세우면 인증과 무관한 이유로 열린 페이지가 reload되고, 나중의 로그아웃은 아무도 듣지
않는 토픽으로 발행된다.

이제 세 갈래다.

**새 연결**: 봉투의 `a`가 지금 연결의 인증 세대와 다르면 join이 거절되고 클라이언트는 reload한다.

**join 시점 재확인**: 경계에 처음 들어가는 join은 세션을 **백엔드에서 다시 읽고, 읽은 것으로
스냅샷을 교체한다**. connect 때 읽은 스냅샷은 그 뒤의 로그아웃을 알 수 없고, 클라이언트는 소켓을
열어 둔 채 첫 join을 미룰 수 있기 때문이다. 지문만 대조하지 않고 교체하는 이유는 인증 말고 다른
것을 읽는 정책 때문이다 — `authorize=lambda ctx: ctx.session.get("mfa")`는 지문을 움직이지 않으므로
대조로는 잡히지 않고, 사라진 값을 그대로 통과시킨다. 연결당 한 번이고, 경계 밖에서는 아예 하지
않는다.

**기존 연결**: `user_logged_out`이 그 세대의 토픽으로 발행하고, 그 토픽을 듣고 있던 소켓이 닫힌다.
경계 안의 연결만 구독한다 — 밖에서는 상태가 인증에 묶여 있지 않으므로 무효화할 것이 없고, 무관한
로그아웃에 공개 페이지의 소켓까지 닫힐 이유가 없다.

로그아웃 없이 **다시 로그인**하는 경우(step-up, 재인증)도 같다. `login()`이 nonce를 덮어쓰므로 이후의
로그아웃은 옛 세대를 부를 수 없다 — 그래서 덮어쓰기 직전에 옛 세대로 한 번 발행한다. 다만 **다른
사용자**가 같은 브라우저에서 로그인하면 Django가 세션을 먼저 flush하므로 그 시점에 옛 nonce가 이미
없다. 그 경우 옛 세대를 끊는 것은 먼저 로그아웃하는 것뿐이다.

**발행은 최선 노력이지 배달 보장이 아니다.** Channels 채널 레이어 규격은 용량 초과 시 조용한 폐기와
그룹 멤버십 만료를 허용한다. 즉시 끊는 것은 이 발행이 하고, 놓친 경우를 회수하는 것은 다음 연결의
join 재확인이다. 두 경로를 각각 다른 보장으로 읽어야 한다 — "모든 소켓이 즉시 닫힌다"는 보장은 없다.

다중 프로세스에서는 **브로커가 필요하다**. InMemory 채널 레이어는 같은 프로세스의 연결에만 닿으므로
다른 워커가 들고 있는 소켓은 닫히지 않는다([배포](../DEPLOYMENT.md)).

### 세션 백엔드가 정하는 한계

| 백엔드 | 로그아웃 뒤 **새 연결** | 로그아웃 뒤 **열린 소켓** |
|---|---|---|
| db / cache / cached_db | 막힌다 (세션이 지워져 join 재확인이 잡는다) | 발행이 닿으면 닫힌다 |
| signed_cookies | **막지 못한다** | 발행이 닿으면 닫힌다 |

signed-cookie 세션은 서버에 아무 기록이 없다. Django 자신이
[문서](https://docs.djangoproject.com/en/6.0/topics/http/sessions/#using-cookie-based-sessions)에서
로그아웃으로 폐기되지 않는다고 밝힌다 — 로그인 시점의 쿠키를 보관했다가 로그아웃 뒤에 다시 제출하면
같은 사용자로 인증된다. 재확인이 다시 읽을 서버 상태가 없으므로 wireview도 이것을 막지 못한다.
**경계 뒤에 진짜 인가가 걸린 페이지가 있다면 서버 저장형 세션 백엔드를 쓴다.**

**이미 마운트된 컴포넌트의 이벤트는 재인가하지 않는다.** 술어는 join마다 돌지만 이벤트마다 돌지는
않고, connect 때 읽은 사용자·세션 스냅샷을 본다. 로그아웃은 소켓을 닫는 것이 갱신 경로다. 그래서
로그아웃을 거치지 않는 권한 변경은 다음 연결에서야 반영된다 —
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

`manage.py check`가 `wireview.W010`으로 넷을 본다 — `django.template.context_processors.request`가
꺼져 있는 경우(위), 아무도 선언하지 않은 이름을 `_live_sessions`가
가리키는 경우(오타가 join 거절과 reload로 나타나 서명 문제처럼 보인다), 프로젝트가 경계를 선언했는데
`_on_mount`로만 자신을 지키는 컴포넌트가 `_live_sessions`를 선언하지 않은 경우, 그리고
`STATE_ACCEPT_LEGACY`가 경계와 함께 켜져 있는 경우(아래).
[시스템 체크](./checks.md) 참고.

## 옛 페이지와의 호환

상태 봉투가 v2로 올라간다. 업그레이드 시점에 열려 있던 페이지의 v1 토큰은 거절되고 클라이언트가
reload한다 — 새 페이지는 현재 인증 문맥으로 다시 렌더되고 새 토큰을 받는다.

**`STATE_ACCEPT_LEGACY`와 경계는 동시에 열 수 없다.** live_session이 하나라도 선언된 프로젝트에서는
이 플래그가 적용되지 않고 옛 토큰은 그대로 거절된다. 옛 토큰은 경계 이름을 담고 있지 않은데, 토큰
안에는 "이 페이지에 경계가 있었는지"를 말해 주는 것도 없다. 소속을 선언한 컴포넌트라면 마운트를
거절해서 끝나지만, 선언하지 않은 컴포넌트는 연결의 경계를 "없음"으로 정해 버리고 **뷰에 붙인
`authorize`와 세션 훅이 통째로 빠진다**. 둘을 구분할 근거가 토큰에 없으므로 경계가 있는 쪽이
reload를 택한다.

경계를 도입하는 배포에서 롤아웃 창이 꼭 필요하다면 순서를 나눈다 — 먼저 v2만 배포해 옛 토큰을
`STATE_MAX_AGE` 동안 소진시키고, 그다음 배포에서 live_session을 켠다.

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
