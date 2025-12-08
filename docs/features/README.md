# 기능 레퍼런스

django-wireview의 주요 기능에 대한 상세 문서입니다.

## 성능 최적화

| 기능 | 설명 | 문서 |
|------|------|------|
| **temporary_assigns** | 렌더링 후 필드 자동 초기화로 메모리 절약 | [문서](./temporary-assigns.md) |
| **skip_render** | 불필요한 렌더링 방지 | 준비 중 |
| **HTML Diff** | 변경된 부분만 전송 | 준비 중 |

## 실시간 기능

| 기능 | 설명 | 문서 |
|------|------|------|
| **Streams** | 대용량 리스트 실시간 조작 | [튜토리얼](../tutorials/06-streams-api.md) |
| **Presence** | 사용자 온라인 상태 추적 | [튜토리얼](../tutorials/07-presence-api.md) |
| **Auto Broadcast** | Django ORM 변경 자동 알림 | 준비 중 |

## 파일 업로드

| 기능 | 설명 | 문서 |
|------|------|------|
| **File Uploads** | 청크 업로드, 진행률 표시 | [튜토리얼](../tutorials/08-file-uploads.md) |

## JavaScript 연동

| 기능 | 설명 | 문서 |
|------|------|------|
| **JS Commands** | 클라이언트 측 DOM 조작 | [구현 문서](../implementation/js-commands.md) |
| **JavaScript Hooks** | 서드파티 JS 라이브러리 통합 | 준비 중 (GAP-001) |

---

## 문서 작성 가이드

새 기능 문서 작성 시 다음 구조를 따릅니다:

1. **개요** - 문제와 해결책
2. **작동 방식** - 내부 동작 설명
3. **사용법** - 코드 예시
4. **주의사항** - 알아야 할 제한사항
5. **실제 사용 예시** - 실무 예제
6. **관련 기능** - 연관 문서 링크
