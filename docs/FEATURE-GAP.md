# Phoenix LiveView 대비 기능 갭 분석

> django-wireview가 Phoenix LiveView 수준에 도달하기 위해 필요한 기능 목록
>
> **최종 업데이트**: 2026-09-08

---

## 개요

```
Phoenix LiveView 주요 기능: ~75개
django-wireview 지원:       ~55개 (73%)
미지원:                     ~20개 (27%)
```

---

## 1. 기능 커버리지 현황

### 카테고리별 상태

| 카테고리 | 커버리지 | 상태 |
|----------|:--------:|------|
| Core Lifecycle | 95% | ✅ 대부분 완료 |
| Real-time (PubSub, Presence) | 95% | ✅ 완료 |
| JS Commands (LiveView.JS) | 95% | ✅ 완료 |
| Optimistic UI | 95% | ✅ 완료 |
| Streams | 80% | ✅ 기본 완료 |
| File Uploads | 100% | ✅ 완료 |
| Async Operations | 95% | ✅ 대부분 완료 |
| Navigation | 85% | ✅ 대부분 완료 |
| **JavaScript Hooks** | 95% | ✅ 완료 |
| **Components (Slots, Function, Live)** | 95% | ✅ 완료 |
| Testing | 80% | ✅ 기본 완료 |
| Developer Tools | 85% | ✅ 대부분 완료 |

---

## 2. 상세 기능 비교

### 2.1 Core Lifecycle ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| mount/joined | `mount/3` | `joined()` | ✅ |
| handle_event | `handle_event/3` | 메서드 직접 호출 | ✅ |
| handle_info | `handle_info/2` | `notification()` | ✅ |
| handle_params | `handle_params/3` | `params_changed()` | ✅ |
| terminate | `terminate/2` | `leaving()` | ✅ |
| ORM mutation | - | `mutation()` | ✅ 추가 기능 |

### 2.2 Real-time Features ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| PubSub broadcast | `Phoenix.PubSub` | `broadcast()` | ✅ |
| Presence tracking | `Phoenix.Presence` | `PresenceMixin` | ✅ |
| Presence list | `Presence.list/1` | `_presence_users` | ✅ |
| Typing indicators | 수동 구현 | `presence_set_typing()` | ✅ |
| Auto-broadcast (ORM) | 수동 구현 | `AUTO_BROADCAST` | ✅ 추가 기능 |

### 2.3 LiveView.JS (Client Commands) ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| show/hide/toggle | ✅ | `JS().show/hide/toggle()` | ✅ |
| add_class/remove_class | ✅ | `JS().add_class/remove_class()` | ✅ |
| toggle_class | ✅ | `JS().toggle_class()` | ✅ |
| set_attribute | ✅ | `JS().set_attr()` | ✅ |
| remove_attribute | ✅ | `JS().remove_attr()` | ✅ |
| transition | ✅ | `JS().transition()` | ✅ |
| focus/focus_first | ✅ | `JS().focus/focus_first()` | ✅ |
| push (server event) | ✅ | `JS().push()` | ✅ |
| dispatch (DOM event) | ✅ | `JS().dispatch()` | ✅ |
| navigate | ✅ | `JS().navigate()` | ✅ |
| Command chaining | ✅ | ✅ 지원 | ✅ |

### 2.4 Optimistic UI ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| phx-click-loading | ✅ | `wireview-click-loading` | ✅ |
| phx-submit-loading | ✅ | `wireview-submit-loading` | ✅ |
| phx-change-loading | ✅ | `wireview-change-loading` | ✅ |
| phx-disabled-with | ✅ | `wire-disabled-with` | ✅ |
| Client-side immediate | ✅ | JS() 명령어 | ✅ |

### 2.5 Streams ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| stream() | ✅ | `stream()` | ✅ |
| stream_insert() | ✅ | `stream_insert()` | ✅ |
| stream_delete() | ✅ | `stream_delete()` | ✅ |
| DOM ID generation | ✅ | `dom_id` param | ✅ |
| wire-stream attribute | `phx-update="stream"` | `wire-stream` | ✅ |
| stream :limit | ✅ | `stream(limit=N)` | ✅ |
| stream :reset | ✅ | ❌ | 🟡 |
| phx-viewport-top/bottom | ✅ | `wire-viewport-*` | ✅ |

### 2.6 File Uploads ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| allow_upload() | ✅ | `allow_upload()` | ✅ |
| live_file_input | ✅ | `{% upload_input %}` | ✅ |
| Progress tracking | ✅ | `entry.progress` | ✅ |
| Image preview | ✅ | `{% upload_preview %}` | ✅ |
| Drag and drop | ✅ | `{% upload_drop_zone %}` | ✅ |
| Chunk upload | ✅ | ✅ | ✅ |
| consume_uploads | ✅ | `consume_uploads()` | ✅ |
| External upload (S3) | ✅ | `external=callback` | ✅ |
| Magic byte validation | ✅ | ✅ | ✅ |

### 2.7 Async Operations ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| assign_async() | ✅ | `assign_async()` | ✅ |
| AsyncResult states | loading/ok/failed | loading/ok/failed | ✅ |
| start_async() | ✅ | `start_async()` | ✅ |
| cancel_async() | ✅ | `cancel_async()` | ✅ |
| handle_async() | ✅ | `handle_async()` | ✅ |

### 2.8 Navigation ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| push_navigate | ✅ | `redirect_to()` | ✅ |
| push_patch | ✅ | `push_to()` | ✅ |
| replace | ✅ | `replace_to()` | ✅ |
| handle_params | ✅ | `params_changed()` | ✅ |
| live_session | ✅ | ❌ | 🟠 |
| Client-side boost | ✅ | `BOOST_PAGES` | ✅ |

### 2.9 JavaScript Interoperability ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| **wire-hook** | ✅ 라이프사이클 훅 | `wire-hook="Name"` | ✅ |
| Hook.mounted | ✅ | `mounted()` | ✅ |
| Hook.updated | ✅ | `updated()` | ✅ |
| Hook.destroyed | ✅ | `destroyed()` | ✅ |
| Hook.disconnected | ✅ | `disconnected()` | ✅ |
| Hook.reconnected | ✅ | `reconnected()` | ✅ |
| Hook.beforeUpdate | ✅ | `beforeUpdate()` | ✅ |
| pushEvent (client→server) | ✅ | `this.pushEvent()` | ✅ |
| handleEvent (server→client) | ✅ | `this.handleEvent()` | ✅ |
| handle_hook_event (server) | - | `handle_hook_event()` | ✅ |
| push_event (server→client) | ✅ | `push_event()` | ✅ |
| Colocated hooks | ✅ | ❌ | 🟠 |
| onBeforeElUpdated | ✅ | `dom.onBeforeElUpdated` | ✅ |

### 2.10 Components ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| Stateful component | ✅ | ✅ Component | ✅ |
| **LiveComponent** | ✅ 중첩 상태 | `{% live_component %}` | ✅ |
| **Function components** | ✅ | `@function_component` | ✅ |
| **Slots (named)** | ✅ `<:header>` | `{% fill header %}` | ✅ |
| Slots (default) | ✅ `inner_block` | `{% render_slot %}` | ✅ |
| Slots (let binding) | ✅ | `let:item` | ✅ |
| @myself target | ✅ | `myself=True` | ✅ |
| update/2 callback | ✅ | `update()` | ✅ |
| update_many/1 | ✅ 배치 최적화 | ❌ | 🟠 |
| Nested LiveViews | ✅ 프로세스 격리 | ❌ | 🟠 |

### 2.11 Form Handling ⚠️

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| phx-change | ✅ | `{% on "input" %}` | ✅ |
| phx-submit | ✅ | `{% on "submit" %}` | ✅ |
| phx-debounce | ✅ | `.debounce.N` | ✅ |
| phx-throttle | ✅ | `.throttle.N` | ✅ |
| phx-feedback-for | ✅ | `wire-feedback-for` | ✅ |
| phx-auto-recover | ✅ | ❌ | 🟠 |
| Form recovery | ✅ 자동 | ❌ | 🟠 |
| Changeset integration | Ecto | Django Forms | ✅ 다른 접근 |

### 2.12 Performance Features ⚠️

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| HTML Diff | ✅ 바이너리 | ✅ Phoenix 스타일 (GAP-024로 부분 diff 실동작) | ✅ |
| skip_render | ✅ | `skip_render()` | ✅ |
| force_render | ✅ | `force_render()` | ✅ |
| **temporary_assigns** | ✅ | ✅ `_temporary_assigns` | ✅ |
| Sticky components | ✅ | ❌ | 🟠 |
| Comprehensions | ✅ 키 기반 | ✅ 위치 기반 (GAP-025) | 🟡 |

### 2.13 Testing ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| render_component | ✅ | `mount()` | ✅ |
| render_click | ✅ | `call()` | ✅ |
| render_change | ✅ | `call()` | ✅ |
| assert_patch | ✅ | 수동 검증 | ⚠️ |
| follow_redirect | ✅ | 수동 검증 | ⚠️ |
| MockChannelLayer | - | ✅ | ✅ 추가 기능 |

### 2.14 Developer Tools ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| enableDebug | ✅ | `wireview.debug.enable()` | ✅ |
| enableLatencySim | ✅ | `wireview.debug.latency()` | ✅ |
| enableProfiling | ✅ | `wireview.debug.enableProfiling()` | ✅ |
| Telemetry | ✅ | ❌ | 🟡 |
| **Type Stubs** | - | `wireview_stubs` | ✅ 추가 기능 |
| **LSP Metadata** | - | `wireview_lsp` | ✅ 추가 기능 |

### 2.15 Miscellaneous ⚠️

| 기능 | Phoenix LiveView | django-wireview | 상태 |
|------|:----------------:|:---------------:|:----:|
| Page title | ✅ `assign(:page_title)` | `push_title()` | ✅ |
| Flash messages | ✅ `put_flash` | `put_flash()` | ✅ |
| Dead views | ✅ JS 비활성화 폴백 | ❌ | 🟠 |
| LongPolling fallback | ✅ | ❌ | 🟠 |
| on_mount hooks | ✅ | `_on_mount` | ✅ |
| attach_hook | ✅ | `attach_hook()` | ✅ |

---

## 3. 미구현 기능 우선순위

### 🔴 P0: Critical (DX에 큰 영향)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-001~~ | ~~**JavaScript Hooks**~~ | ~~클라이언트 측 라이프사이클 훅 (`wire-hook`)~~ | ~~상~~ | ✅ 완료 |
| ~~GAP-002~~ | ~~**Slots**~~ | ~~컴포넌트 콘텐츠 합성 (`{% fill %}`, `{% render_slot %}`)~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-003~~ | ~~**Function Components**~~ | ~~상태 없는 재사용 가능 템플릿 함수~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-004~~ | ~~**handle_params**~~ | ~~URL 파라미터 변경 시 콜백~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-005~~ | ~~**LiveComponent**~~ | ~~독립 상태를 가진 중첩 컴포넌트~~ | ~~상~~ | ✅ 완료 |
| ~~GAP-006~~ | ~~**temporary_assigns**~~ | ~~렌더 후 메모리 자동 해제~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-024~~ | ~~**Stable HTML Diff**~~ | ~~서명 상태를 dynamic 파트로 옮겨 부분 diff 활성화, 상태 압축 서명~~ | ~~하~~ | ✅ 완료 |

### 🟠 P1: Important (기능적 차이)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-007~~ | ~~External Uploads~~ | ~~S3/GCS 직접 업로드~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-008~~ | ~~Form Auto-Recovery~~ | ~~재연결 시 폼 상태 복구~~ | ~~중~~ | ✅ 완료 |
| GAP-009 | live_session | 인증/레이아웃 경계 관리 | 중 | [#58](https://github.com/itda-work/django-wireview/issues/58) |
| ~~GAP-010~~ | ~~Page Title~~ | ~~동적 페이지 타이틀 변경~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-011~~ | ~~Flash Messages~~ | ~~일회성 알림 메시지~~ | ~~하~~ | ✅ 완료 |
| GAP-012 | LongPolling Fallback | WebSocket 불가 시 폴백 | 중 | [#59](https://github.com/itda-work/django-wireview/issues/59) |
| ~~GAP-013~~ | ~~pushEvent (Hook→Server)~~ | ~~훅에서 서버로 이벤트 전송~~ | ~~중~~ | ✅ 완료 |

### 🟡 P2: Nice to Have (편의 기능)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-014~~ | ~~stream :limit~~ | ~~스트림 DOM 크기 제한~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-015~~ | ~~phx-viewport-*~~ | ~~양방향 무한 스크롤 바인딩~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-016~~ | ~~Image Preview~~ | ~~업로드 이미지 미리보기~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-017~~ | ~~start_async/cancel_async~~ | ~~세밀한 비동기 제어~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-018~~ | ~~phx-disabled-with~~ | ~~버튼 비활성화 텍스트~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-019~~ | ~~phx-feedback-for~~ | ~~폼 필드 에러 표시~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-020~~ | ~~enableProfiling~~ | ~~성능 프로파일링~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-021~~ | ~~on_mount hooks~~ | ~~공통 마운트 로직 모듈화~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-022~~ | ~~Telemetry~~ | ~~성능 측정 훅~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-023~~ | ~~onBeforeElUpdated~~ | ~~DOM 패치 전 콜백~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-025~~ | ~~Comprehensions~~ | ~~`{% for %}`를 항목 단위, `{% if %}`를 블록 단위 static/dynamic으로 분리해 항목·분기 변경 시 부분 diff~~ | ~~중~~ | ✅ 완료 (위치 기반, 키 기반은 미지원) |
| ~~GAP-026~~ | ~~Transport seam~~ | ~~`Outbound`/`Broker` 인터페이스 뒤로 채널 레이어 격리, 렌더 스냅샷 직렬화, wire-protocol 문서~~ | ~~중~~ | ✅ 완료 |
| GAP-027 | Session extraction | 컨슈머 핸들러를 `WireviewSession`으로 분리, 세션 상태 export/import (docs/design/transport-abstraction.md) | 상 | [#60](https://github.com/itda-work/django-wireview/issues/60) 착수 기준 대기 |

---

## 4. 구현 로드맵

### Phase 1: JavaScript Interop ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: JavaScript Hooks & Interoperability  ✅ 완료      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-001: JavaScript Hooks ✅                               │
│  ├─ wire-hook="MyHook" 속성                                │
│  ├─ Hook 라이프사이클 (mounted, updated, destroyed, etc.)  │
│  ├─ Hook.pushEvent() → 서버 이벤트                         │
│  └─ handleEvent 클라이언트 핸들러                          │
│                                                             │
│  GAP-013: pushEvent ✅                                      │
│  └─ Hook에서 서버로 커스텀 이벤트 전송                     │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 2: Component System ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: Advanced Components                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-002: Slots ✅ 완료                                     │
│  ├─ {% fill "header" %}...{% endfill %}                    │
│  ├─ {% render_slot "header" %}                             │
│  ├─ default slot ({% render_slot %})                       │
│  └─ let: 바인딩 지원                                       │
│                                                             │
│  GAP-003: Function Components ✅ 완료                       │
│  ├─ @function_component 데코레이터                         │
│  ├─ {% func "name" %} 심플 태그                            │
│  ├─ {% func_block "name" %}...{% endfunc %} 블록 태그      │
│  └─ 타입 검증 및 슬롯 지원                                 │
│                                                             │
│  GAP-005: LiveComponent ✅ 완료                             │
│  ├─ {% live_component "Name" id="..." %} 태그              │
│  ├─ @myself 타겟팅 (myself=True)                           │
│  ├─ update() 콜백                                          │
│  └─ send_update() 부모→자식 통신                           │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 3: Navigation & Forms ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: Navigation & Form Enhancement  ✅ 완료            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-004: handle_params ✅ 완료                             │
│  ├─ URL 변경 감지                                          │
│  └─ params_changed() 콜백                                  │
│                                                             │
│  GAP-008: Form Auto-Recovery ✅ 완료                        │
│  ├─ 재연결 시 폼 상태 저장/복구                            │
│  └─ wire-auto-recover 속성                                 │
│                                                             │
│  GAP-010: Page Title ✅ 완료                                │
│  └─ push_title() 메서드                                    │
│                                                             │
│  GAP-011: Flash Messages ✅ 완료                            │
│  └─ put_flash() / clear_flash()                            │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 4: Performance & Polish (진행 중)

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 4: Performance & Developer Experience                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-006: temporary_assigns ✅ 완료                         │
│  └─ _temporary_assigns 클래스 변수                         │
│                                                             │
│  GAP-007: External Uploads ✅ 완료                          │
│  └─ S3/GCS presigned URL 업로드                            │
│                                                             │
│  GAP-014-015: Stream 고급 기능 ✅ 완료                      │
│  ├─ :limit 옵션 ✅ 완료                                    │
│  └─ viewport 바인딩 ✅ 완료                                │
│                                                             │
│  GAP-020-022: Developer Tools                               │
│  ├─ Type Stubs (.pyi) ✅ 완료                              │
│  ├─ LSP Metadata ✅ 완료                                   │
│  ├─ Profiling                                              │
│  └─ Telemetry                                              │
│                                                             │
│  예상 기간: 4-6주                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. GitHub Issue 구조

### Label 체계

```yaml
priority:
  - "priority: critical"   # P0
  - "priority: high"       # P1
  - "priority: medium"     # P2

type:
  - "type: feature"        # 새 기능
  - "type: enhancement"    # 기존 기능 개선
  - "type: dx"             # 개발자 경험

area:
  - "area: js-interop"     # JavaScript 연동
  - "area: components"     # 컴포넌트 시스템
  - "area: navigation"     # 네비게이션
  - "area: forms"          # 폼 처리
  - "area: uploads"        # 파일 업로드
  - "area: performance"    # 성능
  - "area: devtools"       # 개발자 도구

phase:
  - "phase: 1"
  - "phase: 2"
  - "phase: 3"
  - "phase: 4"
```

### Milestone 구조

```
v6.0.0-alpha.1 (Phase 1)
├── JavaScript Hooks
└── pushEvent

v6.0.0-alpha.2 (Phase 2)
├── Slots
├── Function Components
└── LiveComponent (optional)

v6.0.0-beta.1 (Phase 3)
├── handle_params ✅
├── Form Auto-Recovery
├── Page Title
└── Flash Messages

v6.0.0-rc.1 (Phase 4)
├── temporary_assigns
├── External Uploads
├── Stream Advanced
└── Developer Tools

v6.0.0 (Release)
└── Documentation & Polish
```

---

## 6. 구현 불가/제한 사항

### 런타임 한계

| 기능 | 이유 | 대안 |
|------|------|------|
| BEAM 수준 동시성 | Python GIL | asyncio + 수평 확장 |
| 프로세스 격리 | Python 스레드 모델 | 컴포넌트별 상태 격리 |
| 컴파일 타임 검증 | Python 동적 타이핑 | Pydantic + pyright |

### 프레임워크 차이

| Phoenix | Django | 접근 방식 |
|---------|--------|----------|
| HEEx 템플릿 | Django 템플릿 | 런타임 검증 |
| Ecto Changeset | Django Forms | Pydantic 검증 |
| OTP Supervisor | - | 재연결 로직 |

---

## 7. 참고 자료

- [Phoenix LiveView Documentation](https://hexdocs.pm/phoenix_live_view/)
- [Phoenix LiveView JavaScript Interop](https://hexdocs.pm/phoenix_live_view/js-interop.html)
- [Phoenix LiveComponent](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveComponent.html)
- [Phoenix Presence](https://hexdocs.pm/phoenix/Phoenix.Presence.html)

---

*이 문서는 Phoenix LiveView와의 기능 갭을 분석하고 구현 우선순위를 정의합니다.*
*최종 업데이트: 2025-06*
