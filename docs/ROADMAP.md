# django-wireview 로드맵

> Phoenix LiveView 수준의 DX를 향한 개발 로드맵

---

## 개요

```
현재 버전: pyproject.toml 과 git 태그 v* 가 정본
남은 갭 목록: docs/FEATURE-GAP.md 의 GAP-nnn 이 정본

Phase 1: Foundation     ████████████████████ 완료
Phase 2: Core Features  ████████████████████ 완료
Phase 3: Advanced       ████████████████████ 완료
Phase 4: Component      ████████████████████ 완료
Phase 5: Polish         ████████████████░░░░ 진행 중 (프로파일링·타입 스텁·LSP·Telemetry 완료)
```

Phoenix LiveView 대비 남은 P1 기능 갭은 없다. GAP-009 live_session 과 GAP-022 Telemetry 는
`v0.3.0` 에서 끝났고, **GAP-012 LongPolling 폴백은 만들지 않기로 했다** —
WebSocket 을 필수 전제로 둔다(`docs/design/longpolling-fallback.md` §5).
GAP-027 세션 분리는 `docs/design/transport-abstraction.md` 6절의 착수 기준을 만족할 때 시작한다.
남은 것은 P2·P3 이고 `docs/FEATURE-GAP.md` 3절이 정본이다.

---

## Phase 1: Foundation (기반 현대화) - ✅ 완료

### 1.1 Pydantic v2 마이그레이션 - ✅ 완료

- ✅ BaseModel 설정 변경 (`ConfigDict`)
- ✅ Field 문법 업데이트
- ✅ `field_serializer` 구현
- ✅ `.model_dump()` / `.model_dump_json()` 사용
- ✅ `validate_call` 데코레이터 적용

### 1.2 의존성 업데이트 - ✅ 완료

```toml
python_requires = >=3.12
dependencies = [
    django>=4.2,
    channels>=4,<5,
    pydantic>=2.0,<3
]
```

### 1.3 테스트 인프라 - ✅ 완료

- ✅ pytest-asyncio 설정
- ✅ pytest-django 설정
- ✅ `wireview.testing` 모듈
- ✅ 단위/통합 테스트 구조

---

## Phase 2: Core Features (핵심 기능) - ✅ 90% 완료

### 2.1 JS 명령어 시스템 - ✅ 완료

**구현 완료**: `wireview/js.py`

```python
from wireview.js import JS

# 템플릿에서 사용
{% on "click" JS().toggle("#modal").push("save") %}

# Python에서 사용
await self.push_js(JS().set_value("input", "").focus("#next"))
```

**지원 명령어**:
- `show()`, `hide()`, `toggle()` - 요소 표시/숨김
- `add_class()`, `remove_class()`, `toggle_class()` - 클래스 조작
- `set_attribute()`, `remove_attribute()` - 속성 조작
- `set_value()` - 입력 값 설정
- `focus()` - 포커스 이동
- `push()` - 서버 이벤트 전송
- `dispatch()` - 브라우저 이벤트 발생

### 2.2 Phoenix 스타일 HTML Diff - ✅ 완료

**구현 완료**: `wireview/core/rendered.py`

- ✅ 템플릿 마커 기반 정적/동적 분리
- ✅ 변경된 슬롯만 전송하는 효율적 diff
- ✅ `RenderedDiff` 페이로드

### 2.3 이벤트 시스템 고도화 - ✅ 완료

**구현 완료**: `wireview/event_transpiler.py`

```html
{% on "click.prevent.stop" "handler" %}
{% on "keydown.enter.debounce.300" "search" %}
{% on "input.throttle.500" "filter" %}
```

**지원 수정자**:
- `.prevent`, `.stop` - 기본 동작/전파 방지
- `.debounce.N`, `.throttle.N` - 디바운스/스로틀
- `.capture`, `.once`, `.passive` - 이벤트 옵션
- `.self`, `.away` - 타겟 필터링

---

## Phase 3: Advanced Features (고급 기능) - ✅ 80% 완료

### 3.1 Streams (대량 데이터) - ✅ 완료

**구현 완료**: `wireview/features/streams.py`

```python
class ItemList(Component):
    async def joined(self):
        await self.stream("items", Item.objects.all()[:100])

    async def add_item(self, name: str):
        item = await Item.objects.acreate(name=name)
        await self.stream_insert("items", item, at=0)

    async def remove_item(self, item_id: int):
        await self.stream_delete("items", item_id)
```

### 3.2 파일 업로드 - ✅ 완료

**구현 완료**: `wireview/features/uploads.py`

```python
class ImageUploader(Component):
    async def joined(self):
        self.allow_upload(
            "images",
            accept=[".jpg", ".png"],
            max_entries=5,
            max_file_size=10_000_000,
        )

    async def save_images(self):
        async for entry in self.consume_uploads("images"):
            path = await entry.save_to("uploads/")
```

### 3.3 비동기 작업 (assign_async) - ✅ 완료

**구현 완료**: `wireview/async_result.py`

```python
class Dashboard(Component):
    stats: AsyncResult[Stats] = None

    async def joined(self):
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        return await Stats.objects.aget()
```

### 3.4 Temporary Assigns - ✅ 완료

**메모리 최적화를 위한 임시 할당**:

```python
class MessageList(Component):
    _temporary_assigns = {"messages"}
    messages: list[Message] = []

    async def joined(self):
        self.messages = await Message.objects.all()[:100]
        # 렌더링 후 자동으로 [] 초기화
```

### 3.5 URL 파라미터 처리 - ✅ 완료

```python
async def params_changed(self, params: dict[str, str], uri: str):
    self.page = int(params.get("page", "1"))
    await self.wire.push_to(f"?page={self.page + 1}")
```

---

## Phase 4: Component System (컴포넌트 시스템) - 🔄 진행 중

### 4.1 Slots (컴포넌트 콘텐츠 합성) - ✅ 완료

**GitHub Issue**: #50 (GAP-002)

**목표**: Phoenix LiveView 스타일의 슬롯 시스템

```html
<!-- 컴포넌트 템플릿 (card.html) -->
<div {% tag_header %} class="card">
  {% if slots.header %}
    <header>{% render_slot "header" %}</header>
  {% endif %}

  <div class="card-body">
    {% render_slot %}
  </div>
</div>
```

```html
<!-- 사용 -->
{% component_block "Card" title="Hello" %}
  {% fill header %}
    <h1>{{ title }}</h1>
  {% endfill %}

  Main content goes here
{% endcomponent %}
```

**구현 현황**:
- ✅ `Slot`, `SlotContainer` 클래스 (`wireview/slots.py`)
- ✅ `{% fill %}...{% endfill %}` 태그
- ✅ `{% render_slot %}` 태그
- ✅ `{% component_block %}...{% endcomponent %}` 태그
- ✅ `let:` 변수 바인딩
- ✅ Required slot 검증
- ✅ 단위 테스트 (`tests/test_slots.py`)
- ✅ 통합 테스트 (`tests/test_slots_integration.py`)
- ✅ 테스트 컴포넌트 (`examples/slots/`)

### 4.2 JavaScript Hooks - ⬜ 예정

**GitHub Issue**: #49 (GAP-001)

**목표**: 클라이언트 측 컴포넌트 lifecycle hooks

```javascript
Wireview.hooks.Chart = {
  mounted() { this.chart = new Chart(this.el, {...}) },
  updated() { this.chart.update(this.el.dataset) },
  destroyed() { this.chart.destroy() }
}
```

### 4.3 Function Components - ⬜ 예정

**목표**: 간단한 UI를 위한 함수형 컴포넌트

```python
@component
def button(variant: str = "primary", **slots):
    return f'<button class="btn-{variant}">{slots.get("default", "")}</button>'
```

---

## Phase 5: Polish (완성도) - ⬜ 시작 전

### 5.1 개발자 도구

- ⬜ 브라우저 확장 프로그램
- ⬜ 디버그 모드 로깅
- ⬜ 성능 프로파일링

### 5.2 문서화

- ⬜ API 레퍼런스 완성
- ⬜ 튜토리얼 작성
- ⬜ 예제 앱 (Todo, Chat, Dashboard)

### 5.3 TypeScript 클라이언트 재작성

- ⬜ 타입 정의 추가
- ⬜ 모듈화 개선

---

## 마일스톤 요약

| Phase | 상태 | 주요 목표 |
|-------|:----:|----------|
| **Phase 1** | ✅ | Pydantic v2, 의존성 업데이트, 테스트 |
| **Phase 2** | ✅ | JS 명령어, HTML Diff, 이벤트 |
| **Phase 3** | ✅ | Streams, Uploads, Async |
| **Phase 4** | ✅ | Slots, Hooks, Function Components |
| **Phase 5** | 🔄 | DevTools, 문서화, TypeScript |

---

## 릴리스 이력과 계획

패키지 버전은 git 태그 `v*` 가 정본이다. reactor 시절의 v6.0.0 마일스톤 번호는 쓰지 않는다.

| 버전 | 상태 | 내용 |
|------|:----:|------|
| v0.1.0 | ✅ | 첫 태그. reactor 에서 이어진 기능 전부 |
| v0.1.1 | ✅ | 릴리스 워크플로에 wheel 빌드 |
| v0.2.0 | ✅ | 부분 diff 정상화(GAP-024·025), transport seam(GAP-026), on_mount(GAP-021), NATS 채널 레이어 전환, bench 인프라와 Windows 실측 |
| v0.3.0 | ✅ | live_session(GAP-009), Telemetry(GAP-022). GAP-012 는 설계상 제외로 정리 |
| v1.0.0 | ⬜ | API 안정화 선언. 그 전까지 마이너 버전이 호환성을 깰 수 있다 |

---

*마지막 업데이트: 2026-09-10*
