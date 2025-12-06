# django-wireview 프로젝트 비전

> **Phoenix LiveView의 개발 경험을 Django 생태계에 제공**

---

## 1. 프로젝트 목표

### 1.1 핵심 비전

```
"Django 개발자가 JavaScript 없이 실시간 인터랙티브 UI를 구축할 수 있게 한다"
```

django-wireview는 Phoenix LiveView의 핵심 철학을 Django에 가져옵니다:

- **서버 중심 상태 관리**: 클라이언트-서버 상태 동기화 문제 제거
- **HTML over WebSocket**: SPA 복잡성 없이 실시간 업데이트
- **점진적 향상**: 기존 Django 프로젝트에 쉽게 통합
- **Django 생태계 활용**: ORM, Forms, Auth 등 기존 도구 그대로 사용

### 1.2 목표 사용자

| 사용자 | 해결하는 문제 |
|--------|--------------|
| **Django 개발자** | React/Vue 없이 실시간 UI 구축 |
| **풀스택 개발자** | 프론트엔드/백엔드 코드 분리 최소화 |
| **스타트업** | 빠른 MVP 개발, 적은 기술 스택 |
| **기존 Django 프로젝트** | 점진적 실시간 기능 추가 |

### 1.3 차별점

| vs | django-wireview 차별점 |
|----|----------------------|
| **React/Vue** | JavaScript 코드 작성 불필요, 서버 상태 단일화 |
| **HTMX** | 양방향 WebSocket, Server Push 가능 |
| **django-unicorn** | WebSocket 기반 (AJAX 아님), 상태 유지 |
| **Django-LiveView** | 컴포넌트 모델, DOM Diffing, 상태 캡슐화 |

---

## 2. 설계 원칙

### 2.1 핵심 원칙

```python
# 1. 명시적 > 암시적
class Counter(Component):
    count: int = 0  # 상태가 명확히 보임

    def increment(self):
        self.count += 1  # 의도가 명확함

# 2. Django 관례 존중
_template_name = "components/counter.html"  # Django 템플릿 시스템 사용
_subscriptions = {"myapp.item"}  # Django 앱 네이밍 컨벤션

# 3. 타입 안전성
count: int = 0  # Pydantic 검증
user: User      # Django 모델 타입 힌트

# 4. 최소 놀라움
def increment(self):
    self.count += 1
    # 자동 재렌더링 - 직관적 동작
```

### 2.2 아키텍처 원칙

1. **단일 진실 공급원 (Single Source of Truth)**
   - 상태는 서버에만 존재
   - 클라이언트는 뷰 레이어만 담당

2. **컴포넌트 격리**
   - 각 컴포넌트는 독립적 상태
   - 부모-자식 통신은 명시적

3. **최소 페이로드**
   - HTML Diff로 변경분만 전송
   - morphdom으로 효율적 DOM 업데이트

4. **우아한 성능 저하**
   - WebSocket 연결 끊김 시 graceful handling
   - 자동 재연결 및 상태 복구

---

## 3. 목표 개발 경험 (DX)

### 3.1 Phoenix LiveView 수준의 DX

**현재 (v5.3.0)**:
```python
class Counter(Component):
    count: int = 0

    def increment(self):
        self.count += 1
```

**목표 (v6.0)**:
```python
class Counter(Component):
    count: int = 0
    items: list[Item] = []

    async def increment(self):
        self.count += 1
        # JS 명령어로 즉시 피드백
        await self.push_event("flash", {"message": "Updated!"})

    async def load_items(self):
        # 비동기 데이터 로딩
        self.items = await self.stream(Item.objects.all())
```

### 3.2 목표 템플릿 DX

**현재**:
```html
<button {% on "click" "increment" %}>+</button>
```

**목표**:
```html
<!-- JS 명령어 지원 -->
<button {% on "click" "increment" %}
        {% js_loading "opacity-50 cursor-wait" %}>
  +
</button>

<!-- Optimistic UI -->
<form {% on "submit" "save" %}
      {% js_loading "submitting" %}>
  ...
</form>
```

### 3.3 목표 기능 목록

| 기능 | 현재 | 목표 | 우선순위 |
|------|:----:|:----:|:--------:|
| Pydantic v2 | ❌ | ✅ | P0 |
| TypeScript 클라이언트 | ❌ | ✅ | P1 |
| JS 명령어 | ❌ | ✅ | P1 |
| Optimistic UI | ❌ | ✅ | P1 |
| Streams | ❌ | ✅ | P2 |
| 파일 업로드 | ❌ | ✅ | P2 |
| 개발자 도구 | ❌ | ✅ | P3 |
| Latency 시뮬레이터 | ❌ | ✅ | P3 |

---

## 4. 성공 지표

### 4.1 기술적 지표

- [ ] Pydantic v2 완전 호환
- [ ] Django 5.x 공식 지원
- [ ] Python 3.11+ 최적화
- [ ] 테스트 커버리지 80%+
- [ ] 타입 힌트 100%

### 4.2 사용성 지표

- [ ] 5분 내 첫 컴포넌트 실행 가능
- [ ] 공식 문서 완비
- [ ] 예제 프로젝트 3개 이상
- [ ] IDE 자동완성 완벽 지원

### 4.3 성능 지표

- [ ] 초당 10,000+ 이벤트 처리 (단일 서버)
- [ ] 평균 응답 시간 < 50ms
- [ ] HTML Diff로 평균 70% 대역폭 절감

---

## 5. 비전 로드맵

```
2024 Q4: Foundation (현대화)
├── Pydantic v2 마이그레이션
├── Django 5.x 호환성
└── TypeScript 클라이언트

2025 Q1: Core Features
├── JS 명령어 시스템
├── Optimistic UI
└── 개선된 HTML Diff

2025 Q2: Advanced Features
├── Streams (대량 데이터)
├── 파일 업로드
└── 비동기 작업 (assign_async)

2025 Q3: Polish
├── 개발자 도구
├── 성능 최적화
└── 문서화 완성
```

---

## 6. 철학적 입장

### 6.1 무엇을 하지 않는가

- ❌ React/Vue를 대체하려 하지 않음
- ❌ 모든 웹 애플리케이션의 솔루션이 되려 하지 않음
- ❌ JavaScript를 완전히 제거하려 하지 않음
- ❌ Phoenix LiveView를 100% 복제하려 하지 않음

### 6.2 무엇을 하는가

- ✅ Django 개발자의 생산성 극대화
- ✅ 적절한 사용 사례에서 최고의 DX 제공
- ✅ Django 생태계와의 깊은 통합
- ✅ Python 개발자에게 친숙한 패턴 제공

### 6.3 적합한 사용 사례

```
적합:
├── 관리자 대시보드
├── 실시간 알림/채팅
├── 폼 검증 및 위저드
├── 데이터 테이블 (필터, 정렬, 페이지네이션)
├── 실시간 검색
└── 협업 기능

부적합:
├── 복잡한 클라이언트 사이드 애니메이션
├── 오프라인 우선 애플리케이션
├── 게임 등 고빈도 업데이트
└── 네이티브 앱 수준의 UX 요구사항
```

---

*이 문서는 django-wireview 프로젝트의 방향성을 정의합니다.*
*모든 기술적 결정은 이 비전에 부합해야 합니다.*
