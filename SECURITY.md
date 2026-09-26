# 보안 정책

## 취약점 제보

**공개 이슈로 올리지 마세요.** GitHub의 비공개 제보 기능을 쓴다:
<https://github.com/itda-work/django-wireview/security/advisories/new>

재현 절차, 영향받는 버전, 가능하면 영향 범위(누가 무엇을 할 수 있게 되는가)를 적어 주세요. 확인 결과는
제보 스레드로 알리고, 수정이 배포되면 제보자를 CHANGELOG와 보안 공지에 밝힌다(원하지 않으면 빼고).

## 지원 버전

| 버전 | 보안 수정 |
|------|-----------|
| 최신 릴리스 | ✅ |
| 그 이전 0.x | ❌ — 최신 릴리스로 올린다 |

1.0부터는 최신 마이너 릴리스에 보안 수정을 낸다. 범위는 [docs/COMPATIBILITY.md](./docs/COMPATIBILITY.md)가 정한다.

## wireview가 지키는 경계

제보가 이 경계 중 하나를 넘는지 보면 판단이 빠르다. 상세는 각 문서에 있다.

| 경계 | 무엇을 막나 | 문서 |
|------|-------------|------|
| 서명된 상태(`data-state`) | 클라이언트가 컴포넌트 상태를 고치거나, 한 클래스의 상태를 다른 클래스로, 한 페이지 경계의 상태를 다른 경계로 내미는 것. 만료와 로그아웃 뒤의 재사용 | [html-diff](./docs/features/html-diff.md), [live-session](./docs/features/live-session.md) |
| 이벤트 노출 규칙 | 클라이언트가 사용자 핸들러가 아닌 것(밑줄 메서드, 프레임워크·Pydantic 메서드, 중첩 클래스)을 부르는 것 | [CLAUDE.md](./CLAUDE.md)의 「클라이언트가 호출할 수 있는 메서드」 |
| 페이지 경계(`live_session`) | 인가가 필요한 컴포넌트가 경계 밖 페이지나 다른 로그인으로 마운트되는 것 | [live-session](./docs/features/live-session.md) |
| WebSocket Origin | 다른 사이트의 페이지가 사용자의 쿠키로 소켓을 여는 것(CSWSH) | [DEPLOYMENT](./docs/DEPLOYMENT.md#websocket의-origin) |
| 업로드 토큰 | 서명 없이, 크기·확장자 제한을 넘어, 남의 연결로 청크를 쓰는 것 | [chunked-uploads](./docs/features/chunked-uploads.md) |
| 마크업에 스크립트 없음 | `'unsafe-inline'` 없는 CSP에서의 동작 | [csp](./docs/features/csp.md) |

## 경계 밖의 것

- **핸들러 안의 인가.** 클라이언트가 보내는 인자는 신뢰할 수 없다. 남의 객체 id로 부르는 경우를 막는 것은
  핸들러의 몫이다.
- **서명 키 관리.** `SECRET_KEY`(또는 `WIREVIEW["SIGNING_KEY"]`)가 새면 모든 서명 경계가 무너진다.
- **서명 쿠키 세션 백엔드의 로그아웃.** 서버에 저장하지 않으므로 로그아웃을 폐기할 수 없다
  ([live-session](./docs/features/live-session.md)).
