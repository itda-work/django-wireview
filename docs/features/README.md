# 기능 레퍼런스

django-wireview의 주요 기능에 대한 상세 문서입니다.

## 성능 최적화

| 기능 | 설명 | 문서 |
|------|------|------|
| **temporary_assigns** | 렌더링 후 필드 자동 초기화로 메모리 절약 | [문서](./temporary-assigns.md) |
| **skip_render** | 불필요한 렌더링 방지 | 준비 중 |
| **HTML Diff** | 변경된 dynamic 파트만 전송, 상태 압축 서명 | [문서](./html-diff.md) |

## 실시간 기능

| 기능 | 설명 | 문서 |
|------|------|------|
| **Streams** | 대용량 리스트 실시간 조작 | [튜토리얼](../tutorials/06-streams-api.md) |
| **Presence** | 사용자 온라인 상태 추적 | [튜토리얼](../tutorials/07-presence-api.md) |
| **Auto Broadcast** | Django ORM 변경 자동 알림 | 준비 중 |

## 비동기 처리

| 기능 | 설명 | 문서 |
|------|------|------|
| **Async Operations** | `assign_async`, `start_async`/`cancel_async`, AsyncResult 로딩 상태 | [문서](./async-operations.md) |

## 파일 업로드

| 기능 | 설명 | 문서 |
|------|------|------|
| **File Uploads** | 청크 업로드, 진행률 표시 | [튜토리얼](../tutorials/08-file-uploads.md) |
| **Chunked Uploads (서버)** | 무상태 청크 엔드포인트, 다중 워커, 정리와 서명 키 | [문서](./chunked-uploads.md) |
| **External Uploads** | S3/GCS 직접 업로드 | [문서](./external-uploads.md) |

## 컴포넌트 시스템

| 기능 | 설명 | 문서 |
|------|------|------|
| **Slots** | 컴포넌트 콘텐츠 합성 (Phoenix 스타일) | [문서](./slots.md) |
| **Function Components** | 상태 없는 재사용 가능 컴포넌트 | [문서](./function-components.md) |
| **LiveComponent** | 독립 상태를 가진 중첩 컴포넌트 | [문서](./live-component.md) |
| **Lifecycle Hooks** | `on_mount` 훅, `attach_hook`으로 라이프사이클 가로채기 | [문서](./lifecycle-hooks.md) |
| **live_session** | 페이지 단위 인증 경계. 경계를 넘는 이동은 전체 로드 | [문서](./live-session.md) |
| **세션 읽기** | `self.session`으로 Django 세션 읽기 (읽기 전용) | [문서](./session.md) |

## 폼과 UI 피드백

| 기능 | 설명 | 문서 |
|------|------|------|
| **Optimistic UI** | 로딩 클래스, `wire-disabled-with` | [문서](./optimistic-ui.md) |
| **Form Feedback** | `wire-feedback-for`로 검증 오류 표시 시점 제어 | [문서](./form-feedback.md) |

## JavaScript 연동

| 기능 | 설명 | 문서 |
|------|------|------|
| **JS Commands** | 클라이언트 측 DOM 조작 | [구현 문서](../implementation/js-commands.md) |
| **JavaScript Hooks** | 서드파티 JS 라이브러리 통합 | [문서](./hooks.md) |

## 개발자 도구

| 기능 | 설명 | 문서 |
|------|------|------|
| **Type Stubs** | 컴포넌트 `.pyi` 자동 생성 (`wireview_stubs`) | [문서](./type-stubs.md) |
| **Telemetry** | 이벤트·렌더·diff·브로드캐스트 계측 시그널 (옵트인) | [문서](./telemetry.md) |
| **System Checks** | 조용히 실패하는 함정을 `manage.py check`가 잡는다 | [문서](./checks.md) |
| **Agent Skill** | 앱 개발자용 에이전트 스킬 배포 (`wireview_agent_setup`) | [문서](./agent-skill.md) |

---

## 문서 작성 가이드

새 기능 문서 작성 시 다음 구조를 따릅니다:

1. **개요** - 문제와 해결책
2. **작동 방식** - 내부 동작 설명
3. **사용법** - 코드 예시
4. **주의사항** - 알아야 할 제한사항
5. **실제 사용 예시** - 실무 예제
6. **관련 기능** - 연관 문서 링크
