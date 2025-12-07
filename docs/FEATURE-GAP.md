# Phoenix LiveView 대비 기능 갭 분석

> django-wireview가 Phoenix LiveView 수준에 도달하기 위해 필요한 기능 목록

---

## 1. 기능 비교 매트릭스

### 1.1 핵심 기능

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| WebSocket 기반 통신 | ✅ | ✅ | - | - |
| 서버 사이드 상태 관리 | ✅ | ✅ | - | - |
| 컴포넌트 모델 | ✅ | ✅ | - | - |
| DOM Diffing | ✅ morphdom | ✅ idiomorph | - | - |
| HTML Diff 전송 | ✅ 바이너리 | ✅ Phoenix 스타일 | - | - |
| 자동 재연결 | ✅ | ✅ | - | - |
| 상태 복구 | ✅ 자동 | ⚠️ 수동 | 편의성 | P2 |

### 1.2 LiveView.JS (클라이언트 명령어)

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| JS.show() | ✅ | ✅ JS().show() | - | - |
| JS.hide() | ✅ | ✅ JS().hide() | - | - |
| JS.toggle() | ✅ | ✅ JS().toggle() | - | - |
| JS.add_class() | ✅ | ✅ JS().add_class() | - | - |
| JS.remove_class() | ✅ | ✅ JS().remove_class() | - | - |
| JS.toggle_class() | ✅ | ✅ JS().toggle_class() | - | - |
| JS.set_attribute() | ✅ | ✅ JS().set_attr() | - | - |
| JS.remove_attribute() | ✅ | ✅ JS().remove_attr() | - | - |
| JS.transition() | ✅ | ✅ JS().transition() | - | - |
| JS.focus() | ✅ | ✅ JS().focus() | - | - |
| JS.focus_first() | ✅ | ✅ JS().focus_first() | - | - |
| JS.push() | ✅ | ✅ JS().push() | - | - |
| JS.dispatch() | ✅ | ✅ JS().dispatch() | - | - |
| JS.navigate() | ✅ | ✅ JS().navigate() | - | - |
| JS.patch() | ✅ | ✅ push_to | - | - |
| 명령어 체이닝 | ✅ | ✅ 지원 | - | - |

### 1.3 Optimistic UI

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| phx-click-loading 클래스 | ✅ | ✅ wireview-click-loading | - | - |
| phx-submit-loading 클래스 | ✅ | ✅ wireview-submit-loading | - | - |
| phx-change-loading 클래스 | ✅ | ✅ wireview-change-loading | - | - |
| phx-disabled-with | ✅ | ❌ | 구현 필요 | P2 |
| 클라이언트 사이드 즉시 실행 | ✅ | ✅ JS() 명령어 | - | - |

### 1.4 Streams (대량 데이터)

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| stream() | ✅ | ❌ | 구현 필요 | P2 |
| stream_insert() | ✅ | ❌ | 구현 필요 | P2 |
| stream_delete() | ✅ | ❌ | 구현 필요 | P2 |
| stream_reset() | ✅ | ❌ | 구현 필요 | P2 |
| DOM ID 기반 업데이트 | ✅ | ❌ | 구현 필요 | P2 |
| 메모리에서 해제 | ✅ | ❌ | 구현 필요 | P2 |

### 1.5 파일 업로드

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| allow_upload() | ✅ | ❌ | 구현 필요 | P2 |
| live_file_input | ✅ | ❌ | 구현 필요 | P2 |
| live_img_preview | ✅ | ❌ | 구현 필요 | P2 |
| 진행률 표시 | ✅ | ❌ | 구현 필요 | P2 |
| 드래그 앤 드롭 | ✅ | ❌ | 구현 필요 | P3 |
| 청크 업로드 | ✅ | ❌ | 구현 필요 | P2 |
| 외부 스토리지 직접 업로드 | ✅ | ❌ | 구현 필요 | P3 |
| consume_uploaded_entries() | ✅ | ❌ | 구현 필요 | P2 |

### 1.6 비동기 작업

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| assign_async() | ✅ | ✅ assign_async() | - | - |
| start_async() | ✅ | ❌ | 구현 필요 | P2 |
| cancel_async() | ✅ | ❌ | 구현 필요 | P3 |
| AsyncResult 상태 | ✅ loading/ok/error | ✅ loading/ok/failed | - | - |

### 1.7 폼 처리

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| phx-change | ✅ | ✅ on "input" | - | - |
| phx-submit | ✅ | ✅ on "submit" | - | - |
| phx-feedback-for | ✅ | ❌ | 구현 필요 | P2 |
| phx-debounce | ✅ | ✅ debounce-N | - | - |
| phx-throttle | ✅ | ❌ | 구현 필요 | P3 |
| phx-auto-recover | ✅ | ❌ | 구현 필요 | P3 |
| Changeset 통합 | ✅ Ecto | ⚠️ Django Forms | 다른 접근 | P2 |

### 1.8 라이프사이클 훅

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| mount() | ✅ | ✅ joined() | - | - |
| handle_event() | ✅ | ✅ 메서드 직접 호출 | - | - |
| handle_info() | ✅ | ✅ notification() | - | - |
| handle_params() | ✅ | ⚠️ 부분 지원 | 강화 필요 | P2 |
| terminate() | ✅ | ✅ destroy() | - | - |
| update() (LiveComponent) | ✅ | ❌ | 구현 필요 | P3 |

### 1.9 개발자 도구

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| enableDebug() | ✅ | ✅ wireview.debug.enable() | - | - |
| enableProfiling() | ✅ | ❌ | 구현 필요 | P3 |
| enableLatencySim() | ✅ | ✅ wireview.debug.latency() | - | - |
| 테스트 헬퍼 | ✅ render_click 등 | ✅ mount(), call() | - | - |

### 1.10 템플릿 기능

| 기능 | Phoenix LiveView | django-wireview | 갭 | 우선순위 |
|------|:----------------:|:--------------:|:--:|:--------:|
| HEEx 템플릿 | ✅ | - Django 템플릿 | 다른 접근 | - |
| Function 컴포넌트 | ✅ | ❌ | 고려 필요 | P3 |
| Slots | ✅ | ❌ | 구현 필요 | P3 |
| 컴파일 타임 검증 | ✅ | ❌ | 한계 | - |

---

## 2. 우선순위별 구현 계획

### P0: 기반 (필수 선행)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  P0: Foundation                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Pydantic v2 마이그레이션                                                  │
│  • TypeScript 클라이언트 재작성                                              │
│  • Django 5.x 호환성 확보                                                    │
│  • 테스트 인프라 구축                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### P1: 핵심 UX 개선 ✅ 완료

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  P1: Core UX ✅                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ JS 명령어 시스템                                                         │
│  ├─ ✅ JS().show/hide/toggle                                                │
│  ├─ ✅ JS().add_class/remove_class/toggle_class                             │
│  ├─ ✅ JS().push (서버 이벤트)                                              │
│  └─ ✅ 명령어 체이닝                                                        │
│                                                                              │
│  ✅ Optimistic UI                                                            │
│  ├─ ✅ wireview-click-loading 클래스                                         │
│  ├─ ✅ wireview-submit-loading 클래스                                        │
│  └─ ✅ 클라이언트 사이드 즉시 실행 (JS 명령어)                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**구현 영향**:
- 사용자 체감 지연: 150ms → 50ms (3x 개선)
- 인터랙션 품질: 대폭 향상

### P2: 고급 기능

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  P2: Advanced Features                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Streams                                                                     │
│  ├─ stream() 초기화                                                         │
│  ├─ stream_insert/delete/reset                                              │
│  └─ 메모리 효율적 처리                                                      │
│                                                                              │
│  파일 업로드                                                                 │
│  ├─ allow_upload() 설정                                                     │
│  ├─ 진행률 표시                                                             │
│  └─ 이미지 프리뷰                                                           │
│                                                                              │
│  비동기 작업                                                                 │
│  ├─ assign_async()                                                          │
│  └─ AsyncResult 상태 관리                                                   │
│                                                                              │
│  기타                                                                        │
│  ├─ ✅ HTML Diff 최적화 (Phoenix 스타일 static/dynamic 분리)               │
│  ├─ phx-feedback-for 스타일 에러 표시                                       │
│  ├─ handle_params 강화                                                      │
│  └─ ✅ 테스트 유틸리티 (mount, call)                                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### P3: 완성도 (부분 완료)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  P3: Polish (부분 완료)                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ JS 명령어 확장                                                           │
│  ├─ ✅ JS().transition                                                      │
│  ├─ ✅ JS().set_attr/remove_attr                                            │
│  └─ ✅ JS().focus_first                                                     │
│                                                                              │
│  개발자 도구 (부분 완료)                                                     │
│  ├─ ✅ wireview.debug.enable()                                              │
│  ├─ enableProfiling()                                                       │
│  └─ ✅ wireview.debug.latency()                                             │
│                                                                              │
│  기타                                                                        │
│  ├─ phx-throttle                                                            │
│  ├─ phx-auto-recover                                                        │
│  ├─ Function 컴포넌트                                                       │
│  └─ Slots 지원                                                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 기능별 상세 갭 분석

### 3.1 JS 명령어 시스템

**Phoenix LiveView**:
```elixir
<button phx-click={
  JS.toggle(to: "#modal")
  |> JS.push("save")
  |> JS.add_class("saving", to: "#form")
}>
  저장
</button>
```

**현재 django-wireview**:
```html
<!-- 서버 왕복 필수, 체이닝 불가 -->
<button {% on "click" "save" %}>저장</button>
```

**목표 django-wireview**:
```html
<button {% on "click" JS().toggle("#modal").push("save").add_class("#form", "saving") %}>
  저장
</button>
```

**구현 난이도**: 중
**예상 작업량**: 2주

---

### 3.2 Optimistic UI

**Phoenix LiveView**:
```css
/* 자동 적용 */
.phx-click-loading {
  opacity: 0.5;
}
```

**현재 django-wireview**:
```javascript
// 수동 구현 필요
element.onclick = function() {
  this.classList.add('loading');
  wireview.send(this, 'save', {});
}
```

**목표 django-wireview**:
```css
/* 자동 적용 */
.wireview-click-loading {
  opacity: 0.5;
}
```

**구현 난이도**: 하
**예상 작업량**: 1주

---

### 3.3 Streams

**Phoenix LiveView**:
```elixir
def mount(_params, _session, socket) do
  {:ok, stream(socket, :messages, Messages.list_recent())}
end

def handle_event("new_message", params, socket) do
  message = Messages.create(params)
  {:noreply, stream_insert(socket, :messages, message, at: 0)}
end
```

**현재 django-wireview**:
```python
# 전체 리스트를 매번 재렌더링
class MessageList(Component):
    messages: list[Message] = []

    def add_message(self, content: str):
        Message.objects.create(content=content)
        self.messages = list(Message.objects.all())  # 전체 재조회
```

**목표 django-wireview**:
```python
class MessageList(Component):
    async def joined(self):
        await self.stream("messages", Message.objects.order_by("-created")[:100])

    async def add_message(self, content: str):
        message = await Message.objects.acreate(content=content)
        await self.stream_insert("messages", message, at=0)  # 단일 항목만 전송
```

**구현 난이도**: 상
**예상 작업량**: 3주

---

### 3.4 파일 업로드

**Phoenix LiveView**:
```elixir
def mount(_params, _session, socket) do
  {:ok,
   socket
   |> allow_upload(:avatar, accept: ~w(.jpg .png), max_entries: 2)}
end
```

```heex
<.live_file_input upload={@uploads.avatar} />

<%= for entry <- @uploads.avatar.entries do %>
  <.live_img_preview entry={entry} />
  <progress value={entry.progress} max="100" />
<% end %>
```

**현재 django-wireview**:
지원 안 함

**목표 django-wireview**:
```python
class ImageUploader(Component):
    def joined(self):
        self.allow_upload(
            "avatar",
            accept=[".jpg", ".png"],
            max_entries=2,
            max_file_size=5_000_000,
        )
```

```html
{% load wireview %}

{% upload_input "avatar" %}

{% for entry in uploads.avatar.entries %}
  {% upload_preview entry %}
  <progress value="{{ entry.progress }}" max="100"></progress>
{% endfor %}
```

**구현 난이도**: 상
**예상 작업량**: 3주

---

### 3.5 비동기 작업

**Phoenix LiveView**:
```elixir
def mount(_params, _session, socket) do
  {:ok,
   socket
   |> assign(:stats, AsyncResult.loading())
   |> start_async(:fetch_stats, fn -> Stats.calculate() end)}
end

def handle_async(:fetch_stats, {:ok, stats}, socket) do
  {:noreply, assign(socket, :stats, AsyncResult.ok(stats))}
end
```

```heex
<.async_result :let={stats} assign={@stats}>
  <:loading>로딩 중...</:loading>
  <:failed :let={reason}><%= reason %></:failed>
  총 매출: <%= stats.total %>
</.async_result>
```

**현재 django-wireview**:
지원 안 함 (동기 처리만)

**목표 django-wireview**:
```python
class Dashboard(Component):
    stats: AsyncResult[Stats] | None = None

    async def joined(self):
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        return await Stats.objects.acalculate()
```

```html
{% if stats.loading %}
  로딩 중...
{% elif stats.failed %}
  {{ stats.reason }}
{% else %}
  총 매출: {{ stats.result.total }}
{% endif %}
```

**구현 난이도**: 중
**예상 작업량**: 2주

---

## 4. 구현 불가/어려운 기능

### 4.1 런타임 한계

| 기능 | 이유 | 대안 |
|------|------|------|
| **BEAM 수준 동시성** | Python GIL | asyncio + 수평 확장 |
| **프로세스 격리** | Python 스레드 모델 | 컴포넌트별 상태 격리 |
| **컴파일 타임 검증** | Python 동적 타이핑 | Pydantic + mypy |

### 4.2 프레임워크 차이

| 기능 | Phoenix | Django | 대안 |
|------|---------|--------|------|
| **HEEx 템플릿** | 컴파일 검증 | Django 템플릿 | 런타임 검증 |
| **Ecto Changeset** | 타입 안전 | Django Forms | Pydantic 검증 |
| **OTP Supervisor** | 장애 복구 | - | 재연결 로직 |

---

## 5. 우선순위 결정 기준

```
영향도 = 사용자_체감_개선 × 0.4 + 개발자_생산성 × 0.3 + 기술_부채_해소 × 0.3

P0: 영향도 높음, 선행 의존성
P1: 영향도 높음, UX 직접 개선
P2: 영향도 중간, 특정 사용 사례
P3: 영향도 낮음, 완성도 향상
```

### 최종 우선순위 요약

| 순위 | 기능 | 영향도 | 의존성 |
|:----:|------|:------:|--------|
| **P0** | Pydantic v2 | 높음 | 없음 |
| **P0** | TypeScript 클라이언트 | 높음 | 없음 |
| **P1** | JS 명령어 | 높음 | P0 완료 |
| **P1** | Optimistic UI | 높음 | P0 완료 |
| **P2** | Streams | 중간 | P1 완료 |
| **P2** | 파일 업로드 | 중간 | P1 완료 |
| **P2** | 비동기 작업 | 중간 | P1 완료 |
| **P3** | 개발자 도구 | 낮음 | P2 완료 |

---

*이 문서는 Phoenix LiveView와의 기능 갭을 분석하고 구현 우선순위를 정의합니다.*
