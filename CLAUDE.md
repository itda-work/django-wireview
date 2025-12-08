# django-wireview AI Guide

> AI 에이전트(Claude Code, Codex 등)를 위한 프로젝트 가이드

---

## 언어 및 커뮤니케이션

- **코드 주석**: 영어 (라이브러리 특성상)
- **커밋 메시지**: 영어, Conventional Commits 형식
- **문서**: 한국어/영어 혼용

---

## 프로젝트 컨텍스트

### django-wireview란?

Phoenix LiveView 스타일의 실시간 컴포넌트 라이브러리입니다. Django Channels를 활용하여 서버 사이드 렌더링된 컴포넌트가 WebSocket을 통해 실시간으로 업데이트됩니다.

### 핵심 개념

- **Component**: Pydantic BaseModel 기반의 서버 컴포넌트
- **WireviewMeta**: 렌더링 상태 및 서버-클라이언트 통신 관리
- **HTML Diff**: 변경된 부분만 전송하여 대역폭 절약
- **Morphing**: idiomorph를 사용한 효율적인 DOM 업데이트

### 아키텍처

```
Browser (JavaScript)
├── ServerConnection    # WebSocket 연결 관리
├── WireviewComponent   # 개별 컴포넌트 관리
└── wireview-boost      # DOM morphing, history 관리
        ↕ WebSocket
Django Server
├── WireviewConsumer    # WebSocket Consumer
├── ComponentRepository # 컴포넌트 인스턴스 관리
└── Component           # Pydantic 기반 컴포넌트
```

---

## 기술 스택

### Backend

- **Python**: ≥3.10
- **Django**: 4.2, 5.0, 5.1 지원
- **Django Channels**: WebSocket 통신
- **Pydantic**: v2, 컴포넌트 상태 관리

### Frontend

- **JavaScript**: ES2020, esbuild로 번들링
- **idiomorph**: DOM morphing
- **reconnecting-websocket**: WebSocket 재연결

### 개발 도구

- **ruff**: Python 린팅/포매팅
- **pyright**: Python 타입 체크
- **djlint**: Django 템플릿 린팅
- **pytest**: 테스트 프레임워크
- **pre-commit**: Git hooks

---

## 저장소 구조

```
wireview/
├── __init__.py
├── apps.py
├── component.py        # Component 재export
├── consumer.py         # WebSocket Consumer
├── repository.py       # 컴포넌트 인스턴스 관리
├── auto_broadcast.py   # Django signals 연동
├── event_transpiler.py # 이벤트 문법 파싱
├── js.py               # JS 명령어 빌더
├── schemas.py          # Pydantic 스키마
├── serializer.py       # Django 모델 직렬화
├── settings.py         # 설정 관리
├── testing.py          # 테스트 유틸리티
├── core/               # 핵심 모듈
│   ├── component.py    # Component 베이스 클래스
│   ├── meta.py         # WireviewMeta 클래스
│   └── rendered.py     # Phoenix-style diff
├── features/           # 기능 모듈
│   └── streams.py      # Streams 기능
├── templatetags/
│   └── wireview.py     # {% component %}, {% on %} 등
├── static/wireview/
│   ├── wireview.js     # 메인 클라이언트 모듈
│   ├── wireview-boost.js # 선택적 네비게이션 부스트
│   └── wireview.min.js # 번들링된 결과물
└── urls.py             # WebSocket URL 라우팅

tests/                  # 테스트
├── test_*.py           # wireview 라이브러리 단위 테스트
└── testproj/           # Django 테스트 프로젝트
    └── todo/           # 예제 앱
        ├── tests.py    # Django 앱 통합/E2E 테스트
        └── templates/  # 테스트용 템플릿

docs/                   # 문서
├── ARCHITECTURE.md     # 아키텍처 상세
├── ROADMAP.md          # 로드맵
└── VISION.md           # 비전
```

---

## 개발 워크플로우

### 환경 설정

```bash
# 의존성 설치
make install
# 또는
uv sync --dev

# pre-commit 설치
pre-commit install
```

### 빌드

```bash
# JavaScript 빌드
make build              # esbuild로 번들링
npm run build           # 동일

# JavaScript 워치 모드
make watch-js
npm run watch
```

### 테스트

```bash
# 전체 테스트
make test

# 특정 마커만
pytest -m unit          # 단위 테스트
pytest -m integration   # 통합 테스트
pytest -m "not slow"    # 느린 테스트 제외
pytest -m "not e2e"     # E2E 테스트 제외
```

### 린팅

```bash
# Python
make lint               # ruff check
make check              # pyright

# 타입 체크 (JavaScript)
npm run typecheck       # tsc --noEmit
```

### 테스트 서버

```bash
cd tests
python manage.py runserver
```

---

## 코드 스타일

### Python

- **포매터**: ruff format
- **린터**: ruff
- **라인 길이**: 80자
- **들여쓰기**: 4 spaces
- **따옴표**: double quotes
- **타입 힌트**: 권장

```python
# 예시
async def increment(self, amount: int = 1) -> None:
    """Increment the counter by the given amount."""
    self.count += amount
```

### JavaScript

- **포매터**: 없음 (수동)
- **라인 길이**: 80자
- **들여쓰기**: 2 spaces
- **타입**: JSDoc으로 문서화

```javascript
/**
 * Sends a user event to the server.
 * @param {HTMLElement} element - The triggering element
 * @param {string} name - Event handler name
 * @param {Object} args - Event arguments
 */
send(element, name, args) { ... }
```

### Django 템플릿

- **들여쓰기**: 2 spaces (djlint)
- **프로필**: django

---

## 테스트 전략

### 테스트 구조

| 위치 | 용도 | 예시 |
|------|------|------|
| `tests/test_*.py` | wireview 라이브러리 단위 테스트 | test_js.py, test_streams.py |
| `tests/testproj/{app}/tests.py` | Django 앱 통합/E2E 테스트 | todo/tests.py |

**원칙**:
- wireview 라이브러리 기능 테스트 → `tests/test_*.py`
- Django 앱 특정 테스트 → 해당 앱의 `tests.py` 또는 `tests/` 폴더

### 마커

| 마커 | 설명 |
|------|------|
| `@pytest.mark.unit` | 단위 테스트 |
| `@pytest.mark.integration` | 통합 테스트 |
| `@pytest.mark.slow` | 느린 테스트 |
| `@pytest.mark.e2e` | E2E 테스트 (브라우저 필요) |

### 커버리지

```bash
pytest --cov=wireview --cov-report=html
```

---

## 핵심 API

### Component 클래스

```python
from wireview.component import Component

class Counter(Component):
    _template_name = "counter.html"

    count: int = 0

    async def increment(self, amount: int = 1):
        self.count += amount

    async def joined(self):
        """컴포넌트 마운트 시 호출"""
        pass

    async def mutation(self, channel, action, instance):
        """ORM 변경 알림"""
        pass
```

### temporary_assigns (메모리 최적화)

대용량 리스트를 렌더링 후 메모리에서 해제하여 서버 메모리를 절약합니다.
**상세 문서**: [docs/features/temporary-assigns.md](./docs/features/temporary-assigns.md)

```python
class MessageList(Component):
    _template_name = "messages/list.html"
    _temporary_assigns = {"messages"}  # 렌더 후 초기화할 필드

    messages: list[Message] = []  # 기본값 필수!
    total_count: int = 0  # 이 필드는 유지됨

    async def joined(self):
        self.messages = await Message.objects.all()[:100]
        self.total_count = await Message.objects.acount()
        # 렌더링 후 self.messages = [] 자동 초기화
        # self.total_count는 100 유지
```

**초기화 규칙**:
- **시점**: 매 렌더링 완료 직후 (diff 전송 후)
- **값**: Pydantic 필드의 기본값 (`= []`, `= {}`, `= 0` 등)
- **조건**: 기본값이 없는 필드는 초기화되지 않음

### 템플릿 태그

```html
{% load wireview %}

<!-- 컴포넌트 렌더링 -->
{% component 'Counter' count=10 %}

<!-- 이벤트 바인딩 -->
<button {% on 'click' 'increment' amount=1 %}>+1</button>

<!-- 수정자 사용 -->
<button {% on 'click.prevent.debounce.300' 'search' %}>Search</button>
```

### Streams API

대량 리스트를 메모리 효율적으로 처리하는 Phoenix LiveView 스타일 Streams:

```python
class ItemList(Component):
    _template_name = "myapp/item_list.html"
    items: list[Item] = []

    async def joined(self):
        self.items = list(await Item.objects.all()[:100])

    async def add_item(self, name: str):
        item = await Item.objects.acreate(name=name)
        await self.stream_insert("items", item, at=0)  # prepend

    async def remove_item(self, item_id: int):
        await self.stream_delete("items", item_id)

    async def refresh_all(self):
        items = await Item.objects.all()[:100]
        await self.stream("items", items)  # 전체 교체
```

```html
<!-- item_list.html -->
<ul wire-stream="items">
  {% for item in items %}
    {% include "myapp/item_list_item.html" %}
  {% endfor %}
</ul>

<!-- item_list_item.html -->
<li id="items-{{ item.pk }}">{{ item.name }}</li>
```

**Stream 메서드**:
- `stream(name, items)`: 리스트 전체 교체 (reset)
- `stream_insert(name, item, at=)`: 아이템 삽입 (-1=append, 0=prepend)
- `stream_delete(name, dom_id)`: 아이템 삭제

### JavaScript API

```javascript
// 이벤트 전송
window.wireview.send(element, 'increment', { amount: 1 });

// 디바운스
window.wireview.debounce(300)(() => { ... });
```

---

## 설정

```python
# settings.py
WIREVIEW = {
    "TRANSPILER_CACHE_SIZE": 1024,
    "USE_HTML_DIFF": True,      # HTML diff 사용
    "USE_HMIN": False,          # django-hmin 사용
    "BOOST_PAGES": False,       # 클라이언트 사이드 네비게이션
}
```

---

## 주의사항

### 컴포넌트 설계

1. **상태 직렬화**: 컴포넌트 상태는 JSON 직렬화 가능해야 함
2. **async 메서드**: 이벤트 핸들러는 async로 정의
3. **ID 고유성**: 컴포넌트 ID는 페이지 내에서 고유해야 함

### WebSocket

1. **연결 끊김**: 연결이 끊기면 컴포넌트가 자동으로 재연결됨
2. **상태 복구**: 재연결 시 마지막 상태에서 복구

### 성능

1. **skip_render()**: 불필요한 렌더링 방지
2. **_exclude_fields**: 직렬화에서 제외할 필드 지정
3. **HTML Diff**: 변경된 부분만 전송

---

## 관련 문서

- [README.md](./README.md) - 사용 가이드
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) - 아키텍처 상세
- [docs/ROADMAP.md](./docs/ROADMAP.md) - 개발 로드맵
- [CHANGELOG.md](./CHANGELOG.md) - 변경 이력

---

*이 문서는 AI 에이전트가 프로젝트를 이해하고 효과적으로 기여할 수 있도록 작성되었습니다.*

- gh cli를 활용해서 깃허브 이슈 등을 관리
