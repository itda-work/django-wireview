# 호환성 정책

1.0부터 django-wireview는 여기서 **공개**라고 적은 것을 마이너·패치 릴리스에서 깨지 않는다. 공개가 아닌 것은
예고 없이 바뀔 수 있다. 1.0 전(0.x)에는 마이너 릴리스도 호환을 깰 수 있다.

## 무엇이 공개인가

### Python API: `wireview`의 `__all__`

공개 Python API는 **최상위 패키지 `wireview`가 `__all__`로 내보내는 이름뿐**이다(#98).

```python
from wireview import Component, LiveComponent, JS, mount
```

`wireview.core.*`, `wireview.features.*`, `wireview.consumer`, `wireview.repository` 같은 하위 모듈은
모두 **내부**다. 그 안의 이름을 직접 import하면 마이너 릴리스에서 경로가 바뀌어도 알림이 없다. 필요한
이름이 `__all__`에 없으면 이슈로 요청한다.

`telemetry`는 모듈 자체가 공개 이름이다(`from wireview import telemetry`). 시그널을 모아 둔 네임스페이스이고,
그 모듈의 `__all__`이 공개 범위다.

`Component`와 `LiveComponent`는 이름뿐 아니라 **멤버**도 약속한다. 무엇이 공개인지는
[Component API](./features/component-api.md)가 정본이다. 밑줄이 없어도 거기 없는 멤버는 내부다.

`WireviewMeta`는 **타입 주석용 이름으로만** 공개다(`wire: WireviewMeta`). 생성자와 멤버는 아래 `self.wire`
행이 정한 넷만 공개다.

`tests/test_public_api.py`가 이 경계를 지킨다. `__all__`, 지연 로딩 표, 타입 검사용 import가 서로 같은지 보고,
사용자용 문서(`README.md`, `docs/features/`, `docs/tutorials/`, 앱 개발자용 스킬)와 `examples/`가
`from wireview import ...`로만 import하며 그 이름이 모두 공개인지 본다.

### 통합 지점

이름을 import하지 않고 경로나 문자열로 가리키는 것도 공개다.

| 무엇 | 형태 |
|------|------|
| Django 앱 | `INSTALLED_APPS`의 `"wireview"` |
| URL | `include("wireview.urls")`, `wireview.urls.websocket_urlpatterns`, 그리고 둘이 여는 경로 `/__wireview__`(WebSocket)와 `/__wireview_upload__/…`(업로드). 프록시·CSP `connect-src`·방화벽이 이 경로를 적으므로 경로도 약속이다. **루트에 마운트해야 한다** — 클라이언트와 업로드 토큰이 이 경로를 루트에서 찾으므로, 접두사 아래(`path("app/", include(...))`)나 하위 경로 배포(`SCRIPT_NAME`)에서는 동작하지 않는다 |
| 템플릿 태그 | `{% load wireview %}`와 그 태그들 |
| 설정 | `settings.WIREVIEW`의 키 (`wireview/settings.py`의 `DEFAULT`) |
| 관리 명령 | `wireview_stubs`, `wireview_lsp`, `wireview_agent_setup`, `wireview_upload_gc`와 문서화된 옵션. `wireview_lsp`의 출력 JSON은 그 안의 `version` 필드로 따로 관리한다 — 모양을 바꾸면 `version`을 올린다 |
| 시스템 체크 id | `wireview.W001`~. 없앤 번호는 다시 쓰지 않는다 |
| 컴포넌트 클래스 설정 | `class Meta:`의 키(`ComponentOptions`의 필드)와 `get_subscriptions()` |
| 템플릿 컨텍스트 | 컴포넌트 템플릿의 `this`, 슬롯의 `let` 이름 |
| 훅 파일 위치 | 앱의 `static/<app_label>/hooks/*.js` ([hooks](./features/hooks.md)) |
| 모델 채널 이름 | `AUTO_BROADCAST`가 알리는 채널: `<app_label>.<model>`, `<app_label>.<model>.<pk>`, 가리키는 행의 `<app_label>.<model>.<pk>.<related_name>`, m2m은 양쪽 행의 `<app_label>.<model>.<pk>.<field>`(어느 쪽에서 바꿨든 같다). 밑줄은 하이픈이 된다 |
| `self.wire` | `params`, `redirect_to`, `replace_to`, `push_to`만([navigation](./features/navigation.md)). 나머지는 프레임워크 내부이고, 같은 일은 `Component`의 메서드(`put_flash`, `push_js`, `push_title`, `defer` 등)로 한다 |
| 클라이언트 | `window.wireview`의 문서화된 멤버, `docs/features/`에 문서화된 `wire-*` DOM 속성·`wireview-*` CSS 클래스·`wireview:*` DOM 이벤트, 훅 객체의 문서화된 멤버([hooks](./features/hooks.md)). 접두사가 맞는다고 공개가 아니다 — 아래 "내부" 참고 |
| 테스트 도구 | `mount()`가 돌려주는 `MountedComponent`의 문서화된 멤버([testing](./features/testing.md)). 그 `view.wire`는 컴포넌트의 `self.wire`와 같은 범위만 공개다. `sent_messages`·`stream_ops`의 항목과 `render_diff()`의 diff는 와이어 메시지라 **모양은 공개가 아니다** — `render_diff()`는 `None`인지만 약속한다 |

### 내부 (공개처럼 보이지만 아닌 것)

| 무엇 | 왜 |
|------|----|
| 템플릿 태그가 출력하는 마크업 | 계약은 태그다. `{% on %}`이 내는 `wire-on-*` 속성과 그 JSON 값, 업로드 태그가 내는 `wire-upload`·`wire-upload-select`·`wire-upload-drop`·`wire-preview`(값 `name:ref`), `{% tag_header %}`가 내는 `wireview-component`·`wireview-live` 표식과 `data-name`·`data-state`·`data-is-live`·`data-parent`, `{% wireview_header %}`의 `<meta>` |
| 훅 객체의 `__` 멤버 | `__hookId`, `__manager` 등 |
| `window.wireview.debug`의 반환값 | 개발 도구다. 함수 이름은 남기지만 돌려주는 객체의 모양은 약속하지 않는다 |
| static의 번들 밖 파일 | `wireview.min.js`만 페이지가 싣는다. `wireview.js`, `*.mjs`, `types.d.ts`, `.map`은 빌드 재료다 |
| 하위 모듈 | 위 Python API 절 |

### 와이어 프로토콜

서버와 브라우저 사이의 메시지 형태는 공개가 아니다. 대신 **번들과 서버가 버전이 달라도 페이지가 깨지지 않는다**는
것을 약속한다. 규칙은 [wire-protocol](./implementation/wire-protocol.md) §7이다.

## 없애는 절차

공개 API를 없앨 때는 한 메이저 버전 동안 동작하게 두고 경고를 낸다.

1. 옛 이름은 계속 동작하고, 쓰일 때 `wireview.WireviewDeprecationWarning`을 낸다. 경고는 새 이름과 제거될
   버전을 말한다(`wireview/deprecation.py`의 `warn_deprecated`).
2. 다음 메이저 릴리스에서 지운다.
3. 두 단계 모두 `CHANGELOG.md`에 적는다.

`WireviewDeprecationWarning`은 `DeprecationWarning`의 하위 클래스다. 테스트에서 경고를 오류로 바꾸려면:

```python
import warnings

from wireview import WireviewDeprecationWarning

warnings.simplefilter("error", WireviewDeprecationWarning)
```

현재 진행 중인 것:

| 옛 것 | 새 것 | 제거 |
|-------|-------|------|
| `wireview.component` 모듈 | `from wireview import Component` | 2.0 |
| 테스트의 `view.wire.broadcasts` | `view.broadcasts` | 2.0 |
| 테스트의 `view.wire.presence_broadcasts` | `view.presence_broadcasts` | 2.0 |
| DOM 이벤트 `upload:added`·`progress`·`complete`·`error`·`cancel` | `wireview:upload-added` 등 (둘 다 나간다) | 2.0 |

## 지원 범위

**Django가 보안 지원하는 Django 버전**과, 그 버전들이 지원하는 Python 중 **3.12 이상**을 지원한다. 지금은
Django 5.2 LTS·6.0·6.1, Python 3.12·3.13·3.14다.

- Django가 한 버전의 지원을 끝내면 그다음 **마이너** 릴리스에서 그 버전을 뺀다. 메이저를 올리지 않는다 —
  Django가 이미 끝낸 버전을 붙잡는 것은 사용자를 지키는 일이 아니다.
- 새 Django·Python 버전은 매트릭스를 통과하면 패치 릴리스로 더한다.
- 정본은 `pyproject.toml`(의존성·classifier)과 `.github/workflows/ci.yml`의 매트릭스다. 같은 격자를 로컬에서
  `make test-matrix`로 돈다(CI는 수동으로만 돈다).
