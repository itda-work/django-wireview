# Wireview Tutorials

django-wireview 단계별 학습 가이드입니다.

## 학습 경로

### 입문 (Getting Started)
- [01. 시작하기](01-getting-started.md) - 설치, 설정, 첫 컴포넌트

### 초급 (Beginner)
- [02. Counter 컴포넌트](02-counter-component.md) - Component 기초, 이벤트, 상태 관리
- [10. Poll 앱](10-poll-app.md) - skip_render, 조건부 클래스, 실시간 투표
- [11. Rating 앱](11-rating-app.md) - URL 상태, 키보드 이벤트, 별점 평가

### 중급 (Intermediate)
- [03. Todo 앱](03-todo-app.md) - CRUD, 모델 구독, 중첩 컴포넌트
- [12. Live Search](12-live-search.md) - 디바운스, JS 명령어, 검색 자동완성
- [13. Quiz 앱](13-quiz-app.md) - 상태 머신, mutation, 리더보드

### 고급 (Advanced)
- [04. Chat 앱](04-chat-app.md) - Streams API, Presence API, 라이프사이클 훅
- [05. Dashboard](05-dashboard.md) - AsyncResult, 복합 컴포지션
- [14. Notifications](14-notifications.md) - broadcast, JS 명령어 체이닝, 알림 센터
- [15. LiveComponent](15-live-components.md) - 중첩 컴포넌트, 부모-자식 통신

### 심화 (Deep Dive)
- [06. Streams API 심화](06-streams-api.md) - 성능 최적화, DOM ID 전략
- [07. Presence API 심화](07-presence-api.md) - 다중 방, 스케일링
- [08. File Uploads 심화](08-file-uploads.md) - 진행률, 보안
- [09. 테스트 가이드](09-testing-components.md) - mount(), pytest 활용

## 전제 조건

- Python 3.12+
- Django 4.2+ 기본 지식
- HTML/CSS 기본 지식
- 비동기 프로그래밍 기초 (async/await)

## 학습 시간 예상

| 레벨 | 튜토리얼 | 예상 시간 |
|------|---------|----------|
| 입문 | 01. 시작하기 | 30분 |
| 초급 | 02. Counter | 1시간 |
| 초급 | 10. Poll | 1시간 |
| 초급 | 11. Rating | 1시간 |
| 중급 | 03. Todo 앱 | 3시간 |
| 중급 | 12. Live Search | 1.5시간 |
| 중급 | 13. Quiz | 2시간 |
| 고급 | 04. Chat 앱 | 4시간 |
| 고급 | 05. Dashboard | 2시간 |
| 고급 | 14. Notifications | 2시간 |
| 고급 | 15. LiveComponent | 2시간 |
| 심화 | 06-09 | 각 1-2시간 |

**총 학습 시간**: 약 20-25시간

## 예제 코드

튜토리얼이 설명하는 앱의 **동작하는 전체 코드**는 [examples/](../../examples/README.md)에 있습니다.
CI가 매번 돌리므로 문서와 달리 조용히 낡지 않습니다. 각 예제 디렉터리의 README가 그 예제가
가르치는 개념 하나와 대응 튜토리얼을 가리킵니다.

| 예제 | 개념 | 튜토리얼 |
|------|------|----------|
| [todo](../../examples/todo/) | 모델 구독, 중첩 컴포넌트 | [03](03-todo-app.md) |
| [chat](../../examples/chat/) | Streams, Presence | [04](04-chat-app.md) |
| [dashboard](../../examples/dashboard/) | AsyncResult, 컴포지션 | [05](05-dashboard.md) |
| [poll](../../examples/poll/) | skip_render, 조건부 클래스 | [10](10-poll-app.md) |
| [rating](../../examples/rating/) | URL 상태, 키보드 이벤트 | [11](11-rating-app.md) |
| [search](../../examples/search/) | 디바운스, JS 명령 | [12](12-live-search.md) |
| [quiz](../../examples/quiz/) | 상태 머신, 리더보드 | [13](13-quiz-app.md) |
| [notifications](../../examples/notifications/) | 브로드캐스트, JS 체이닝 | [14](14-notifications.md) |
| [livecomp](../../examples/livecomp/) | LiveComponent, 부모-자식 | [15](15-live-components.md) |
| [slots](../../examples/slots/) | 슬롯 합성 | [기능 문서](../features/slots.md) |

## 기능별 학습 가이드

| 기능 | 튜토리얼 |
|------|---------|
| 기본 상태 관리 | 02, 10 |
| 이벤트 핸들링 | 02, 11, 12 |
| 모델 구독 | 03, 10, 13 |
| Streams API | 04, 14 |
| Presence API | 04, 07 |
| JS 명령어 | 12, 14 |
| URL 상태 | 10, 11 |
| 키보드 이벤트 | 11, 12 |
| AsyncResult | 05 |
| LiveComponent | 15 |

## 피드백

문제가 있거나 개선 제안이 있다면 [GitHub Issues](https://github.com/itda-work/django-wireview/issues)에 알려주세요.
