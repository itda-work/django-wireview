# 컴포넌트

`from wireview import Component, LiveComponent, JS, broadcast`

## 상태

Pydantic v2 모델이다. 필드가 곧 상태이고, 서명된 `data-state`로 클라이언트와 왕복한다.

```python
class XTodoList(Component):
    _template_name = "todo/list.html"

    showing: Showing = Showing.ALL          # JSON 직렬화 가능해야 한다
    item: Item | None = None                # Django 모델 필드는 pk로 직렬화·복원된다

    @property
    def items(self):                        # 큰 QuerySet은 필드가 아니라 property로
        return Item.objects.filter(...)
```

클래스 변수로 동작을 바꾼다.

| 클래스 변수 | 뜻 |
|---|---|
| `_template_name` | 템플릿 경로. 생략하면 클래스명에서 유추한다 |
| `_subscriptions` | 구독 채널 집합. `{"todo.item"}`은 Item 모델 전체 변경 |
| `_temporary_assigns` | 렌더 후 기본값으로 되돌릴 필드 이름들. **기본값이 있는 필드만** 대상 |
| `_exclude_fields` | 상태 직렬화에서 뺄 필드. 기본 `{"user", "wire"}` |
| `_slots` | 슬롯 정의 (`references/templates.md`) |
| `_on_mount` | 마운트 시 실행할 훅 클래스 목록 |

`self.user`(요청 사용자)와 `self.wire`(클라이언트 명령 채널)는 항상 있다.
`self.wire.params`는 URL 쿼리 파라미터다.

## 라이프사이클

전부 `async def`다.

| 메서드 | 언제 |
|---|---|
| `joined()` | WebSocket 연결 후 첫 진입. 구독 설정, Streams 초기화, `allow_upload()` 자리 |
| `leaving()` | 연결 해제. 정리 훅 |
| `mutation(channel, action, instance)` | `_subscriptions`의 모델이 변경됨. `action`은 `ModelAction.CREATED/UPDATED/DELETED` |
| `notification(channel, **kwargs)` | `broadcast(channel, ...)`로 보낸 사용자 정의 알림 |
| `params_changed(params, uri)` | 브라우저 URL이 바뀜 (뒤로가기, `push_to`) |

```python
from wireview.schemas import ModelAction

class XTodoList(Component):
    _subscriptions = {"todo.item"}

    async def mutation(self, channel, action, instance):
        if action == ModelAction.DELETED:
            self.skip_render()
```

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
| `await self.push_js(JS().add_class("shake", to="#row"))` | 클라이언트 DOM 명령 |
| `await self.push_event(name, payload)` | JavaScript Hook으로 이벤트 전달 |
| `await self.wire.push_to(url)` / `replace_to` / `redirect_to` | 내비게이션 (앞의 둘은 연결 유지) |

`JS()` 빌더: `show`, `hide`, `toggle`, `add_class`, `remove_class`, `toggle_class`,
`transition`, `set_attr`, `remove_attr`, `set_value`, `focus`, `focus_first`, `push`,
`navigate`, `dispatch`. 체이닝된다.

## 브로드캐스트

컴포넌트 밖(뷰, 셀러리 태스크 등)에서는 모듈 함수를 쓴다.

```python
from wireview import broadcast          # 동기 컨텍스트
broadcast("room.42", event="new_message")

# 컴포넌트 안에서
await self.broadcast("room.42", event="new_message")
```

받는 쪽은 `_subscriptions = {"room.42"}` + `async def notification(self, channel, **kwargs)`.
모델 변경 자동 브로드캐스트는 `WIREVIEW["AUTO_BROADCAST"]`가 켜고 끈다.

## 비동기 작업

느린 조회로 첫 렌더를 막지 않는다.

```python
from wireview import AsyncResult

class Dashboard(Component):
    stats: AsyncResult | None = None

    async def joined(self):
        # 즉시 로딩 상태로 렌더하고, 끝나면 다시 렌더한다
        self.stats = await self.assign_async(self._fetch_stats())

    async def search(self, query: str):
        await self.start_async("search", self._do_search(query))   # 이름 붙은 태스크
                                                                   # 같은 이름이면 앞의 것을 취소하고 교체
    async def handle_async(self, name, result):                    # start_async 완료 콜백
        ...

    async def cancel_search(self):
        await self.cancel_async("search")
```

템플릿에서는 `{% if stats.loading %}` / `{{ stats.result }}` / `{{ stats.error_message }}`로 분기한다.
상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/async-operations.md

## LiveComponent

부모의 연결을 공유하면서 자기 상태를 가지는 중첩 컴포넌트. 부모와의 통신은
`send_to_parent`로 한다. 상세:
https://github.com/itda-work/django-wireview/blob/main/docs/features/live-component.md

## 라이프사이클 훅 (`_on_mount`, `attach_hook`)

인증·추적처럼 여러 컴포넌트에 공통으로 얹는 것. 마운트를 중단시킬 수 있다.
상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/lifecycle-hooks.md
