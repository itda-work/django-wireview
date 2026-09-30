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
| 스타터 템플릿 | 설치된 패키지의 `wireview/project_template/` 디렉터리(`startproject --template`의 대상, [튜토리얼 01](./tutorials/01-getting-started.md)). 약속은 그 경로와, 만든 프로젝트가 `manage.py check`에 아무것도 보고하지 않는다는 것이다. 만들어진 파일은 사용자의 코드이므로 안의 내용은 릴리스마다 바뀔 수 있다 |
| 시스템 체크 id | `wireview.W001`~. 없앤 번호는 다시 쓰지 않는다 |
| 컴포넌트 클래스 설정 | `class Meta:`의 키(`ComponentOptions`의 필드)와 `get_subscriptions()` |
| 템플릿 컨텍스트 | 컴포넌트 템플릿의 `this`, 슬롯의 `let` 이름 |
| 훅 파일 위치 | 앱의 `static/<app_label>/hooks/*.js` ([hooks](./features/hooks.md)) |
| 모델 채널 이름 | `AUTO_BROADCAST`가 알리는 채널: `<app_label>.<model>`, `<app_label>.<model>.<pk>`, 가리키는 행의 `<app_label>.<model>.<pk>.<related_name>`, m2m은 양쪽 행의 `<app_label>.<model>.<pk>.<field>`(어느 쪽에서 바꿨든 같다). 밑줄은 하이픈이 된다. 알리는 모델은 `senders`에 적은 것뿐이고(비우면 없다), m2m은 바꾼 쪽의 모델이 `senders`에 있을 때 알린다 |
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
- **의존성의 하한도 약속이다.** `pyproject.toml`이 허용하는 가장 오래된 조합(지금은 Django 5.2, channels 4.2.1,
  pydantic 2.7.0)을 `make test-lowest`가 Python 3.12에서 설치해 스위트를 돈다. CI의 `test-lowest` 잡이 같은 것이다.
  하한 조합이 깨지면 하한을 올리고 `CHANGELOG.md`에 적는다(#132). 반대쪽 끝, 새 설치가 받는 최신 해는 `make test-latest`다.
- **설치되지 않거나 지원하는 기능(채널 레이어 포함)에서 동작하지 않던 하한을 올리는 것은 패치 릴리스다.** 그 하한에
  머문 사용자는 그 기능이 동작하는 조합을 갖고 있지 않았다. 지원하는 모든 기능에서 동작하던 하한을 올리는 것은 마이너 릴리스다.
- channels의 하한은 `tests/test_nats_layer.py`가 지킨다. 그 테스트는 nats-server 바이너리가 없으면 로컬에서는
  건너뛰지만 CI(`CI` 환경 변수)에서는 실패한다. CI의 단위 테스트 잡들은 E2E와 같은 nats 이미지에서 바이너리를 꺼내 쓴다.

### 채널 레이어

프로세스가 둘 이상이면 브로커가 있는 레이어가 필요하다. 지원하는 것은 아래 둘이고, 둘 다 E2E 스위트 전체를 돈다(#130).

| 레이어 | 패키지 | 검증한 버전 | 브로커 | 여러 프로세스 | 가득 찼을 때 |
|--------|--------|-------------|--------|---------------|--------------|
| NATS | `channels-nats` | 0.2.0 | nats-server 2.14 | 된다 | 받는 쪽 프로세스가 `channels_nats` 로거에 WARNING을 남기고 버린다 |
| Redis | `channels-redis` | 4.3.0 | Redis 8 | 된다 | `send`는 `ChannelFull`, `group_send`는 `channels_redis.core` 로거에 INFO를 남기고 버린다 |
| InMemory | `channels`에 포함 | — | 없음 | **안 된다** | `group_send`가 아무 흔적 없이 버린다 |

- **InMemory는 단일 프로세스 전용이다.** 여러 프로세스에 두면 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는
  나지 않는다. 개발 서버와 단일 프로세스 배포에만 쓴다.
- **검증한 버전**은 `uv.lock`이 고정한 버전이다. CI의 E2E 잡이 레이어마다 한 번씩 그 버전으로 돈다
  (`make ci-test-e2e LAYER=nats|redis`, 브로커는 표의 릴리스 태그 이미지를 서비스 컨테이너로 띄운다). 로컬에서는
  `make test-e2e`(NATS)와 `make test-e2e LAYER=redis`(`REDIS_URL`의 redis-server)다.
- **channels-nats는 channels 4.2.1 이상에서만 동작한다.** 자신은 `channels>=4`라고 선언하지만 4.2.1에서 생긴
  `require_valid_channel_name`을 부른다. django-wireview의 하한이 `channels>=4.2.1`인 이유다(#132).
- 유실을 세는 방법은 [배포 가이드](./DEPLOYMENT.md)의 관측 절이다.
- `uv.lock`에서 레이어 패키지를 올리면 두 E2E 레인을 돌리고 이 표의 버전을 같이 고친다.
  `tests/test_supported_versions.py`가 표와 `uv.lock`, CI의 E2E 매트릭스가 같은 레이어를 말하는지, `ci.yml`이 쓰는
  브로커 이미지 태그가 표의 브로커 릴리스와 같은지 본다.
