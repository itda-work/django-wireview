# Wireview Tutorials

django-wireview 단계별 학습 가이드입니다.

## 학습 경로

### 입문 (Getting Started)
- [시작하기](01-getting-started.md) - 설치, 설정, 첫 컴포넌트

### 초급 (Beginner)
- [Counter 컴포넌트](02-counter-component.md) - Component 기초, 이벤트, 상태 관리
- [Poll 앱](10-poll-app.md) - skip_render, 조건부 클래스, 실시간 투표
- [Rating 앱](11-rating-app.md) - URL 상태, 키보드 이벤트, 별점 평가

### 중급 (Intermediate)
- [Todo 앱](03-todo-app.md) - CRUD, 모델 구독, 중첩 컴포넌트
- [Live Search](12-live-search.md) - 디바운스, JS 명령, 검색 자동완성
- [Quiz 앱](13-quiz-app.md) - 상태 머신, mutation, 리더보드

### 고급 (Advanced)
- [Chat 앱](04-chat-app.md) - Streams API, Presence API, 라이프사이클 훅
- [Dashboard](05-dashboard.md) - AsyncResult, 복합 컴포지션
- [Notifications](14-notifications.md) - 사용자별 채널, 알림과 토스트, broadcast, JS 명령 체이닝
- [LiveComponent](15-live-components.md) - 중첩 컴포넌트, 부모-자식 통신

### 심화 (Deep Dive)
- [Streams API 심화](06-streams-api.md) - 성능 최적화, DOM ID 전략
- [Presence API 심화](07-presence-api.md) - 다중 방, 스케일링
- [File Uploads 심화](08-file-uploads.md) - 진행률, 보안
- [테스트 가이드](09-testing-components.md) - mount(), pytest 활용

## 전제 조건

- Python 3.12+
- Django 5.2+ 기본 지식
- HTML/CSS 기본 지식
- 비동기 프로그래밍 기초 (async/await)

## 학습 시간 예상

| 레벨 | 튜토리얼 | 예상 시간 |
|------|---------|----------|
| 입문 | 시작하기 | 30분 |
| 초급 | Counter 컴포넌트 | 1시간 |
| 초급 | Poll 앱 | 1시간 |
| 초급 | Rating 앱 | 1시간 |
| 중급 | Todo 앱 | 3시간 |
| 중급 | Live Search | 1.5시간 |
| 중급 | Quiz 앱 | 2시간 |
| 고급 | Chat 앱 | 4시간 |
| 고급 | Dashboard | 2시간 |
| 고급 | Notifications | 2시간 |
| 고급 | LiveComponent | 2시간 |
| 심화 | Streams API 심화 | 1-2시간 |
| 심화 | Presence API 심화 | 1-2시간 |
| 심화 | File Uploads 심화 | 1-2시간 |
| 심화 | 테스트 가이드 | 1-2시간 |

**총 학습 시간**: 약 24-28시간

## 예제 코드

튜토리얼이 설명하는 앱의 **동작하는 전체 코드**는 [examples/](../../examples/README.md)에 있습니다.
`make test`가 함께 돌리고 릴리스 게이트(CI)가 태그마다 다시 돌리므로 문서와 달리 조용히 낡지 않습니다.
각 예제 디렉터리의 README가 그 예제가 가르치는 개념 하나와 대응 튜토리얼을 가리킵니다.

| 예제 | 개념 | 튜토리얼 |
|------|------|----------|
| [todo](../../examples/todo/) | 모델 구독, 중첩 컴포넌트 | [Todo 앱](03-todo-app.md) |
| [chat](../../examples/chat/) | Streams, Presence | [Chat 앱](04-chat-app.md) |
| [dashboard](../../examples/dashboard/) | AsyncResult, 컴포지션 | [Dashboard](05-dashboard.md) |
| [poll](../../examples/poll/) | skip_render, 조건부 클래스 | [Poll 앱](10-poll-app.md) |
| [rating](../../examples/rating/) | URL 상태, 키보드 이벤트 | [Rating 앱](11-rating-app.md) |
| [search](../../examples/search/) | 디바운스, JS 명령 | [Live Search](12-live-search.md) |
| [quiz](../../examples/quiz/) | 상태 머신, 리더보드 | [Quiz 앱](13-quiz-app.md) |
| [notifications](../../examples/notifications/) | 사용자별 알림과 토스트, 브로드캐스트 | [Notifications](14-notifications.md) |
| [livecomp](../../examples/livecomp/) | LiveComponent, 부모-자식 | [LiveComponent](15-live-components.md) |
| [slots](../../examples/slots/) | 슬롯 합성 | [기능 문서](../features/slots.md) |
| [hooks](../../examples/hooks/) | 브라우저만 할 수 있는 일을 컴포넌트에 붙인다 | [기능 문서](../features/hooks.md) |

## 기능별 학습 가이드

| 기능 | 튜토리얼 |
|------|---------|
| 기본 상태 관리 | 02, 10 |
| 이벤트 핸들링 | 02, 11, 12 |
| 모델 구독 | 03, 10, 13 |
| Streams API | 04, 06, 14 |
| Presence API | 04, 07 |
| JS 명령 | 12, 14 |
| URL 상태 | 10, 11 |
| 키보드 이벤트 | 11, 12 |
| AsyncResult | 05 |
| LiveComponent | 15 |

## 피드백

문제가 있거나 개선 제안이 있다면 [GitHub Issues](https://github.com/itda-work/django-wireview/issues)에 알려주세요.
