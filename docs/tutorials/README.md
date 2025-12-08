# Wireview Tutorials

django-wireview 단계별 학습 가이드입니다.

## 학습 경로

### 입문 (Getting Started)
- [01. 시작하기](01-getting-started.md) - 설치, 설정, 첫 컴포넌트

### 초급 (Beginner)
- [02. Counter 컴포넌트](02-counter-component.md) - Component 기초, 이벤트, 상태 관리

### 초~중급 (Beginner to Intermediate)
- [03. Todo 앱](03-todo-app.md) - CRUD, 모델 구독, 중첩 컴포넌트

### 중~고급 (Intermediate to Advanced)
- [04. Chat 앱](04-chat-app.md) - Streams API, Presence API, 라이프사이클 훅

### 고급 (Advanced)
- [05. Dashboard](05-dashboard.md) - AsyncResult, 복합 컴포지션

### 심화 (Deep Dive)
- [06. Streams API 심화](06-streams-api.md) - 성능 최적화, DOM ID 전략
- [07. Presence API 심화](07-presence-api.md) - 다중 방, 스케일링
- [08. File Uploads 심화](08-file-uploads.md) - 진행률, 보안
- [09. 테스트 가이드](09-testing-components.md) - mount(), pytest 활용

## 전제 조건

- Python 3.10+
- Django 4.2+ 기본 지식
- HTML/CSS 기본 지식
- 비동기 프로그래밍 기초 (async/await)

## 학습 시간 예상

| 레벨 | 튜토리얼 | 예상 시간 |
|------|---------|----------|
| 입문 | 01. 시작하기 | 30분 |
| 초급 | 02. Counter | 1시간 |
| 초~중급 | 03. Todo 앱 | 3시간 |
| 중~고급 | 04. Chat 앱 | 4시간 |
| 고급 | 05. Dashboard | 2시간 |
| 심화 | 06-09 | 각 1-2시간 |

**총 학습 시간**: 약 15-20시간

## 예제 코드

모든 튜토리얼의 완성된 예제 코드는 `tests/testproj/` 디렉토리에서 확인할 수 있습니다:

- `tests/testproj/todo/` - Todo 앱
- `tests/testproj/chat/` - Chat 앱
- `tests/testproj/dashboard/` - Dashboard

## 피드백

문제가 있거나 개선 제안이 있다면 [GitHub Issues](https://github.com/itda-work/django-wireview/issues)에 알려주세요.
