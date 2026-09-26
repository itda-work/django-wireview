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

`tests/test_public_api.py`가 이 경계를 지킨다. `__all__`, 지연 로딩 표, 타입 검사용 import가 서로 같은지 보고,
사용자용 문서(`README.md`, `docs/features/`, `docs/tutorials/`, 앱 개발자용 스킬)와 `examples/`가
`from wireview import ...`로만 import하며 그 이름이 모두 공개인지 본다.

### 통합 지점

이름을 import하지 않고 경로나 문자열로 가리키는 것도 공개다.

| 무엇 | 형태 |
|------|------|
| Django 앱 | `INSTALLED_APPS`의 `"wireview"` |
| URL | `include("wireview.urls")`, `wireview.urls.websocket_urlpatterns` |
| 템플릿 태그 | `{% load wireview %}`와 그 태그들 |
| 설정 | `settings.WIREVIEW`의 키 (`wireview/settings.py`의 `DEFAULT`) |
| 관리 명령 | `wireview_stubs`, `wireview_lsp`, `wireview_agent_setup`, `wireview_upload_gc` |
| 시스템 체크 id | `wireview.W001`~ |
| 컴포넌트 클래스 설정 | `class Meta:`의 키(`ComponentOptions`의 필드)와 `get_subscriptions()` |
| `self.wire` | `params`, `redirect_to`, `replace_to`, `push_to`만. 나머지는 프레임워크 내부이고, 같은 일은 `Component`의 메서드(`put_flash`, `push_js`, `push_title`, `defer` 등)로 한다 |
| 클라이언트 | `window.wireview`의 문서화된 멤버, `wire-*` DOM 속성, `wireview-*` CSS 클래스, `wireview:*` DOM 이벤트 |

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

## 지원 범위

지원하는 Python·Django 버전의 정본은 `pyproject.toml`과 `.github/workflows/ci.yml`의 매트릭스다. 1.0에서
어떤 버전을 얼마나 오래 지원할지는 [#93](https://github.com/itda-work/django-wireview/issues/93)의 1.0rc
항목에서 정한다.
