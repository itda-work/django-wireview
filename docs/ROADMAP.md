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
GAP-027 세션 분리는 1단계(컨슈머에서 `WireviewSession` 분리)가 `v1.0.0rc3` 에서, 3단계(업로드 분산 접근)가
#83 에서 끝났다. 2단계(세션 상태 export/import)와 4단계(프런트 어댑터)는 `docs/design/session-extraction.md`
5절의 착수 기준을 만족할 때 시작한다.
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
# 정본은 pyproject.toml
requires-python = ">=3.12"
dependencies = [
    "django>=5.2",
    "channels>=4.2.1,<5",
    "pydantic>=2.7,!=2.9.0,<3",
]
```

### 1.3 테스트 인프라 - ✅ 완료

- ✅ pytest-asyncio 설정
- ✅ pytest-django 설정
- ✅ `wireview.testing` 모듈
- ✅ 단위/통합 테스트 구조

---

## Phase 2: Core Features (핵심 기능) - ✅ 완료

### 2.1 JS 명령어 시스템 - ✅ 완료

**구현 완료**: `wireview/js.py`

```python
from wireview import JS, Component


class Editor(Component):
    # 템플릿은 인자를 받는 호출을 못 하므로 체인은 컴포넌트의 속성이 만든다
    @property
    def save_js(self) -> JS:
        return JS().toggle("#modal").push("save")

    async def save(self):
        ...

    async def reset(self):
        # Python에서 바로 실행
        await self.push_js(JS().set_value("input", "").focus("#next"))
```

```html
<button {% on "click" this.save_js %}>저장</button>
```

**지원 명령어**:
- `show()`, `hide()`, `toggle()` - 요소 표시/숨김
- `add_class()`, `remove_class()`, `toggle_class()` - 클래스 조작
- `set_attr()`, `remove_attr()` - 속성 조작
- `set_value()` - 입력 값 설정
- `focus()`, `focus_first()` - 포커스 이동
- `transition()` - CSS 전환 클래스 적용
- `push()` - 서버 이벤트 전송
- `dispatch()` - 브라우저 이벤트 발생
- `navigate()` - 페이지 이동

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

## Phase 3: Advanced Features (고급 기능) - ✅ 완료

### 3.1 Streams (대량 데이터) - ✅ 완료

**구현 완료**: `wireview/features/streams.py`

```python
class ItemList(Component):
    async def joined(self):
        # QuerySet은 async 컨텍스트에서 동기로 순회할 수 없으므로 먼저 목록으로 만든다
        await self.stream("items", [item async for item in Item.objects.all()[:100]])

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
        async for upload in self.consume_uploads("images"):
            path = await upload.save_to("uploads/")
```

### 3.3 비동기 작업 (assign_async) - ✅ 완료

**구현 완료**: `wireview/async_result.py`

```python
from wireview import AsyncResult, Component


class Dashboard(Component):
    stats: AsyncResult[Stats] | None = None  # 결과의 모델 인스턴스는 pk로 서명된다

    async def joined(self):
        self.stats = await self.assign_async(self._load_stats())

    async def _load_stats(self):
        return await Stats.objects.aget()
```

### 3.4 Temporary Assigns - ✅ 완료

**메모리 최적화를 위한 임시 할당**:

```python
class MessageList(Component):
    class Meta:
        temporary_assigns = {"messages"}

    messages: list[Message] = []  # 서명 상태에는 pk 목록이 실린다

    async def joined(self):
        self.messages = [message async for message in Message.objects.all()[:100]]
        # 렌더링 후 선언한 기본값 []으로 돌아간다
```

### 3.5 URL 파라미터 처리 - ✅ 완료

```python
async def params_changed(self, params: dict[str, str], uri: str):
    # URL이 바뀔 때마다 불린다. 여기서 다시 push_to하면 params_changed가 또 불려 끝나지 않는다
    self.page = int(params.get("page", "1"))

async def next_page(self):
    await self.wire.push_to(f"?page={self.page + 1}")
```

---

## Phase 4: Component System (컴포넌트 시스템) - ✅ 완료

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

### 4.2 JavaScript Hooks - ✅ 완료

**GitHub Issue**: #49 (GAP-001). 상세는 [features/hooks.md](./features/hooks.md)

```javascript
// myapp/static/myapp/hooks/chart.js — {% wireview_header %}가 모아 싣는다
window.wireview.hooks.Chart = {
  mounted() { this.chart = new Chart(this.el, JSON.parse(this.el.dataset.config)) },
  updated() { this.chart.update() },
  destroyed() { this.chart.destroy() }
}
```

```html
<canvas wire-hook="Chart" data-config='{"type": "line"}'></canvas>
```

### 4.3 Function Components - ✅ 완료

상세는 [features/function-components.md](./features/function-components.md)

```python
from django.utils.html import format_html

from wireview import function_component


@function_component
def button(text: str, variant: str = "primary"):
    return format_html('<button class="btn btn-{}">{}</button>', variant, text)
```

```html
{% func "button" text="Click me" variant="danger" %}
```

---

## Phase 5: Polish (완성도) - 🔄 진행 중

### 5.1 개발자 도구

- ⬜ 브라우저 확장 프로그램
- ✅ 클라이언트 디버그 로깅과 프로파일링 (`wireview.js`)
- ✅ 서버 계측: Telemetry 시그널(GAP-022, [features/telemetry.md](./features/telemetry.md)), sync/async 전환 감지(`DEBUG_SYNC_TRANSITIONS`)
- ✅ 타입 스텁·LSP 메타데이터 (`wireview_stubs`, `wireview_lsp`)

### 5.2 문서화

- ✅ 기능 레퍼런스 ([features/](./features/README.md))
- ✅ 튜토리얼 15편 ([tutorials/](./tutorials/README.md))
- ✅ 예제 앱 11개 (`examples/`)

### 5.3 TypeScript 클라이언트 재작성

- ✅ 타입 정의 (`static/wireview/types.d.ts`)
- 🔄 모듈화: 판단 로직을 순수 함수 모듈(`*.mjs`)로 분리하는 중. TypeScript 재작성은 하지 않았다

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
| v0.4.0 | ✅ | 이벤트 바인딩의 인라인 스크립트 제거(CSP, #90), 입력값 보존(#91·#92), 항목 재배열 diff(GAP-030) |
| v0.5.0 | ✅ | 1.0 전의 호환 파괴를 한 번에(#93): `class Meta:`, 공개 API 경계, 레거시 경로·`USE_HMIN` 제거, Django 5.2+. 핸들러 예외 복구(#94), Origin 검사(#96). [업그레이드 가이드](./UPGRADING.md) |
| v0.6.0 | ✅ | 1.0 전 계약 바로잡기. 초기화된 temporary assign이 화면에서 지워지지 않음(GAP-006), 훅의 소유·수명·응답 짝(#107·#108), 알림 예제의 사용자별 알림과 토스트(#41). 호환이 깨지는 넷은 [업그레이드 가이드](./UPGRADING.md#05에서-06으로) |
| v0.7.0 | ✅ | 실사용 앱에서 나온 버그와 작은 추가. 로딩 표시와 응답 짝짓기(#118), 리다이렉트 뒤 주소(#104), join 뒤 무한 스크롤 판단(#112, 프로토콜 5), 스텁 결정성(#109), `wire-update="ignore"`(#102), `wire-boost` 폼과 `wireview.visit()`(#103), 테스트의 `render_diff()`(#117). 호환 변경 없음 |
| v1.0.0rc1 | ✅ | 0.7.0의 코드 그대로, 호환 변경 없음. RC 동안 새 `bug` 이슈가 잦아드는지와 실사용 앱 도그푸딩을 본다(#93) |
| v1.0.0rc2 | ✅ | 1.0 동결 전 공개 API 정리(#119): 공개 범위 선언, `Component` 멤버·설정 레퍼런스, `handle_async`의 `AsyncResult`, `mount()` 키워드 전용, m2m 채널, `dom()` 제거 등. rc1에서 올리는 절차는 [업그레이드 가이드](./UPGRADING.md) |
| v1.0.0rc3 | ✅ | rc2 뒤의 호환 추가만: `LiveComponent.update_many()`(GAP-035), `toast()`·`atoast()`·`{% wireview_toasts %}`(#116), sticky 컴포넌트(GAP-033), JavaScript 없는 페이지의 약속 문서화(GAP-034), 세션 로직의 `WireviewSession` 분리(GAP-027 1단계). 호환 변경 없음 |
| v1.0.0rc4 | ✅ | 리뷰 후속과 CI 게이트. `AUTO_BROADCAST`는 `senders`에 적은 모델만 알린다(빈 `senders`는 아무것도 연결하지 않음, W015). async 안전장치(#120), 렌더 중 작업을 미루는 RenderGate(#138·#147), join ref와 프로토콜 6(#139·#146), 스타터 템플릿(#131), W016(#134). 의존성 하한 상향(`channels>=4.2.1`, `pydantic>=2.7,!=2.9.0`, #132). 릴리스 게이트에 하한·Redis E2E 레인. 동작이 바뀌는 것은 [업그레이드 가이드](./UPGRADING.md) |
| v1.0.0 | ⬜ | API 안정화 선언. 이후 규칙은 [호환성 정책](./COMPATIBILITY.md) |

### 릴리스 절차

1. `CHANGELOG.md`의 Unreleased를 버전 절로 옮긴다.
2. `pyproject.toml`과 `package.json`의 `version`을 함께 올린다(`tests/test_packaging.py`가 둘을 비교한다).
   1.0.0에서는 classifier를 `Development Status :: 5 - Production/Stable`로 바꾼다.
3. `make quality`, `make test`, `make test-latest`, `make test-lowest`, `make test-e2e`, `make test-e2e LAYER=redis`, `make test-matrix`, `make ci-build`, `make ci-smoke`.
   태그 뒤의 게이트와 같은 것을 먼저 로컬에서 본다 — 게이트에서 떨어지면 태그를 지우고 다시 찍어야 한다.
4. 워크플로나 액션 버전을 바꿨다면 태그 전에 `gh workflow run release.yml`로 dry run을 돌린다. 게이트까지 똑같이 돌고
   배포만 하지 않는다.
5. 태그 `v<버전>`을 push한다. `.github/workflows/release.yml`이 다음을 모두 통과해야 PyPI에 올린다(#122).
   - **ci**: `ci.yml` 전체를 태그 커밋에 대해 부른다(`workflow_call`). Python × Django 매트릭스, 새 설치가 받는
     최신 의존성(`test-latest`, #127), 하한 의존성(`test-lowest`, #132), NATS·Redis 레이어의 E2E(#130), lint, typecheck, 패키지 빌드. `ci.yml`에 job을 더하면 게이트도 넓어진다.
   - **build**: 태그와 `pyproject.toml`의 버전이 같은지 보고 `make ci-build`. PyPI 페이지가 되는 README, 프로젝트 URL, wheel에 싣는
     스킬의 `main` 링크는 빌드가 그 태그로 바꾼다(`hatch_build.py`). 그래서 태그 이름은 반드시 `v<버전>`이다.
   - **smoke**: 빌드한 wheel을 lock 없이 새로 해석한 의존성에 설치해 import와 `check`를 돈다(`make ci-smoke`).
     rc3처럼 lock의 버전에서만 import되는 산출물은 여기서 멈춘다.

   E2E가 불안정해 게이트가 떨어졌다면 Actions의 "Re-run failed jobs"로 그 job만 다시 돌린다. 통과하면 publish가 이어진다.
   게이트는 태그 push와 dry run에서만 돈다. 평소 `ci.yml`은 여전히 수동 실행 전용이다.

---

*마지막 업데이트: 2026-09-30*
