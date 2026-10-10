# django-wireview 로드맵

> Phoenix LiveView 수준의 DX를 향한 개발 로드맵

---

## 개요

```
현재 버전: pyproject.toml 과 git 태그 v<버전> 이 정본 (편집기 확장은 따로: vscode-v<버전>)
남은 갭 목록: docs/FEATURE-GAP.md 의 GAP-nnn 이 정본

Phase 1: Foundation     ████████████████████ 완료
Phase 2: Core Features  ████████████████████ 완료
Phase 3: Advanced       ████████████████████ 완료
Phase 4: Component      ████████████████████ 완료
Phase 5: Polish         ████████████████░░░░ 진행 중 (남은 둘은 1.0 범위 밖)
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

**지원 수정자** (정본은 `wireview/event_transpiler.py`의 `MODIFIERS`, 적용 순서는 [csp](./features/csp.md#수정자)):
- `.prevent`, `.stop` - 기본 동작/전파 방지
- `.debounce.N`, `.throttle.N` - 디바운스/스로틀
- `.ctrl`, `.alt`, `.shift`, `.meta` - 보조 키가 눌렸을 때만
- `.key.<이름>`, `.key_code.N`, `.enter`, `.tab`, `.delete`, `.backspace`, `.esc`, `.space`, `.up`, `.down`, `.left`, `.right` - 그 키일 때만

Phoenix·Alpine의 `.capture`, `.once`, `.passive`, `.self`, `.away`는 없다.

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

반환한 문자열은 이스케이프 없이 그대로 출력된다. 인자를 f-string으로 끼우면 XSS가 되므로 `format_html`로 만든다.

```html
{% func "button" text="Click me" variant="danger" %}
```

---

## Phase 5: Polish (완성도) - 🔄 진행 중

1.0은 Phase 1~4와 아래의 ✅ 항목으로 낸다. 남은 둘(⬜ 브라우저 확장, 🔄 TypeScript 재작성)은 **1.0 범위 밖이고**
날짜를 약속하지 않는다. 둘 다 공개 API를 바꾸지 않는 작업이라 1.x의 마이너 릴리스에서 할 수 있다.

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

패키지 버전은 git 태그 `v<버전>`(`v1.5.1`)이 정본이다. 편집기 확장의 태그 `vscode-v<버전>`은 따로 매긴다([VS Code 확장 릴리스 절차](#vs-code-확장-릴리스-절차)). reactor 시절의 v6.0.0 마일스톤 번호는 쓰지 않는다.

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
| v1.0.0 | ✅ | API 안정화 선언. 이후 규칙은 [호환성 정책](./COMPATIBILITY.md). rc4 뒤의 결함 수정과 보안 권고 셋(GHSA-8q8p-x4w4-p745, GHSA-4v8p-p6p8-78pj, GHSA-pv9v-gqcj-f42x), `mutation()` 인스턴스의 보통 저장(#153), LiveComponent·중첩 컴포넌트의 수명주기와 슬롯·temporary assign 정합성, 문서 사이트와 릴리스 묶음(#157·#160). 동작이 바뀌는 것은 [업그레이드 가이드](./UPGRADING.md#100rc4에서-10으로) |
| v1.1.0 | ✅ | 첫 마이너. `AUTO_BROADCAST.senders`가 모델마다 보낼 필드를 적는 매핑도 받는다(집합은 그대로 모든 필드, #144), 비밀번호 해시·세션 키를 보내는 설정을 알리는 `wireview.W017`, 문서 사이트의 llms.txt와 에이전트 스킬 Markdown 게시(#164·#165). 호환 변경 없음. 할 일은 [업그레이드 가이드](./UPGRADING.md#10에서-11로) |
| v1.2.0 | ✅ | 내비게이션을 브라우저와 Phoenix에 맞춘다. 같은 경로의 `push_to`·`replace_to`는 다시 가져오지 않는 patch(#169), join은 첫 렌더 앞에서 `params_changed()`, boost 이동의 params는 새 페이지가 화면에 놓인 뒤 `navigated`로(프로토콜 7), 조각 링크·같은 URL·폼 method·`no-cors` 폼·실패하거나 중지된 가져오기와 `wireview:navigation-failed`(#170), IME 조합 중인 칸의 값 보존(#169). 테스트의 `mount(path=...)`·`follow_push(Destination)`와 `path=` 없는 `follow_push()`의 폐기. 문서 사이트의 자기 자산과 묶음 mtime(#166), README와 WHY(#167), 문서의 주장을 지키는 브라우저 테스트(#168). 조용히 달라지는 동작이 여럿이다. 할 일은 [업그레이드 가이드](./UPGRADING.md#11에서-12로) |
| v1.3.0 | ✅ | 팬아웃을 싸게 하고 개발 루프를 줄인다. 스트림 항목·훅 이벤트·JS를 한 번 렌더해 구독자마다 id만 끼우는 `Broadcast`(#178), 보는 사람을 읽지 않는 렌더를 같은 메시지의 연결들이 함께 쓰는 `Meta.shared_render`(#176)와 `wireview.W019`, 평범한 값의 렌더 지름길(라이브 렌더 약 3분의 1 감소), 개발 서버에서 템플릿을 저장하면 열린 페이지가 다시 join하는 rejoin(#180, 프로토콜 8), 취소할 수 있는 `wireview:before-navigate`와 이동 종류(#154), 첫 HTTP 렌더의 `params_changed()`와 `request.GET`의 쿼리, `wireview_check_templates`(#179), `wireview_lsp` 메타데이터 2.0(#162), FastAPI 비교 벤치(#174). 조용히 달라지는 동작이 있다. 할 일은 [업그레이드 가이드](./UPGRADING.md#12에서-13으로) |
| v1.4.0 | ✅ | 개발 중 SQL을 부른 자리를 알린다. 렌더·핸들러·작업이 실행한 문장마다 템플릿 줄이나 property를 로거 `wireview.queries`에 남기고 같은 자리의 반복을 `WARNING`으로(`DEBUG_RENDER_QUERIES`, #182), 테스트는 `MountedComponent.queries()`로 단언한다. 같은 기록을 개발 서버가 JSON 줄로 남겨 VS Code 확장이 인라인 힌트로 보인다(#188). 첫 HTTP 렌더가 요청의 DB 연결을 닫아 `ATOMIC_REQUESTS`의 쓰기가 조용히 롤백되던 결함 수정(#190). 할 일은 [업그레이드 가이드](./UPGRADING.md#13에서-14로) |
| v1.5.0 | ✅ | 템플릿이 읽는 것만 계산한다. `Meta.lazy_properties`를 켠 컴포넌트는 sync property를 템플릿이 그 이름을 읽을 때 렌더마다 한 번 계산하므로 쓰지 않는 property의 SQL이 돌지 않는다(#187). 렌더 SQL 로그와 편집기 기록이 라이브 렌더와 그 `LiveComponent`들(#189), 뷰 템플릿이 그린 컴포넌트들(#193)을 한 일로 묶어 형제 사이의 N+1을 반복으로 보인다. 초기화된 temporary assign 뒤에 남긴 블록이 `{% class %}`·extends 부모 등으로 읽은 바뀐 값을 다시 그리지 않던 결함 수정(#194). ASGI 서버 비교 벤치(#191). 할 일은 [업그레이드 가이드](./UPGRADING.md#14에서-15로) |
| v1.5.1 | ✅ | 문서 사이트만 바뀐 패치. 모든 페이지 헤더가 그 문서가 설명하는 릴리스를 태그 배지로 보이고, 그 릴리스의 CHANGELOG 절로 잇는다(#196). 라이브러리 코드는 1.5.0과 같다. 할 일 없음 |

### 릴리스 절차

1. `CHANGELOG.md`의 Unreleased를 버전 절로 옮긴다. 아래 비교 링크도 고친다: `[Unreleased]`는 새 태그부터
   `HEAD`까지, 새 절은 앞 태그부터 새 태그까지(`tests/test_changelog.py`가 본다). 그 사이 공개된 보안 권고는
   `### Security`에 GHSA 링크·영향 버전·조치와 함께 적고 `SECURITY.md`의 권고 표에도 더한다. yank한 릴리스는
   제목에 `[YANKED]`를 붙인다. 옮기기 전에 그 절의 항목을 하나씩 `docs/UPGRADING.md`의 그 버전 절과 대조한다 —
   업그레이드하는 프로젝트가 할 일이 있으면 그 절에 적고(조용히 달라지면 **조용함**), 없으면 그 이유를
   `tests/test_upgrading.py`의 표에 적는다. 여러 브랜치가 각자 CHANGELOG에만 적은 변경이 이렇게 빠졌다. 그 표는
   1.2에서 1.3으로 가는 절을 본다(`PREVIOUS`·`SECTION`). 다음 릴리스는 표와 절을 그 버전으로 옮긴다.
2. `pyproject.toml`과 `package.json`의 `version`을 함께 올리고, `uv lock`과 `npm install --package-lock-only`로
   두 lock의 버전도 맞춘다(`tests/test_packaging.py`가 넷을 비교한다). 사전 릴리스가 아니면 classifier가
   `Development Status :: 5 - Production/Stable`이어야 한다(같은 파일이 본다).
3. 이 문서의 릴리스 이력 표에 행을 ✅로 두고 맨 아래 "마지막 업데이트" 날짜를 바꾼다. 마이너·메이저 릴리스면
   `SECURITY.md`의 지원 버전 표("지금은 1.3.x")와 `docs/UPGRADING.md`의 "어디서 오나" 표·버전 범위를 새 버전에 맞춘다.
4. `make quality`, `make test`, `make test-latest`, `make test-lowest`, `make test-e2e`, `make test-e2e LAYER=redis`, `make test-matrix`, `make ci-build`, `make ci-smoke`, `make docs-site-bundle`(`make docs-site`를 먼저 돈다).
   태그 뒤의 게이트와 같은 것을 먼저 로컬에서 본다 — 게이트에서 떨어지면 태그를 지우고 다시 찍어야 한다.
5. 워크플로나 액션 버전을 바꿨다면 태그 전에 `gh workflow run release.yml`로 dry run을 돌린다. 게이트까지 똑같이 돌고
   배포만 하지 않는다. publish는 태그 push 이벤트에서만 돌므로 태그를 `--ref`로 골라 수동 실행해도 배포하지 않는다(#163).
6. 태그 `v<버전>`을 push한다. `.github/workflows/release.yml`이 다음을 모두 통과해야 PyPI에 올린다(#122).
   - **ci**: `ci.yml` 전체를 태그 커밋에 대해 부른다(`workflow_call`). Python × Django 매트릭스, 새 설치가 받는
     최신 의존성(`test-latest`, #127), 하한 의존성(`test-lowest`, #132), NATS·Redis 레이어의 E2E(#130), lint, typecheck, 패키지 빌드,
     문서 사이트 빌드(`docs-site` 잡: `make docs-site`, 문서 가드와 링크·앵커·사라진 URL 관문, #160). `ci.yml`에 job을 더하면 게이트도 넓어진다.
   - **build**: 태그와 `pyproject.toml`의 버전이 같은지 보고 `make ci-build`. PyPI 페이지가 되는 README, 프로젝트 URL, wheel에 싣는
     스킬의 `main` 링크는 빌드가 그 태그로 바꾼다(`hatch_build.py`). 그래서 태그 이름은 반드시 `v<버전>`이다.
   - **smoke**: 빌드한 wheel을 lock 없이 새로 해석한 의존성에 설치해 import와 `check`를 돈다(`make ci-smoke`).
     rc3처럼 lock의 버전에서만 import되는 산출물은 여기서 멈춘다. `dist/`에 wheel 하나와 sdist 하나 말고 다른 파일이
     있으면 실패한다 — PyPI는 `dist/`를 통째로 받는다. 문서 묶음도 내려받아 풀어 본다.
   - **docs**: `make docs-site-bundle`로 문서 사이트를 `docs-site-v<버전>.tar.gz`로 묶는다. `dist/` 밖(`build/site-dist/`)에
     만들어 별도 artifact로 올리고, 파일 이름과 묶음 안의 `wireview/VERSION`이 태그와 같은지 본다. 같은 커밋은 같은 바이트로 묶인다 —
     항목의 mtime은 0이 아니라 태그 커밋의 커밋 시각이다(`SOURCE_DATE_EPOCH`가 있으면 그것, #166). 묶음이 itda.work에
     약속하는 것(최상위 `wireview/`, `VERSION`, 있어야 하는 파일, 해시 자산 이름, 외부 출처)은
     [묶음 계약](./implementation/docs-site-bundle.md)이 정본이다. 바꾸려면 website 저장소에 먼저 알린다.
     서빙(텍스트 파일의 `charset=utf-8` 포함, #165)은 website가 맡는다(website #221, RUNBOOK §18).
   - **publish**: 위 모두를 기다린다 — 문서 빌드가 실패하면 publish는 돌지 않는다. PyPI에는 `dist/`만 올리고, 문서 묶음에
     빌드 증명(`actions/attest`)을 붙인 뒤 GitHub Release에 `dist/*`와 묶음을 함께 붙인다. 내려받은 묶음은
     `gh attestation verify docs-site-v<버전>.tar.gz -R itda-work/django-wireview`로 확인한다.

   E2E가 불안정해 게이트가 떨어졌다면 Actions의 "Re-run failed jobs"로 그 job만 다시 돌린다. 통과하면 publish가 이어진다.
   게이트는 태그 push와 dry run에서만 돈다. 평소 `ci.yml`은 여전히 수동 실행 전용이다.
7. 문서 사이트를 갱신한다. Release가 게시된 뒤 website 저장소(`itda-skills/website`)에서 돌린다(website RUNBOOK §18):

   ```bash
   just deploy-wireview-docs vX.Y.Z --dry-run
   just deploy-wireview-docs vX.Y.Z
   ```

   recipe가 Release의 묶음을 내려받아 빌드 증명과 [묶음 계약](./implementation/docs-site-bundle.md)을 검사한 뒤 갈아 끼운다.
   `https://itda.work/wireview/VERSION`이 새 태그(`vX.Y.Z`)면 끝이다.

### VS Code 확장 릴리스 절차

편집기 확장(`editors/vscode/`, Marketplace·Open VSX의 `itda.django-wireview`)은 라이브러리와 따로 릴리스한다.
확장의 버전·게시 절차·운영 메모는 이 절이 정본이다.

- **버전.** 라이브러리와 독립된 semver이고 `editors/vscode/package.json`의 `version`이 정본이다. 라이브러리와 확장
  사이의 약속은 [메타데이터 형식](./features/editor-support.md)뿐이라, 한쪽이 올라도 다른 쪽을 올리지 않는다. Marketplace는
  `0.2.0-beta.1` 같은 사전 릴리스 버전을 받지 않으므로 `x.y.z`만 쓴다.
- **변경 기록.** `editors/vscode/CHANGELOG.md`(영어). 작업 중에는 다음 버전의 절 `## [<버전>] - Unreleased`에 쌓고,
  날짜는 게시 커밋에서 넣는다(`## [<버전>] - YYYY-MM-DD`). 날짜 없는 절로 태그를 찍으면 워크플로가 실패한다. 그 절이
  GitHub Release 본문이 되고, 파일 전체가 Marketplace 페이지의 Changelog 탭이 된다. 확장이 읽는 메타데이터 버전의 범위가
  바뀌면 그 파일의 머리말도 고친다.
- **태그.** `vscode-v<버전>`. 라이브러리의 `release.yml`은 `v[0-9]*`만 보므로 확장 태그로 PyPI 릴리스가 돌지 않는다.

1. 게시 커밋: `editors/vscode/CHANGELOG.md`의 절에 날짜를 넣고 `editors/vscode/package.json`의 `version`을 맞춘 뒤
   `editors/vscode`에서 `npm install --package-lock-only`로 lock의 버전도 맞춘다.
2. `make ext-check ext-test ext-test-host ext-package`. 만든 `.vsix`를 `code --install-extension`으로 설치해 본다.
3. 워크플로나 액션 버전을 바꿨다면 태그 전에 `gh workflow run vscode-release.yml --ref main`으로 dry run을 돌린다. 게이트와
   패키징까지 똑같이 돌고, 게시 대신 Marketplace 신원 ID를 출력한다(아래 운영 메모). 게시 잡은 태그 push 이벤트에서만 돌므로
   이미 있는 태그를 `--ref`로 골라 수동 실행해도 게시하지 않는다.
4. 태그 `vscode-v<버전>`을 push한다. `.github/workflows/vscode-release.yml`이 다음을 모두 통과해야 게시한다.
   - **vscode-extension**, **vscode-extension-host**: `ci.yml`의 같은 이름 잡을 그대로 옮긴 게이트(타입 검사·단위 테스트·
     패키징, 내려받은 VS Code에서의 호스트 테스트). 라이브러리의 파이썬 매트릭스는 돌지 않는다. 두 사본이 같은지는
     `tests/test_packaging.py`가 본다 — `ci.yml`의 잡을 고치면 이쪽도 고친다.
   - **package**: 태그가 `vscode-v<package.json의 version>`인지, CHANGELOG에 날짜 있는 절이 있는지 보고 `.vsix`를 한 번 만든다.
     README·CHANGELOG의 상대 링크와 이미지는 `main`이 아니라 그 태그를 가리킨다. 아이콘·README가 들었는지 보고, CHANGELOG의
     그 절을 릴리스 노트로 잘라 `.vsix`와 함께 artifact로 올린다.
   - **marketplace**, **open-vsx**: 같은 `.vsix`를 `--skip-duplicate`로 올린다. 따로 도는 잡이라 한쪽만 실패하면 Actions의
     "Re-run failed jobs"로 그쪽만 다시 돌린다. 레지스트리가 버전을 받아 둔 뒤에 잡이 실패했어도(응답이 끊김, 러너 종료) 다시 돌린
     잡은 이미 있는 그 버전을 성공으로 보고 넘어가므로, 그 뒤 `github-release`까지 이어진다. 다시 돌려도 태그 push의 실행이라
     게시 잡이 돈다.
   - **github-release**: 둘 다 끝나면 태그 `vscode-v<버전>`의 GitHub Release에 `.vsix`를 붙인다. 저장소의 latest 릴리스는
     라이브러리의 것이므로 이 Release는 latest가 되지 않는다(`make_latest: false`).
5. [Marketplace 페이지](https://marketplace.visualstudio.com/items?itemName=itda.django-wireview)와
   [Open VSX 페이지](https://open-vsx.org/extension/itda/django-wireview)에 새 버전이 보이면 끝이다. Marketplace는 올린 뒤
   검사에 몇 분이 걸린다.

**운영 메모.** 토큰은 어디에도 저장하지 않는다. Azure DevOps의 global PAT는 2026-12-01에 폐지되므로 Marketplace는
Microsoft Entra ID 관리 ID로, Open VSX는 Trusted Publishing(OIDC)으로 올린다. 처음 한 번 맞춰 둔 것은 다음과 같다.

- **Marketplace publisher** `itda`. 확장 ID는 `itda.django-wireview`다.
- **Azure**: 구독 '종량제', 리소스 그룹 `rg-wireview-publish`, 사용자 할당 관리 ID `id-wireview-vscode-publish`(koreacentral).
  그 federated credential의 subject는 `repo:itda-work/django-wireview:environment:vscode-marketplace`(GitHub Actions 발급자,
  audience는 기본값). 구독의 역할 할당은 필요 없다 — `azure/login`이 `allow-no-subscriptions`로 로그인한다.
- **GitHub environment** `vscode-marketplace`: 배포는 태그 `vscode-v*`와 브랜치 `main`(dry run)만. 변수(secret 아님)
  `AZURE_CLIENT_ID`(관리 ID의 클라이언트 ID)와 `AZURE_TENANT_ID`. 게시하는 세 잡이 모두 이 environment에서 돈다.
- **Marketplace Members**: dry run의 `marketplace-identity` 잡이 출력하는 ID(실행 요약에도 남는다)를 publisher `itda`의
  Members에 Contributor로 더한다. 그 전에는 `vsce publish`가 권한 오류로 실패한다.
- **Open VSX**: 네임스페이스 `itda`의 Trusted Publishing에 저장소 `itda-work/django-wireview`, 워크플로
  `vscode-release.yml`, environment `vscode-marketplace`를 등록한다. 게시는 `open-vsx` 잡이 한다.
  등록에는 두 조건이 있었다. 네임스페이스의 **소유자**여야 하고(만든 사람은 검증 전까지 기여자다 — 소유권 주장
  [EclipseFdn/open-vsx.org#13895](https://github.com/EclipseFdn/open-vsx.org/issues/13895)), 확장에 활성 버전이 있어야 한다.
  그래서 0.1.0은 태그 실행의 `open-vsx` 잡이 실패한 뒤, 그 실행의 vsix artifact를 일회용 access token으로 직접 올리고
  (토큰은 바로 폐기) 등록한 다음 실패한 잡을 다시 돌렸다(`--skip-duplicate`). 미검증 네임스페이스의 버전은 공개되지 않는다.
- 관리 ID를 다시 만들면 클라이언트 ID와 Marketplace 신원 ID가 바뀐다. environment 변수를 고치고 dry run으로 새 ID를 얻어
  Members를 다시 맞춘다. 워크플로 파일 이름이나 environment 이름을 바꾸면 federated credential의 subject와 Open VSX
  등록도 함께 바꾼다.

---

*마지막 업데이트: 2026-10-10*
