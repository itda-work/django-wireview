# django-reactor 로드맵

> 단계별 현대화 및 기능 확장 계획

---

## 개요

```
현재 버전: v5.3.0b0
목표 버전: v6.0.0 (Phoenix LiveView 수준의 DX)

Phase 1: Foundation     ████████░░░░░░░░░░░░ 기반 현대화
Phase 2: Core Features  ░░░░░░░░░░░░░░░░░░░░ 핵심 기능
Phase 3: Advanced       ░░░░░░░░░░░░░░░░░░░░ 고급 기능
Phase 4: Polish         ░░░░░░░░░░░░░░░░░░░░ 완성도
```

---

## Phase 1: Foundation (기반 현대화)

### 1.1 Pydantic v2 마이그레이션

**목표**: Pydantic v1 → v2 완전 마이그레이션

| 작업 | 파일 | 상태 | 난이도 |
|------|------|:----:|:------:|
| BaseModel 설정 변경 | `component.py` | ⬜ | 중 |
| Field 문법 업데이트 | `component.py` | ⬜ | 중 |
| validator → field_validator | `component.py` | ⬜ | 중 |
| .dict() → .model_dump() | 전체 | ⬜ | 하 |
| json_encoders → 별도 처리 | `component.py` | ⬜ | 중 |
| ModelField → FieldInfo | `component.py` | ⬜ | 상 |
| validate_arguments 대체 | `component.py` | ⬜ | 상 |

**예상 변경 사항**:
```python
# Before (Pydantic v1)
class Component(BaseModel):
    class Config:
        arbitrary_types_allowed = True
        validate_assignment = True
        json_encoders = {...}

# After (Pydantic v2)
class Component(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    @field_serializer('user')
    def serialize_user(self, user):
        return user.pk
```

**참고 문서**: [MIGRATION-PYDANTIC.md](./MIGRATION-PYDANTIC.md)

---

### 1.2 의존성 업데이트

**목표**: 최신 안정 버전으로 업데이트

```toml
# setup.cfg 변경
[options]
python_requires = >=3.10
install_requires =
    django>=4.2
    channels>=4,<5
    pydantic>=2.0,<3     # v1 → v2
```

| 패키지 | 현재 | 목표 | 비고 |
|--------|------|------|------|
| pydantic | >=1.8,<2 | >=2.0,<3 | **핵심 변경** |
| channels | >=4,<5 | >=4,<5 | 유지 |
| django | 미지정 | >=4.2 | 명시 추가 |
| lru-dict | >=1.2.0,<2 | 유지 또는 제거 | 검토 필요 |

**JavaScript 의존성**:
```json
{
  "dependencies": {
    "idiomorph": "^0.3.0",           // 0.0.8 → 0.3.0
    "reconnecting-websocket": "^4.4.0"  // 유지
  },
  "devDependencies": {
    "esbuild": "^0.20.0",            // 0.13.9 → 0.20.0
    "typescript": "^5.3.0"           // 신규 추가
  }
}
```

---

### 1.3 TypeScript 클라이언트 재작성

**목표**: 타입 안전성 및 유지보수성 향상

**현재 구조**:
```
reactor/static/reactor/
├── reactor.js        (349줄, 순수 JS)
└── reactor-boost.js  (morphdom 래퍼)
```

**목표 구조**:
```
reactor/static/reactor/
├── src/
│   ├── index.ts
│   ├── connection.ts       # WebSocket 관리
│   ├── component.ts        # 컴포넌트 클래스
│   ├── commands.ts         # JS 명령어 시스템
│   ├── diff.ts             # HTML Diff 적용
│   └── types.ts            # 타입 정의
├── dist/
│   └── reactor.min.js      # 번들 결과물
├── tsconfig.json
└── esbuild.config.ts
```

**작업 목록**:
- [ ] TypeScript 설정 (`tsconfig.json`)
- [ ] 타입 정의 (`types.ts`)
- [ ] ServerConnection 클래스 변환
- [ ] ReactorComponent 클래스 변환
- [ ] 빌드 스크립트 업데이트

---

### 1.4 테스트 인프라 구축

**목표**: 테스트 커버리지 80%+

```
tests/
├── unit/
│   ├── test_component.py
│   ├── test_consumer.py
│   ├── test_diff.py
│   └── test_serializer.py
├── integration/
│   ├── test_websocket.py
│   └── test_broadcast.py
├── e2e/
│   └── test_counter.py
└── conftest.py
```

**작업 목록**:
- [ ] pytest-asyncio 설정
- [ ] pytest-django 설정
- [ ] 컴포넌트 단위 테스트
- [ ] WebSocket 통합 테스트
- [ ] CI/CD 파이프라인 (GitHub Actions)

---

## Phase 2: Core Features (핵심 기능)

### 2.1 JS 명령어 시스템

**목표**: Phoenix LiveView.JS 스타일 클라이언트 명령어

**설계**:
```python
# Python 측
class JS:
    def show(self, selector: str, transition: str = None) -> "JS": ...
    def hide(self, selector: str, transition: str = None) -> "JS": ...
    def toggle(self, selector: str) -> "JS": ...
    def add_class(self, selector: str, classes: str) -> "JS": ...
    def remove_class(self, selector: str, classes: str) -> "JS": ...
    def set_attribute(self, selector: str, attr: str, value: str) -> "JS": ...
    def push(self, event: str, **kwargs) -> "JS": ...
    def dispatch(self, event: str, **kwargs) -> "JS": ...
    def focus(self, selector: str) -> "JS": ...
    def navigate(self, url: str) -> "JS": ...
```

**사용 예시**:
```html
<button {% on "click" JS().toggle("#modal").push("save") %}>
  저장
</button>
```

**참고 문서**: [implementation/js-commands.md](./implementation/js-commands.md)

---

### 2.2 Optimistic UI

**목표**: 서버 응답 대기 중 즉각적 UI 피드백

**기능**:
1. **CSS 로딩 클래스 자동 적용**
   - `reactor-click-loading`
   - `reactor-submit-loading`
   - `reactor-change-loading`

2. **JS 명령어 즉시 실행**
   - 서버 이벤트 전송 전 클라이언트 명령 실행

**구현**:
```html
<!-- 클릭 시 자동으로 클래스 추가/제거 -->
<button {% on "click" "save" %}
        class="reactor-click-loading:opacity-50">
  저장
</button>
```

```css
/* 사용자 CSS */
.reactor-click-loading {
  opacity: 0.5;
  cursor: wait;
}
```

---

### 2.3 개선된 HTML Diff

**목표**: 대역폭 효율성 향상

**현재**: difflib 기반 라인 diff
```python
diff = [0, 1, '<div>new</div>\n', 3]  # 라인 인덱스 + 문자열
```

**목표**: 템플릿 슬롯 기반 바이너리 diff
```python
# 템플릿: <div>{{ count }}</div>
# 변경 시: {"0": "5"}  # 슬롯 인덱스만 전송
```

**작업 목록**:
- [ ] 템플릿 파싱 및 슬롯 추출
- [ ] 슬롯 기반 diff 생성
- [ ] 클라이언트 측 diff 적용 로직
- [ ] 벤치마크 및 최적화

---

## Phase 3: Advanced Features (고급 기능)

### 3.1 Streams (대량 데이터)

**목표**: 메모리 효율적인 대량 리스트 처리

**설계**:
```python
class ItemList(Component):
    async def mount(self):
        # 스트림 초기화 - 메모리에 저장하지 않음
        await self.stream("items", Item.objects.all()[:100])

    async def add_item(self, name: str):
        item = await Item.objects.acreate(name=name)
        # 단일 항목만 클라이언트로 전송
        await self.stream_insert("items", item, at=0)

    async def remove_item(self, item_id: int):
        await Item.objects.filter(id=item_id).adelete()
        await self.stream_delete("items", item_id)
```

**참고 문서**: [implementation/streams.md](./implementation/streams.md)

---

### 3.2 파일 업로드

**목표**: 실시간 업로드 진행률 및 프리뷰

**설계**:
```python
class ImageUploader(Component):
    def mount(self):
        self.allow_upload(
            "images",
            accept=[".jpg", ".png", ".gif"],
            max_entries=5,
            max_file_size=10_000_000,  # 10MB
        )

    async def save_images(self):
        async for entry in self.consume_uploads("images"):
            path = await entry.save_to("uploads/")
            await Image.objects.acreate(path=path)
```

```html
{% load reactor %}

<form {% on "submit" "save_images" %}>
  {% upload_input "images" %}

  {% for entry in uploads.images.entries %}
    {% upload_preview entry %}
    <progress value="{{ entry.progress }}" max="100"></progress>
  {% endfor %}

  <button type="submit">업로드</button>
</form>
```

**참고 문서**: [implementation/uploads.md](./implementation/uploads.md)

---

### 3.3 비동기 작업 (assign_async)

**목표**: 비동기 데이터 로딩 패턴

**설계**:
```python
class Dashboard(Component):
    stats: AsyncResult[Stats] = None
    recent_orders: AsyncResult[list[Order]] = None

    async def mount(self):
        # 비동기로 데이터 로딩 시작
        self.stats = await self.assign_async(self.load_stats())
        self.recent_orders = await self.assign_async(self.load_orders())

    async def load_stats(self):
        await asyncio.sleep(1)  # 느린 쿼리 시뮬레이션
        return await Stats.objects.aget()

    async def load_orders(self):
        return await Order.objects.order_by('-created')[:10].alist()
```

```html
{% if stats.loading %}
  <div class="skeleton">로딩 중...</div>
{% elif stats.error %}
  <div class="error">{{ stats.error }}</div>
{% else %}
  <div>총 매출: {{ stats.result.total_revenue }}</div>
{% endif %}
```

---

## Phase 4: Polish (완성도)

### 4.1 개발자 도구

**목표**: 디버깅 및 프로파일링 지원

**기능**:
```javascript
// 브라우저 콘솔에서
window.Reactor.enableDebug();      // 상세 로깅
window.Reactor.enableProfiling();  // 성능 측정
window.Reactor.enableLatencySim(200);  // 200ms 지연 시뮬레이션
```

**참고 문서**: [implementation/devtools.md](./implementation/devtools.md)

---

### 4.2 테스트 유틸리티

**목표**: 컴포넌트 테스트 편의성

**설계**:
```python
from reactor.testing import ComponentTestCase

class TestCounter(ComponentTestCase):
    async def test_increment(self):
        # 컴포넌트 마운트
        view = await self.mount(Counter, count=0)

        # 이벤트 발생
        await view.click("increment")

        # 상태 검증
        assert view.component.count == 1

        # 렌더링 결과 검증
        assert "Count: 1" in view.html
```

---

### 4.3 문서화

**목표**: 완전한 공식 문서

```
docs/
├── getting-started/
│   ├── installation.md
│   ├── quickstart.md
│   └── first-component.md
├── guides/
│   ├── components.md
│   ├── events.md
│   ├── forms.md
│   ├── uploads.md
│   └── testing.md
├── reference/
│   ├── component-api.md
│   ├── template-tags.md
│   └── js-commands.md
└── examples/
    ├── todo-app.md
    ├── chat-app.md
    └── dashboard.md
```

---

## 마일스톤 요약

| Phase | 주요 목표 | 예상 작업량 |
|-------|----------|------------|
| **Phase 1** | Pydantic v2, TS 클라이언트, 테스트 | 중규모 |
| **Phase 2** | JS 명령어, Optimistic UI | 중규모 |
| **Phase 3** | Streams, 업로드, 비동기 | 대규모 |
| **Phase 4** | 개발자 도구, 문서화 | 중규모 |

---

## 작업 우선순위 매트릭스

```
중요도
  ↑
  │  ┌─────────────┬─────────────┐
  │  │ Pydantic v2 │ JS 명령어   │
높 │  │ TS 클라이언트│ Optimistic │
음 │  │             │             │
  │  ├─────────────┼─────────────┤
  │  │ Streams     │ 개발자 도구 │
낮 │  │ 업로드      │ 문서화      │
음 │  │ 비동기 작업 │             │
  │  └─────────────┴─────────────┘
  └──────────────────────────────→
        긴급함 →                   긴급도
        (기반)      (기능 추가)
```

---

*이 로드맵은 프로젝트 진행에 따라 업데이트됩니다.*
