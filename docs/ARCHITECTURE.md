# django-wireview 아키텍처

> 현재 구조 분석 및 개선 방향

---

## 1. 현재 아키텍처 (v5.3.0)

### 1.1 전체 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           현재 아키텍처 (v5.3.0)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Browser                                                                     │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  wireview.js                                                        │     │
│  │  ├─ ServerConnection      # WebSocket 연결 관리                    │     │
│  │  │   ├─ open()            # 연결 시작                              │     │
│  │  │   ├─ joinAllComponents # 컴포넌트 등록                          │     │
│  │  │   └─ _processMessage   # 서버 메시지 처리                       │     │
│  │  │                                                                 │     │
│  │  └─ ReactorComponent      # 개별 컴포넌트 관리                     │     │
│  │      ├─ join()            # 서버에 등록                            │     │
│  │      ├─ applyDiff()       # HTML diff 적용                         │     │
│  │      ├─ dispatch()        # 이벤트 발송                            │     │
│  │      └─ serialize()       # 폼 데이터 직렬화                       │     │
│  │                                                                    │     │
│  │  wireview-boost.js                                                  │     │
│  │  └─ morph()               # idiomorph 래퍼                         │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                              ↕ WebSocket                                     │
│  Django Server                                                               │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  ReactorConsumer (consumer.py)                                     │     │
│  │  ├─ connect()             # 연결 수립                              │     │
│  │  ├─ command_join()        # 컴포넌트 참여                          │     │
│  │  ├─ command_user_event()  # 사용자 이벤트 처리                     │     │
│  │  ├─ model_mutation()      # ORM 변경 알림                          │     │
│  │  └─ send_render()         # 렌더링 결과 전송                       │     │
│  │                                                                    │     │
│  │  ComponentRepository (repository.py)                               │     │
│  │  ├─ join()                # 컴포넌트 인스턴스 생성                 │     │
│  │  ├─ dispatch_event()      # 이벤트 디스패치                        │     │
│  │  └─ components_subscribed_to()  # 구독 관리                        │     │
│  │                                                                    │     │
│  │  Component (component.py)                                          │     │
│  │  ├─ Pydantic BaseModel 기반                                        │     │
│  │  ├─ ReactorMeta           # 렌더링 상태 관리                       │     │
│  │  ├─ _render_diff()        # HTML diff 생성                         │     │
│  │  └─ 라이프사이클 훅       # joined, mutation, notification         │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 파일별 역할

| 파일 | 줄수 | 역할 |
|------|------|------|
| `component.py` | 470 | 컴포넌트 베이스 클래스, ReactorMeta, HTML diff |
| `consumer.py` | 203 | WebSocket Consumer, 메시지 라우팅 |
| `repository.py` | ~150 | 컴포넌트 인스턴스 관리, 구독 관리 |
| `auto_broadcast.py` | ~100 | Django signals 연동, ORM 자동 브로드캐스트 |
| `templatetags/reactor.py` | ~200 | 템플릿 태그 (`{% on %}`, `{% component %}`) |
| `event_transpiler.py` | ~100 | 이벤트 문법 파싱 |
| `serializer.py` | ~50 | Django 모델 직렬화 |
| `wireview.js` | 349 | 클라이언트 WebSocket, DOM 관리 |
| `wireview-boost.js` | ~100 | morphdom/idiomorph 래퍼, 히스토리 관리 |

### 1.3 데이터 흐름

```
사용자 클릭
    │
    ▼
┌─────────────────┐
│ onclick 핸들러  │  reactor.send(element, 'increment', {})
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ReactorComponent│  dispatch(command, args, formScope)
│   .dispatch()   │  serialize() → 폼 데이터 수집
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ServerConnection│  sendUserEvent(id, command, implicit, explicit)
│   ._send()      │  JSON.stringify → WebSocket.send
└────────┬────────┘
         │
         ▼ WebSocket
┌─────────────────┐
│ ReactorConsumer │  command_user_event(id, command, ...)
│ .receive_json() │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ComponentRepo   │  dispatch_event(id, command, args, kwargs)
│ .dispatch_event │  component.increment(**kwargs)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Component       │  self.count += 1
│ .increment()    │  (Pydantic 검증)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ReactorMeta     │  render_diff() → difflib.ndiff
│ .render_diff()  │  compress_diff()
└────────┬────────┘
         │
         ▼ WebSocket
┌─────────────────┐
│ ServerConnection│  _processMessage → case "render"
│ ._processMessage│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ReactorComponent│  applyDiff(diff) → getHtml(diff)
│ .applyDiff()    │  boost.morph(element, html)
└─────────────────┘
```

---

## 2. 핵심 컴포넌트 분석

### 2.1 Component 클래스 (component.py)

```python
class Component(BaseModel):
    """Pydantic v1 기반 컴포넌트"""

    # 클래스 레벨 레지스트리
    _all: dict[str, Type["Component"]] = {}

    # 컴포넌트 메타데이터
    _name: str
    _template_name: str
    _subscriptions: set[str] = set()

    # 인스턴스 상태
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: AnonymousUser | AbstractBaseUser
    wire: WireviewMeta

    # 라이프사이클
    async def joined(self): ...
    async def mutation(self, channel, action, instance): ...
    async def notification(self, channel, **kwargs): ...
    async def destroy(self): ...
```

**현재 문제점**:
1. Pydantic v1 의존 (`validate_arguments`, `ModelField`)
2. `__init_subclass__`에서 복잡한 메타프로그래밍
3. WireviewMeta와 Component의 책임 분리 불명확

### 2.2 WireviewMeta 클래스

```python
class WireviewMeta:
    """렌더링 상태 및 서버 통신 관리"""

    _last_sent_html: list[str]  # 마지막 전송 HTML (diff용)
    _is_frozen: bool            # 렌더링 중지 플래그
    _skip_render: bool          # 다음 렌더링 스킵
    _redirected_to: str | None  # 리다이렉트 URL

    # 렌더링
    async def render_diff(self, component, repo) -> HTMLDiff | None
    def render(self, component, repo) -> SafeText | None

    # 서버 → 클라이언트 통신
    async def send(self, _command, **kwargs)
    async def redirect_to(self, to, **kwargs)
    async def push_to(self, to, **kwargs)
```

**현재 문제점**:
1. HTML diff 로직이 복잡하고 최적화 여지 있음
2. `send` 메서드들의 일관성 부족

### 2.3 ReactorConsumer

```python
class ReactorConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket Consumer"""

    # 프론트엔드 명령
    async def command_join(self, name, state, children): ...
    async def command_leave(self, id): ...
    async def command_user_event(self, id, command, ...): ...

    # 컴포넌트 → 프론트엔드
    async def component_remove(self, id): ...
    async def component_dom_action(self, action, id, html): ...
    async def component_url_change(self, command, url): ...

    # 브로드캐스트 수신
    async def model_mutation(self, data): ...
    async def notification(self, data): ...
```

---

## 3. 목표 아키텍처 (v6.0)

### 3.1 개선된 전체 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           목표 아키텍처 (v6.0)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Browser (TypeScript)                                                        │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  connection.ts                                                     │     │
│  │  └─ ReactorConnection     # WebSocket + 재연결 + 상태 복구        │     │
│  │                                                                    │     │
│  │  component.ts                                                      │     │
│  │  └─ ReactorComponent      # 컴포넌트 래퍼 + diff 적용             │     │
│  │                                                                    │     │
│  │  commands.ts              # [신규] JS 명령어 실행 엔진             │     │
│  │  ├─ JSCommandExecutor     # 명령어 실행기                          │     │
│  │  ├─ show/hide/toggle      # DOM 조작                               │     │
│  │  ├─ addClass/removeClass  # 클래스 조작                            │     │
│  │  └─ push                  # 서버 이벤트                            │     │
│  │                                                                    │     │
│  │  optimistic.ts            # [신규] Optimistic UI 관리              │     │
│  │  └─ LoadingStateManager   # 로딩 클래스 자동 적용                  │     │
│  │                                                                    │     │
│  │  streams.ts               # [신규] 대량 데이터 처리                │     │
│  │  └─ StreamRenderer        # DOM ID 기반 효율적 업데이트            │     │
│  │                                                                    │     │
│  │  uploads.ts               # [신규] 파일 업로드                     │     │
│  │  └─ UploadManager         # 청크 업로드 + 프리뷰                   │     │
│  │                                                                    │     │
│  │  devtools.ts              # [신규] 개발자 도구                     │     │
│  │  └─ ReactorDevtools       # 디버그/프로파일링/latency sim          │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                              ↕ WebSocket (Binary Protocol)                   │
│  Django Server                                                               │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  consumer.py (개선)                                                │     │
│  │  └─ ReactorConsumer                                                │     │
│  │      ├─ command_js_exec    # [신규] JS 명령어 전달                 │     │
│  │      └─ command_upload     # [신규] 파일 업로드 처리               │     │
│  │                                                                    │     │
│  │  component.py (개선)                                               │     │
│  │  └─ Component (Pydantic v2)                                        │     │
│  │      ├─ push_event()       # [신규] 클라이언트 이벤트              │     │
│  │      ├─ stream()           # [신규] 대량 데이터                    │     │
│  │      ├─ stream_insert/delete/reset                                 │     │
│  │      └─ allow_upload()     # [신규] 업로드 설정                    │     │
│  │                                                                    │     │
│  │  js.py                     # [신규] JS 명령어 빌더                 │     │
│  │  └─ JS class              # 체이닝 API                             │     │
│  │                                                                    │     │
│  │  streams.py               # [신규] Stream 관리                     │     │
│  │  └─ StreamManager         # 메모리 효율적 리스트                   │     │
│  │                                                                    │     │
│  │  uploads.py               # [신규] 업로드 처리                     │     │
│  │  └─ UploadHandler         # 청크 수신 + 저장                       │     │
│  │                                                                    │     │
│  │  diff.py (개선)           # 최적화된 diff                          │     │
│  │  └─ TemplateDiffer        # 템플릿 슬롯 기반 바이너리 diff         │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 신규 모듈 설계

#### 3.2.1 JS 명령어 시스템 (js.py)

```python
from dataclasses import dataclass, field
from typing import Self
import json

@dataclass
class JS:
    """Phoenix LiveView.JS 스타일 클라이언트 명령어 빌더"""

    commands: list[dict] = field(default_factory=list)

    def show(
        self,
        selector: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        self.commands.append({
            "op": "show",
            "to": selector,
            "transition": transition,
            "time": time,
        })
        return self

    def hide(
        self,
        selector: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        self.commands.append({
            "op": "hide",
            "to": selector,
            "transition": transition,
            "time": time,
        })
        return self

    def toggle(self, selector: str) -> Self:
        self.commands.append({"op": "toggle", "to": selector})
        return self

    def add_class(self, selector: str, classes: str) -> Self:
        self.commands.append({
            "op": "add_class",
            "to": selector,
            "classes": classes.split(),
        })
        return self

    def remove_class(self, selector: str, classes: str) -> Self:
        self.commands.append({
            "op": "remove_class",
            "to": selector,
            "classes": classes.split(),
        })
        return self

    def push(self, event: str, **kwargs) -> Self:
        """서버로 이벤트 전송"""
        self.commands.append({
            "op": "push",
            "event": event,
            "payload": kwargs,
        })
        return self

    def dispatch(self, event: str, to: str | None = None, **detail) -> Self:
        """커스텀 DOM 이벤트 발생"""
        self.commands.append({
            "op": "dispatch",
            "event": event,
            "to": to,
            "detail": detail,
        })
        return self

    def focus(self, selector: str) -> Self:
        self.commands.append({"op": "focus", "to": selector})
        return self

    def navigate(self, url: str, replace: bool = False) -> Self:
        self.commands.append({
            "op": "navigate",
            "url": url,
            "replace": replace,
        })
        return self

    def to_json(self) -> str:
        return json.dumps(self.commands)

    def __str__(self) -> str:
        return self.to_json()
```

#### 3.2.2 Stream 관리 (streams.py)

```python
from dataclasses import dataclass
from typing import TypeVar, Generic, Callable
from django.db.models import Model

T = TypeVar('T', bound=Model)

@dataclass
class StreamItem(Generic[T]):
    """스트림 아이템 래퍼"""
    id: str
    item: T

@dataclass
class StreamOp:
    """스트림 작업"""
    op: str  # "insert" | "delete" | "reset"
    stream_name: str
    items: list[StreamItem] | None = None
    item: StreamItem | None = None
    at: int = -1  # insert 위치

class StreamManager:
    """컴포넌트별 스트림 관리"""

    def __init__(self, component_id: str):
        self.component_id = component_id
        self.streams: dict[str, Stream] = {}

    def init_stream(
        self,
        name: str,
        items: list[T],
        dom_id_fn: Callable[[T], str] | None = None,
    ) -> StreamOp:
        """스트림 초기화"""
        if dom_id_fn is None:
            dom_id_fn = lambda item: f"{name}-{item.pk}"

        stream = Stream(name=name, dom_id_fn=dom_id_fn)
        self.streams[name] = stream

        stream_items = [
            StreamItem(id=dom_id_fn(item), item=item)
            for item in items
        ]
        return StreamOp(op="reset", stream_name=name, items=stream_items)

    def insert(self, name: str, item: T, at: int = -1) -> StreamOp:
        """스트림에 아이템 추가"""
        stream = self.streams[name]
        stream_item = StreamItem(
            id=stream.dom_id_fn(item),
            item=item
        )
        return StreamOp(
            op="insert",
            stream_name=name,
            item=stream_item,
            at=at
        )

    def delete(self, name: str, item_id: str) -> StreamOp:
        """스트림에서 아이템 제거"""
        return StreamOp(op="delete", stream_name=name, item=StreamItem(id=item_id, item=None))

@dataclass
class Stream:
    """개별 스트림"""
    name: str
    dom_id_fn: Callable
```

### 3.3 개선된 Component 설계

```python
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from typing import Self, Any
from uuid import uuid4

from .js import JS
from .streams import StreamManager, StreamOp

class Component(BaseModel):
    """Pydantic v2 기반 컴포넌트"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    # 클래스 레벨
    _all: ClassVar[dict[str, type[Self]]] = {}
    _name: ClassVar[str]
    _template_name: ClassVar[str]
    _subscriptions: ClassVar[set[str]] = set()

    # 인스턴스 필드
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: Any  # Django User
    wire: "WireviewMeta"

    # 내부 상태
    _streams: StreamManager | None = None

    @field_serializer('user')
    def serialize_user(self, user: Any) -> int | None:
        return user.pk if hasattr(user, 'pk') else None

    # 라이프사이클
    async def joined(self) -> None:
        """컴포넌트 마운트 시 호출"""
        pass

    async def mutation(
        self,
        channel: str,
        action: str,
        instance: Any,
    ) -> None:
        """ORM 변경 알림"""
        pass

    async def notification(self, channel: str, **kwargs) -> None:
        """브로드캐스트 알림"""
        pass

    # JS 명령어
    async def push_event(self, event: str, **payload) -> None:
        """클라이언트에 이벤트 전송"""
        await self.wire.send("push_event", event=event, payload=payload)

    def js(self) -> JS:
        """JS 명령어 빌더 생성"""
        return JS()

    # Streams
    async def stream(self, name: str, items: list) -> None:
        """스트림 초기화"""
        if self._streams is None:
            self._streams = StreamManager(self.id)
        op = self._streams.init_stream(name, items)
        await self.wire.send_stream_op(op)

    async def stream_insert(self, name: str, item: Any, at: int = -1) -> None:
        """스트림에 아이템 추가"""
        op = self._streams.insert(name, item, at)
        await self.wire.send_stream_op(op)

    async def stream_delete(self, name: str, item_id: str) -> None:
        """스트림에서 아이템 제거"""
        op = self._streams.delete(name, item_id)
        await self.wire.send_stream_op(op)

    # Uploads (향후 구현)
    def allow_upload(
        self,
        name: str,
        accept: list[str],
        max_entries: int = 1,
        max_file_size: int = 10_000_000,
    ) -> None:
        """파일 업로드 허용"""
        # 향후 구현
        pass
```

---

## 4. 디렉토리 구조 변경

### 현재 구조
```
wireview/
├── __init__.py
├── apps.py
├── auto_broadcast.py
├── component.py           # 470줄, 복잡
├── consumer.py
├── event_transpiler.py
├── log.py
├── repository.py
├── schemas.py
├── serializer.py
├── settings.py
├── templatetags/
│   ├── __init__.py
│   └── reactor.py
├── urls.py
├── utils.py
└── static/wireview/
    ├── wireview.js         # 349줄, 순수 JS
    └── wireview-boost.js
```

### 목표 구조
```
wireview/
├── __init__.py
├── apps.py
├── settings.py
├── urls.py
│
├── core/                   # 핵심 모듈 분리
│   ├── __init__.py
│   ├── component.py        # 기본 컴포넌트
│   ├── meta.py             # ReactorMeta 분리
│   ├── repository.py
│   └── consumer.py
│
├── features/               # 기능별 모듈
│   ├── __init__.py
│   ├── js.py               # JS 명령어
│   ├── streams.py          # Streams
│   ├── uploads.py          # 파일 업로드
│   └── diff.py             # HTML Diff
│
├── broadcast/              # 브로드캐스트
│   ├── __init__.py
│   ├── auto.py             # auto_broadcast
│   └── serializer.py
│
├── templatetags/
│   ├── __init__.py
│   └── reactor.py
│
├── testing/                # 테스트 유틸리티
│   ├── __init__.py
│   └── helpers.py
│
└── static/wireview/
    ├── src/                # TypeScript 소스
    │   ├── index.ts
    │   ├── connection.ts
    │   ├── component.ts
    │   ├── commands.ts
    │   ├── streams.ts
    │   ├── uploads.ts
    │   ├── devtools.ts
    │   └── types.ts
    ├── dist/               # 빌드 결과
    │   └── wireview.min.js
    ├── tsconfig.json
    └── esbuild.config.ts
```

---

## 5. 프로토콜 설계

### 5.1 현재 메시지 형식

```typescript
// Client → Server
{ command: "join", payload: { name, state, children } }
{ command: "leave", payload: { id } }
{ command: "user_event", payload: { id, command, implicit_args, explicit_args } }

// Server → Client
{ command: "render", payload: { id, diff } }
{ command: "remove", payload: { id } }
{ command: "focus_on", payload: { selector } }
{ command: "url_change", payload: { command, url } }
```

### 5.2 확장된 메시지 형식 (v6.0)

```typescript
// Client → Server (추가)
{ command: "upload_chunk", payload: { upload_id, chunk, offset, total } }

// Server → Client (추가)
{ command: "js_exec", payload: { commands: JSCommand[] } }
{ command: "stream_op", payload: { op, stream_name, items?, item?, at? } }
{ command: "push_event", payload: { event, data } }
{ command: "upload_progress", payload: { upload_id, progress } }

// JSCommand 타입
type JSCommand =
  | { op: "show", to: string, transition?: string }
  | { op: "hide", to: string, transition?: string }
  | { op: "toggle", to: string }
  | { op: "add_class", to: string, classes: string[] }
  | { op: "remove_class", to: string, classes: string[] }
  | { op: "push", event: string, payload: object }
  | { op: "focus", to: string }
  | { op: "navigate", url: string, replace: boolean }
```

---

## 6. 성능 고려사항

### 6.1 메모리 최적화

```python
# 현재: 모든 컴포넌트가 전체 상태 유지
class Component:
    items: list[Item]  # 1000개 아이템 = 메모리에 1000개

# 개선: Streams로 메모리 절약
class Component:
    async def mount(self):
        await self.stream("items", Item.objects.all()[:1000])
        # 메모리에는 없고, 클라이언트에만 렌더링됨
```

### 6.2 대역폭 최적화

```python
# 현재: 라인 기반 diff
diff = [0, 1, '<div class="count">6</div>\n', 3]  # ~50 bytes

# 개선: 템플릿 슬롯 기반
diff = {"0": {"0": "6"}}  # ~20 bytes (60% 절감)
```

### 6.3 렌더링 최적화

```python
# skip_render로 불필요한 렌더링 방지
async def handle_click(self):
    if not self.should_update:
        self.skip_render()
        return

# temporary_assigns로 렌더링 후 메모리 해제
class Component:
    _temporary_assigns = {"large_data"}

    async def mount(self):
        self.large_data = await get_large_data()
        # 렌더링 후 자동으로 None으로 설정
```

---

*이 문서는 django-wireview의 기술적 설계를 정의합니다.*
