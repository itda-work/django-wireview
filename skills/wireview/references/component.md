# 컴포넌트

`from wireview import Component, LiveComponent, JS, broadcast`

## 상태

Pydantic v2 모델이다. 필드가 곧 상태이고, 서명된 `data-state`로 클라이언트와 왕복한다.

```python
class XTodoList(Component):
    class Meta:
        template_name = "todo/list.html"

    showing: Showing = Showing.ALL          # JSON 직렬화 가능해야 한다
    item: Item | None = None                # 모델 인스턴스는 pk로 서명되고 재join 때 다시 읽힌다
    recent: list[Item] = []                 # list·dict·AsyncResult 안이어도 같다. 목록은 쿼리 한 번

    @property
    def items(self):                        # 큰 QuerySet은 필드가 아니라 property로
        return Item.objects.filter(...)
```

상태에 남는 것은 행의 정체(pk)라 재join 때 그 순간의 행을 읽는다. 그 사이 삭제된 행은 목록에서 빠지고
단일 필드는 `None`이 된다. 타입 표기가 무엇을 다시 읽을지 정하므로 `list`가 아니라 `list[Item]`으로 적는다.

설정은 클래스 안의 `class Meta:`에 둔다. 하위 클래스는 적지 않은 키를 부모에게서 물려받고,
모르는 키나 옛 밑줄 이름(`_template_name` 등)은 `TypeError`다.

| `Meta` 키 | 뜻 |
|---|---|
| `template_name` | 템플릿 경로. **필수** — 없으면 렌더할 때 `ImproperlyConfigured` |
| `subscriptions` | 구독 채널 집합. `{"todo.item"}`은 Item 모델 전체 변경. 상태에 따라 달라지면 `def get_subscriptions(self) -> set[str]`를 오버라이드한다 |
| `temporary_assigns` | 렌더 후 기본값으로 되돌릴 필드 이름들. **기본값이 있는 필드만** 대상. 되돌림은 변경이 아니라 다음 렌더가 화면에서 지우지 않는다. 서명 상태에 실리지 않으므로 **`joined()`에서 불러온다** |
| `exclude_fields` | 상태 직렬화에서 **더** 뺄 필드. `user`·`wire`·`session`은 항상 빠진다. `data-state`는 서명만 되고 암호화되지 않아 브라우저에서 읽히므로 비밀은 여기로 뺀다 |
| `slots` | 슬롯 정의 (`references/templates.md`) |
| `on_mount` | 마운트 시 실행할 훅 클래스 목록 |
| `live_sessions` | 마운트될 수 있는 `live_session` 이름들 |
| `presence` | `PresenceMixin`의 `PresenceConfig` |
| `sticky` | `True`면 boost 이동으로 같은 컴포넌트가 있는 페이지에 가도 인스턴스·DOM·훅이 이어진다 |

`self.user`(요청 사용자), `self.wire`(클라이언트 명령 채널), `self.session`(Django 세션,
**읽기 전용**)은 항상 있다. `self.wire.params`는 URL 쿼리 파라미터다.
익명 방문자 식별은 `self.session.session_key` — 세션을 만드는 것은 뷰만 할 수 있다
(`request.session.create()`). 세션 쓰기는 `TypeError`다: 소켓에는 `Set-Cookie`를 실을 응답이 없다.

## 라이프사이클

전부 `async def`다.

| 메서드 | 언제 |
|---|---|
| `joined()` | WebSocket 연결 후 첫 진입. 구독 설정, Streams 초기화, `allow_upload()` 자리 |
| `leaving()` | 연결 해제. 정리 훅. `joined()`가 돈 인스턴스만 받는다 — 얻는 일은 `joined()`에 둔다 |
| `mutation(channel, action, instance)` | `Meta.subscriptions`의 모델이 변경됨. `action`은 `ModelAction.CREATED/UPDATED/DELETED`, m2m 변경이면 `ADDED/REMOVED/CLEARED` |
| `notification(channel, **kwargs)` | `broadcast(channel, ...)`로 보낸 사용자 정의 알림 |
| `params_changed(params, uri)` | 같은 경로에서 쿼리가 바뀜 (같은 경로의 `push_to`·`replace_to`, 그 항목 사이의 뒤로가기). 같은 인스턴스가 받으므로 상태가 남는다. 쿼리가 있는 페이지를 열 때는 첫 HTTP 렌더(렌더 전, `joined()` 없이)와 join(`joined()` 뒤)에서 한 번씩, 두 번 돈다 — 같은 params면 같은 상태를 내게 쓰고, 한 번만 할 일은 핸들러에 둔다. 첫 HTTP 렌더에서 시작한 `assign_async`·`start_async`는 취소되고 join이 다시 시작한다 |

```python
from wireview import ModelAction

class XTodoList(Component):
    class Meta:
        subscriptions = {"todo.item"}

    async def mutation(self, channel, action, instance):
        if action == ModelAction.DELETED:
            self.skip_render()
```

`instance`는 알림에 실린 값에서 복원한 보통의 모델 인스턴스다. 저장하면 보통의 저장(모델의 `save()`, 시그널)이라
**그 저장이 다시 알림이 되어 `mutation()`이 또 불린다** — 받을 때마다 무조건 `asave()`하면 끝없이 돈다. 값이 다를 때만
저장하거나, 시그널을 내지 않는 `QuerySet.update()`를 쓴다. `asave(update_fields=[...])`는 루프를 막지 않는다(그것도 알림이다).
그것은 다른 컬럼을 페이로드의 옛 값으로 덮지 않으려 할 때 쓴다 — 저장은 페이로드에 실린 필드를 모두 쓰기 때문이다.
페이로드에 없는 필드(다중 테이블 상속의 부모 필드 등)는 deferred라 `await instance.arefresh_from_db(fields=["name"])`처럼
필드를 적어 먼저 읽는다(필드를 적지 않으면 deferred 필드는 건너뛴다).
상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/settings.md#모델-알림

## 이벤트 핸들러

`_`로 시작하지 않고 **직접 정의한** async 메서드가 핸들러로 노출된다. 인자는 템플릿의
`{% on %}` kwargs와 폼 필드에서 온다.

```python
    async def add(self, new_item: str = ""):     # 타입 힌트대로 검증된다
        if not new_item.strip():
            return
        await Item.objects.acreate(text=new_item)

    def _slugify(self, text: str) -> str:        # `_` 접두사 = 클라이언트에 노출 안 됨
        ...
```

인자는 클라이언트에서 온 값이다. 타입 검증은 자동이지만 **권한 검사는 직접** 한다.

## 렌더 제어와 클라이언트 명령

| 호출 | 효과 |
|---|---|
| `self.skip_render()` / `self.force_render()` | 이번 이벤트의 렌더를 건너뛰거나 강제 |
| `await self.send_render()` | 지금 다시 렌더해서 보낸다 |
| `await self.destroy()` | 이 컴포넌트를 DOM에서 제거 |
| `await self.focus_on(selector)` | 포커스 |
| `await self.scroll_into_view(element_id, behavior="smooth")` | 스크롤 |
| `await self.push_title(title)` | 문서 제목 |
| `await self.put_flash(...)` / `await self.clear_flash()` | 플래시 메시지 |
| `await self.push_js(JS().add_class("#row", "shake"))` | 클라이언트 DOM 명령 |
| `await self.push_event(name, payload)` | JavaScript Hook으로 이벤트 전달 |
| `await self.wire.push_to(url)` / `replace_to` / `redirect_to` | 내비게이션. `push_to`·`replace_to`는 같은 경로(쿼리만 다름)면 patch — 가져오지 않고 같은 인스턴스가 `params_changed()`를 받는다 — 다른 경로면 그 페이지를 가져온다. `redirect_to`는 언제나 가져온다. 컴포넌트 밖 템플릿이 `request.GET`을 읽는 페이지는 `redirect_to`로 |

`JS()` 빌더: `show`, `hide`, `toggle`, `add_class`, `remove_class`, `toggle_class`,
`transition`, `set_attr`, `remove_attr`, `set_value`, `focus`, `focus_first`, `push`,
`navigate`, `dispatch`. 체이닝된다. 첫 인자는 대상 선택자다(`add_class("#row", "shake")`).

## 브로드캐스트

컴포넌트 밖(뷰, 셀러리 태스크 등)에서는 모듈 함수를 쓴다.

```python
from wireview import broadcast
broadcast("room.42", event="new_message")

# 컴포넌트 안에서
await self.broadcast("room.42", event="new_message")
```

받는 쪽은 `Meta.subscriptions = {"room.42"}` + `async def notification(self, channel, **kwargs)`.
모델 변경 자동 브로드캐스트는 `WIREVIEW["AUTO_BROADCAST"]`가 켜고 끈다. 알릴 모델은
`senders={("todo", "Item")}`처럼 반드시 적는다 — 비우면 아무것도 알리지 않는다(`wireview.W015`). 집합으로 적은
모델은 모든 필드가 채널 레이어로 직렬화된다. `User`처럼 민감한 필드가 있는 모델은 매핑으로 보낼 필드를 적는다:
`senders={("todo", "Item"): "__all__", ("auth", "User"): ("username",)}`(`()`는 pk만, 비밀번호 해시가 실리면 `wireview.W017`).
세션 모델은 pk가 세션 키라 필드를 적어도 W017이다 — `senders`에 넣지 않는다.
그때 `mutation()`의 `instance`에서 적지 않은 필드는 deferred라, 읽으려면 `await instance.arefresh_from_db(fields=[...])`.

## 토스트 — 다른 사람·다른 탭에 띄우는 플래시

`put_flash()`는 이벤트를 처리한 그 연결에만 뜬다. 뷰·시그널·백그라운드 작업이나 다른 사용자의 행동에서
**받는 사람의 열린 페이지 전부**에 띄우려면 토스트를 쓴다. 받는 쪽은 레이아웃에 `{% wireview_toasts %}`
한 줄(그리고 `[wire-flash]` 컨테이너)이면 된다.

```python
from wireview import atoast, toast

toast(user, "다시 오신 것을 환영합니다")                      # 동기 코드. 커밋 뒤에 나간다
await atoast(user, "지금 회의 들어와요", flash_type="warning")  # 비동기 코드. 바로 나간다
toast(request.session.session_key, "장바구니에 담았습니다")      # 로그인 전 방문자
```

그 태그가 없는 페이지와 닫힌 탭에는 가지 않고 나중에 다시 오지도 않는다. 상세:
https://github.com/itda-work/django-wireview/blob/main/docs/features/flash.md

## 비동기 작업

느린 조회로 첫 렌더를 막지 않는다.

```python
from wireview import AsyncResult

class Dashboard(Component):
    stats: AsyncResult | None = None
    results: list[str] = []

    async def joined(self):
        # 즉시 로딩 상태로 렌더하고, 끝나면 다시 렌더한다
        self.stats = await self.assign_async(self._fetch_stats())

    async def search(self, query: str):
        await self.start_async("search", self._do_search(query))   # 이름 붙은 태스크
                                                                   # 같은 이름이면 앞의 것을 취소하고 교체
    async def handle_async(self, name: str, result: AsyncResult):  # start_async 완료 콜백
        if name == "search":                                       # 끝난 상태(ok 또는 failed)로만 온다.
            self.results = result.result if result.ok else []      # 취소된 작업은 부르지 않는다

    async def cancel_search(self):
        await self.cancel_async("search")
```

템플릿에서는 `{% if stats.loading %}` / `{{ stats.result }}` / `{{ stats.error_message }}`로 분기한다.
태스크는 컴포넌트가 떠나면(탭 닫힘, 요소 제거, 재연결) 취소되고 이어지지 않는다. 재연결 뒤에도 필요하면 `joined()`에서 다시 시작한다.
상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/async-operations.md

## LiveComponent

부모의 연결을 공유하면서 자기 상태를 가지는 중첩 컴포넌트. 부모와의 통신은
`send_to_parent`로 한다. 부모가 넘긴 값이 바뀌면 자식의 `update(**assigns)`가 불린다.

목록의 행마다 LiveComponent가 있고 각자 `update()`에서 조회하면 부모 렌더 한 번에 조회가 행 수만큼 돈다(N+1).
그럴 때는 클래스 메서드 `update_many()`를 오버라이드해 **값이 바뀐 같은 클래스 자식 전부**를 한 번에 받는다.

```python
class Row(LiveComponent):
    class Meta:
        template_name = "rows/row.html"

    item_id: int
    title: str = ""

    @classmethod
    async def update_many(cls, updates):              # [(component, 바뀐 assigns), ...]
        await super().update_many(updates)            # 각자의 update()로 새 값을 반영한다
        ids = [component.item_id for component, _ in updates]
        titles = {pk: t async for pk, t in Item.objects.filter(pk__in=ids).values_list("pk", "title")}
        for component, _ in updates:
            component.title = titles.get(component.item_id, "")
```

새로 생긴 자식은 `update_many()`가 아니라 `joined()`를 받는다. 상세:
https://github.com/itda-work/django-wireview/blob/main/docs/features/live-component.md

## 라이프사이클 훅 (`Meta.on_mount`, `attach_hook`)

인증·추적처럼 여러 컴포넌트에 공통으로 얹는 것. 마운트를 중단시킬 수 있고, 중단하면 그 컴포넌트는
HTML도 상태도 내보내지 않는다.
상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/lifecycle-hooks.md

## 페이지 경계 (`live_session`)

인증이 페이지 전체에 걸리는 것이면 컴포넌트마다 훅을 붙이지 말고 경계를 선언한다. 같은 술어가
뷰(첫 HTML 전)와 WebSocket join 두 곳에서 돌고, 경계를 벗어나는 boost 이동은 전체 페이지 로드가
된다.

```python
# myapp/live_sessions.py
from wireview import live_session

admin = live_session("admin", authorize=lambda ctx: ctx.user.is_staff)

# myapp/views.py
@admin.view
def dashboard(request): ...

# myapp/live.py
class AdminPanel(Component):
    class Meta:
        live_sessions = {"admin"}  # 이 경계 밖에서는 렌더되지 않는다
```

상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/live-session.md
