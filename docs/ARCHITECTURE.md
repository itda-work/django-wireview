# django-wireview 아키텍처

> 현재 구조. 모듈별 한 줄 지도는 저장소의 `CLAUDE.md`, 메시지 형태의 정본은
> [implementation/wire-protocol.md](./implementation/wire-protocol.md), 기능별 API는 [features/](./features/README.md)에 있다.
> 이 문서는 그것들을 잇는 흐름을 설명한다.

---

## 1. 전체 구조

### 1.1 구성 요소

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Browser                                                                     │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  wireview.js                                                       │     │
│  │  ├─ ServerConnection      # WebSocket 연결, 재연결, 메시지 처리    │     │
│  │  │   ├─ open()            # /__wireview__?vsn=<n> 연결             │     │
│  │  │   ├─ joinAllComponents # 페이지의 루트 컴포넌트 join            │     │
│  │  │   └─ _processMessage   # render·stream_op·exec_js ... 처리      │     │
│  │  ├─ WireviewComponent     # 컴포넌트 하나의 렌더 상태             │     │
│  │  │   ├─ join()            # 서명 상태(data-state)로 서버에 등록    │     │
│  │  │   ├─ applyDiff()       # diff를 렌더 상태에 접고 다음 프레임에 morph │
│  │  │   ├─ dispatch()        # user_event 전송                        │     │
│  │  │   └─ serialize()       # 폼 데이터 직렬화                       │     │
│  │  ├─ 위임 리스너           # <html>이 wire-on-* 속성을 찾아 실행    │     │
│  │  ├─ HookManager, UploadManager, ViewportObserver, FeedbackManager  │     │
│  │  └─ rendered.mjs 등       # diff 적용·HTML 복원 순수 함수          │     │
│  │  wireview-boost.js                                                 │     │
│  │  └─ morph()               # idiomorph 래퍼, 내비게이션·히스토리    │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                              ↕ WebSocket (JSON 프레임)                       │
│  Django Server                                                               │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  WireviewConsumer (consumer.py)   # Channels 어댑터일 뿐            │     │
│  │  ├─ websocket_connect()   # Origin·채널 레이어 검사 후 accept      │     │
│  │  ├─ connect()/disconnect()  # 세션 start()/stop()                  │     │
│  │  └─ receive_json()        # → WireviewSession.handle_message()     │     │
│  │                                                                    │     │
│  │  WireviewSession (session.py)     # 세션 로직 전부. channels 없음   │     │
│  │  ├─ command_join()        # 서명 상태 검증, 경계 검사, 마운트      │     │
│  │  ├─ command_user_event()  # 사용자 이벤트 처리                     │     │
│  │  ├─ component_*()         # 컴포넌트가 보낸 세션 메일              │     │
│  │  ├─ model_mutation(), notification(), upload_*()  # fan-out 수신   │     │
│  │  └─ send_render()         # 부모 렌더 + 자식 LiveComponent 수명주기 │     │
│  │                                                                    │     │
│  │  ComponentRepository (repository.py)                               │     │
│  │  ├─ join()                # 컴포넌트 인스턴스 생성·등록            │     │
│  │  ├─ dispatch_event()      # 핸들러 호출 (판정: core/handlers.py)   │     │
│  │  ├─ take_lifecycle()      # LiveComponent joined/update/leaving 배치 │    │
│  │  └─ components_subscribed_to()  # 구독 관리                        │     │
│  │                                                                    │     │
│  │  Component (core/component.py)                                     │     │
│  │  ├─ Pydantic v2 BaseModel                                          │     │
│  │  ├─ wire: WireviewMeta    # 렌더 상태, 클라이언트 명령 (core/meta.py) │  │
│  │  └─ 라이프사이클 훅       # joined, leaving, mutation, notification │    │
│  │                                                                    │     │
│  │  Rendered (core/rendered.py)   # static/dynamic 분리 diff          │     │
│  │  Outbound·Broker (core/transport.py)  # 채널 레이어를 만지는 유일한 곳 │ │
│  └────────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 파일별 역할

| 파일 | 역할 |
|------|------|
| `__init__.py` | 공개 API의 전부. 하위 모듈은 모두 내부다([COMPATIBILITY.md](./COMPATIBILITY.md)) |
| `core/component.py` | `Component` 베이스: 라이프사이클, 이벤트, streams·uploads·async·flash·hooks 메서드, `class Meta:` 해석(`ComponentOptions`) |
| `core/handlers.py` | 클라이언트가 부를 수 있는 메서드의 판정 정본 `is_client_callable`. 디스패처·시스템 체크·`validate_call` 감싸기가 함께 쓴다 (#127) |
| `core/meta.py` | `WireviewMeta` (`self.wire`): 렌더 diff 계산, 내비게이션·flash·JS 등 클라이언트 명령 |
| `core/rendered.py` | 동적 마커로 나눈 static/dynamic 구조와 diff |
| `core/state.py`, `core/signing.py` | `data-state` 서명·복원, 서명 키 |
| `core/model_state.py` | 상태 안의 모델 인스턴스를 pk로 서명하고 필드 타입 표기를 따라 다시 읽는다 (#113) |
| `core/render_reads.py`, `core/render_gate.py` | 초기화된 temporary assign을 읽은 동적 부분 찾기(#111), 워커 스레드 렌더 중 백그라운드 작업 미루기(#138) |
| `core/session.py` | `SessionView`: Django 세션의 읽기 전용 뷰. 소켓에서는 connect 때 한 번 읽는다 |
| `core/live_session.py` | 페이지 경계(`live_session`)와 인증 세대 |
| `core/origin.py` | WebSocket Origin 검사 (#96) |
| `core/transport.py` | `Outbound`·`Broker` 인터페이스와 Channels 구현 |
| `template_engine.py` | 템플릿 변수 출력에 diff 마커 주입 |
| `consumer.py` | `WireviewConsumer`: Channels WebSocket 어댑터. 소켓을 받고 세션을 시작·종료한다 |
| `session.py` | `WireviewSession`: 메시지 라우팅, 렌더 전송, 업로드·브로드캐스트 수신. `Outbound`로만 내보낸다 (#60) |
| `repository.py` | `ComponentRepository`: 연결당 컴포넌트 인스턴스, 핸들러 호출, LiveComponent 수명주기 배치 |
| `live_component.py` | `LiveComponent`: 부모 연결을 공유하는 중첩 상태 컴포넌트 |
| `js.py` | `JS()` 클라이언트 명령 빌더 |
| `features/` | streams, presence, uploads(레지스트리·토큰·청크 저장소), hooks, toasts(`{% wireview_toasts %}`) |
| `auto_broadcast.py` | Django 시그널 → 컴포넌트 `mutation()` |
| `templatetags/wireview.py` | 템플릿 태그 전체 |
| `static/wireview/wireview.js` | 클라이언트 연결, 컴포넌트, 이벤트 위임, 훅, 업로드 |
| `static/wireview/*.mjs` | diff 적용, 스트림, 이벤트 수정자, 입력값 보존 등 순수 함수 |
| `static/wireview/wireview-boost.js` | idiomorph 래퍼, 내비게이션, 히스토리 |

### 1.3 데이터 흐름

```
사용자 클릭
    │
    ▼
┌─────────────────┐
│ 위임 리스너     │  <html>이 click을 받아 wire-on-click 속성을 찾고
│ (wire-on-click) │  wireview.send(element, 'increment', {})
└────────┬────────┘
         │
         ▼
┌──────────────────┐
│ WireviewComponent│  dispatch(command, args, formScope)
│   .dispatch()    │  serialize() → 폼 데이터 수집
└────────┬─────────┘
         │
         ▼
┌─────────────────┐
│ ServerConnection│  sendUserEvent(id, command, implicit, explicit, ref)
│   ._send()      │  JSON.stringify → WebSocket.send
└────────┬────────┘
         │
         ▼ WebSocket
┌──────────────────┐
│ WireviewConsumer │  receive_json → WireviewSession.handle_message
│ → WireviewSession│  → command_user_event(id, command, ...)
└────────┬─────────┘
         │
         ▼
┌─────────────────┐
│ ComponentRepo   │  dispatch_event(id, command, args, kwargs)
│ .dispatch_event │  await component.increment(**kwargs)  (validate_call)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Component       │  self.count += 1
│ .increment()    │  (Pydantic 대입 검증)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ WireviewSession │  send_render(component, ref=...)
│ .send_render()  │  → WireviewMeta.render_diff()
│                 │    템플릿 렌더(마커 포함) → Rendered.from_marked_html
│                 │    → get_diff(이전 Rendered, vsn) → 바뀐 dynamic만
└────────┬────────┘
         │
         ▼ WebSocket  {"command": "render", "payload": {"id", "diff", "children"?, "ref"?}}
┌─────────────────┐
│ ServerConnection│  _processMessage → case "render"
└────────┬────────┘
         │
         ▼
┌──────────────────┐
│ WireviewComponent│  applyDiff(diff) → applyPhoenixDiff (rendered.mjs)
│ .applyDiff()     │  → currentHtml() → boost.morph(element, html)
└──────────────────┘
```

---

## 2. 핵심 클래스

### 2.1 Component (core/component.py)

```python
class Component(BaseModel):
    """Pydantic v2 기반 컴포넌트"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,   # 선언하지 않은 필드에 대입하면 ValidationError
        ignored_types=(type,),      # 클래스 값 속성(class Meta: 포함)은 필드가 아니다
    )

    # 클래스 레벨 레지스트리
    _all: ClassVar[dict[str, type["Component"]]] = {}
    _name: ClassVar[str]
    _meta: ClassVar[ComponentOptions]  # class Meta: 를 부모 것과 키 단위로 합친 결과 (#99)

    # 인스턴스 상태 (user·wire·session은 Meta.exclude_fields와 무관하게 직렬화하지 않는다)
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: AnonymousUser | AbstractBaseUser
    wire: WireviewMeta
    session: SessionView

    # 라이프사이클 (모두 async)
    async def joined(self): ...
    async def leaving(self): ...
    async def mutation(self, channel, action, instance): ...
    async def notification(self, channel, **kwargs): ...
    async def params_changed(self, params, uri): ...
```

- `__init_subclass__`가 클래스명으로 전역 등록하고(`Component._all`), `class Meta:`를 `ComponentOptions`로 해석해
  `cls._meta`에 둔다.
- 클라이언트가 부를 수 있는 것은 `_`로 시작하지 않고 사용자 코드에서 정의한 메서드뿐이다. 프레임워크와 Pydantic이
  소유한 이름(`joined`, `update`, `model_post_init` 등)은 오버라이드해도 빠진다. 판정의 정본은
  `wireview/core/handlers.py`의 `is_client_callable` 하나이고, 디스패처(`ComponentRepository.dispatch_event`)·
  시스템 체크·`validate_call` 감싸기가 모두 이 모듈의 같은 판정을 쓴다(#127).
- 상태 필드는 렌더마다 `{% tag_header %}`의 `data-state`에 서명되어 실리고, join 때 그것으로 복원된다
  (`core/state.py`, v2 봉투).

### 2.2 WireviewMeta (core/meta.py)

```python
class WireviewMeta:
    """렌더링 상태 및 서버 통신 관리"""

    _last_rendered: Rendered | None  # 마지막으로 보낸 렌더 (diff 기준)
    _is_frozen: bool                 # freeze() 뒤로 렌더 중지
    _skip_render: bool               # 다음 렌더 한 번 건너뜀
    _redirected_to: str | None       # 내비게이션이 예약됐으면 렌더하지 않음

    # 렌더링
    async def render_diff(self, component, repo) -> DiffPayload | None
    def render(self, component, repo) -> SafeText | None

    # 서버 → 클라이언트 명령
    async def send(self, _command, **kwargs)
    async def redirect_to(self, to, **kwargs)
    async def push_to(self, to, **kwargs)
    async def replace_to(self, to, **kwargs)
    async def push_js(self, component_id, js)
    async def put_flash(self, flash_type, message, ...)
```

`render_diff()`는 async 속성을 먼저 풀고 템플릿을 동기 컨텍스트에서 한 번 렌더한 뒤
`Rendered.from_marked_html()`로 static과 dynamic을 나누고, 이전 `Rendered`와 비교해 바뀐 dynamic만 담은
payload를 돌려준다. 바뀐 것이 없으면 `None`이다. 첫 렌더와 static이 바뀐 렌더는 `{"s", "d", "f"}` 전체를 보낸다.
형태는 [features/html-diff.md](./features/html-diff.md).

`joined()` 동안의 명령은 대기열(`enter_pending_mode`/`flush_pending`)에 쌓였다가 첫 렌더 뒤에 나간다.

### 2.3 WireviewSession (session.py)과 WireviewConsumer (consumer.py)

세션 로직은 전부 `WireviewSession`에 있고, 내보내는 것은 `Outbound`로만 한다. 이 모듈은 channels를 import하지
않는다(`tests/test_session_extraction.py`). `WireviewConsumer`는 그것을 상속한 Channels 어댑터로, 소켓을 받거나
거절하고(`websocket_connect`), 세션을 시작·종료하고(`connect`·`disconnect` → `start`·`stop`), JSON 프레임을
`handle_message`에 넘길 뿐이다(#60). 채널 레이어 메일은 Channels가 `type`의 이름으로 세션의 메서드에 보낸다.

```python
class WireviewSession:
    # 프론트엔드 명령: handle_message가 command_<name>(**payload)로 보낸다
    async def command_join(self, name, state, children=None, ref=None): ...
    async def command_leave(self, id): ...
    async def command_user_event(self, id, command, implicit_args, explicit_args, ref=None): ...
    async def command_hook_event(self, component_id, hook_id, event, payload, ref=None): ...
    async def command_params_changed(self, params, uri): ...
    async def command_upload_register(self, id, name, entries): ...   # upload_cancel, upload_complete

    # 컴포넌트 → 자기 세션 (message_from_component가 component_<command>로 보낸다)
    async def component_remove(self, id, ref=None): ...
    async def component_stream_op(self, op, stream, items, at, limit=0): ...
    async def component_exec_js(self, id, commands): ...
    async def component_crashed(self, id): ...                       # 백그라운드 작업이 던졌다
    # ... url_change, title, flash, upload_op, dispatch_event, send_render 등

    # fan-out 수신 (Broker.publish의 type)
    async def model_mutation(self, data): ...
    async def notification(self, data): ...
    async def upload_progress(self, event): ...                      # upload_completed, upload_error
    async def session_invalidated(self, event): ...                  # 로그아웃: 소켓을 4001로 닫는다

class WireviewConsumer(AsyncJsonWebsocketConsumer, WireviewSession): ...
```

- 컴포넌트 코드에서 난 예외는 `_crashed()`(이벤트)와 `_join_failed()`(join)가 받는다. 소켓을 닫지 않고 그 컴포넌트만
  버린다([features/errors.md](./features/errors.md)).
- 렌더를 보내는 경로는 모두 `send_render()`를 지난다. 부모 렌더 뒤에 자식 LiveComponent의 `joined()`·`update()`·
  `leaving()`을 처리하고, 자식 diff를 같은 `render` 메시지의 `children`으로 보낸다
  ([design/live-component-ownership.md](./design/live-component-ownership.md)).

---

## 3. 기능 모듈

### 3.1 JS 명령 (js.py)

`JS()`는 브라우저에서 실행할 명령을 체인으로 쌓는다. 각 명령은 `{"cmd": <이름>, ...}` 딕셔너리이고,
값이 `None`인 인자는 빠진다. `transition`은 문자열·`(클래스, ms)` 튜플·딕셔너리를 받아 `{"transition", "time"?}`
형태로 정규화한다.

```python
>>> from wireview import JS
>>> JS().show("#modal", transition=("fade-in", 200)).push("opened").to_json()
'[{"cmd": "show", "to": "#modal", "transition": {"transition": "fade-in", "time": 200}}, {"cmd": "push", "event": "opened"}]'
```

템플릿은 인자를 받는 호출을 못 하므로 템플릿에서 쓰는 체인은 컴포넌트의 `@property`가 만들고
`{% on "click" this.open_modal %}`처럼 넘긴다. 서버에서 바로 실행하려면 `await self.push_js(js)`이고,
`exec_js` 명령(`id`, `commands`)으로 나간다. 상세는 [implementation/js-commands.md](./implementation/js-commands.md)와 [features/optimistic-ui.md](./features/optimistic-ui.md).

### 3.2 Streams (features/streams.py)

`await self.stream(name, items)`는 항목마다 항목 템플릿(`item` 컨텍스트)을 렌더해 `StreamOp`를 만들고
`stream_op` 명령으로 보낸다. 항목은 컴포넌트 상태에 남지 않는다. `stream_insert`·`stream_delete`가 같은 경로를 쓴다.

```python
@dataclass
class StreamOp:
    op: Literal["reset", "insert", "delete"]
    stream: str
    items: list[StreamItem] = field(default_factory=list)   # StreamItem(dom_id, html)
    at: int = -1        # -1 끝, 0 앞, n 위치
    limit: int = 0      # DOM에 남길 최대 항목 수 (0 = 제한 없음)
```

### 3.3 클라이언트 훅 이벤트

`await self.push_event(event, payload=None, hook_id=None)`는 `push_event` 명령(`component_id`, `hook_id`,
`event`, `payload`)으로 나가고, `hook_id`가 없으면 그 컴포넌트의 모든 훅이 받는다. 훅이 보낸 이벤트는
`hook_event`로 들어와 `handle_hook_event()`에 닿는다([features/hooks.md](./features/hooks.md)).

### 3.4 업로드 (features/uploads.py, features/upload_store.py, views.py)

`joined()`에서 `self.allow_upload(name, accept=..., max_entries=..., max_file_size=...)`로 설정한다. 브라우저가
`upload_register`를 보내면 서버가 검증하고 서명 토큰을 발급하며, 청크는 WebSocket이 아니라 HTTP 엔드포인트
`UploadView`로 간다. 엔드포인트는 상태가 없고 토큰에서 계산한 경로에 쓴다. 완료된 파일은
`async for` 로 `consume_uploads()`에서 꺼낸다. 상세는 [features/chunked-uploads.md](./features/chunked-uploads.md).

### 3.5 전송 계층 (core/transport.py)

채널 레이어는 이 모듈에서만 만진다. 세션으로 가는 출력은 `Outbound.send_command`, 세션들 사이의 fan-out은
`Broker.publish`, 컴포넌트에서 자기 세션으로 보내는 메시지는 `Broker.send_to_session`이다. 다른 연결 계층은
이 두 인터페이스만 구현하면 된다([design/transport-abstraction.md](./design/transport-abstraction.md)).

---

## 4. 디렉터리 구조

```
wireview/
├── __init__.py            # 공개 API (_EXPORTS 표로 지연 로딩)
├── core/                  # component, handlers, meta, rendered, render_reads, render_gate, state, signing,
│                          # model_state, session(SessionView), live_session, origin, transport
├── features/              # streams, presence, uploads, upload_store, hooks, toasts
├── consumer.py  session.py  repository.py  live_component.py  function_components.py  slots.py
├── template_engine.py  event_transpiler.py  js.py  async_result.py  auto_broadcast.py
├── views.py  urls.py  apps.py  settings.py  checks.py  telemetry.py  testing.py  deprecation.py
├── schemas.py  serializer.py  utils.py  log.py
├── component.py           # 폐기 예정 re-export (2.0에서 제거)
├── debug/                 # sync_detector (DEBUG_SYNC_TRANSITIONS)
├── templatetags/wireview.py
├── management/commands/   # wireview_stubs, wireview_lsp, wireview_agent_setup, wireview_upload_gc
├── project_template/      # startproject --template 스타터
├── templates/wireview_header.html  templates/wireview/toasts.html
└── static/wireview/
    ├── wireview.js        # 소스 (wireview.min.js는 make build-js의 산출물)
    ├── wireview-boost.js
    ├── rendered.mjs  streams.mjs  targets.mjs  events.mjs  values.mjs  live-session.mjs  ready.mjs  reload.mjs
    ├── loading.mjs  navigation.mjs  reconnect.mjs  uploads.mjs  joins.mjs
    └── types.d.ts
```

---

## 5. 프로토콜

모든 프레임은 `{"command": str, "payload": {...}}` JSON이다. 명령 목록과 payload의 정본은
[implementation/wire-protocol.md](./implementation/wire-protocol.md)다. 요약하면:

```typescript
// Client → Server
{ command: "join", payload: { name, state, children, ref? } }
{ command: "leave", payload: { id } }
{ command: "user_event", payload: { id, command, implicit_args, explicit_args, ref? } }
{ command: "hook_event", payload: { component_id, hook_id, event, payload, ref } }  // ref: "hook-<n>" | null
{ command: "params_changed", payload: { params, uri } }
// upload_register, upload_cancel, upload_complete

// Server → Client
{ command: "render", payload: { id, diff, children?, ref?, vsn?, instances? } }
{ command: "remove", payload: { id, ref? } }
{ command: "joined", payload: { id, ref? } }
{ command: "error", payload: { id, during, ref? } }
{ command: "stream_op", payload: { op, stream, items, at, limit? } }
{ command: "exec_js", payload: { id, commands } }
{ command: "push_event", payload: { component_id, hook_id, event, payload } }
{ command: "url_change", payload: { command, url } }
// reload, focus_on, title, flash, upload_op, hook_reply, set_query_string ...
```

diff 형태를 새로 더하면 `PROTOCOL_VERSION`을 올리고, 서버는 클라이언트가 `?vsn=`으로 말한 버전 이하의 형태만 보낸다.

---

## 6. 성능 고려사항

### 6.1 메모리

```python
# 상태 필드: 렌더마다 서명되어 data-state에 실린다
class Feed(Component):
    items: list[dict] = []   # 1000개면 1000개가 메모리와 data-state에 있다

# Streams: 항목은 렌더되어 클라이언트로 가고 상태에 남지 않는다
class Feed(Component):
    async def joined(self):
        items = [item async for item in Item.objects.all()[:1000]]
        await self.stream("items", items)
```

### 6.2 대역폭

템플릿의 변수 출력마다 마커가 붙고, 렌더는 static과 dynamic으로 나뉜다. 첫 렌더 뒤에는 바뀐 dynamic만 간다.

```python
# 전체 렌더 (첫 렌더, static이 바뀐 렌더)
diff = {"s": ['<div class="count">', "</div>"], "d": ["5"], "f": "<fingerprint>"}

# 부분 렌더
diff = {"0": "6"}
```

### 6.3 렌더링

```python
# skip_render()로 불필요한 렌더 방지 (동기 메서드)
async def handle_click(self):
    if not self.should_update:
        self.skip_render()
        return

# temporary_assigns로 렌더 후 메모리 해제
class Report(Component):
    large_data: list[dict] = []

    class Meta:
        temporary_assigns = {"large_data"}

    async def joined(self):
        self.large_data = await get_large_data()
        # 렌더 후 선언한 기본값([])으로 돌아간다. 기본값이 없는 필드는 건드리지 않는다
```

상세 수치는 [PERFORMANCE.md](./PERFORMANCE.md).
