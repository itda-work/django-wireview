# 업그레이드 가이드

## 0.5에서 0.6으로

0.6은 1.0 전에 계약을 바로잡은 릴리스다. 대부분 결함 수정이지만 아래 넷은 코드나 테스트를 고쳐야 할 수 있다.
전체 목록은 [CHANGELOG](../CHANGELOG.md).

### 1. `temporary_assigns`는 `joined()`에서 불러온다 (**조용함**)

`Meta.temporary_assigns` 필드는 이제 서명 상태(`data-state`)에 실리지 않는다. 재접속으로 다시 join하면 그
필드는 기본값에서 시작한다. 템플릿 태그 인자로만 채우던 필드(`{% component "X" messages=... %}`)는 재접속 뒤
비므로, 불러오는 코드를 `joined()`로 옮긴다. 대신 초기화는 더 이상 변경이 아니어서, 목록과 무관한 렌더가 목록을 지우지 않는다
([temporary_assigns](./features/temporary-assigns.md)).

### 2. 중첩 컴포넌트 안의 훅은 그 컴포넌트의 것이다 (**조용함**)

부모가 먼저 join하면서 자식 컴포넌트 안의 훅까지 가져가던 결함을 고쳤다. 그래서 부모의 `push_event`가 자식
안의 훅에 닿던 코드는 이제 닿지 않는다. 훅을 가진 컴포넌트에서 보내거나, 훅을 부모 자신의 마크업으로 옮긴다
([훅](./features/hooks.md#훅-수명주기)).

### 3. 테스트: `call()`은 클라이언트가 부를 수 있는 것만 부른다

`MountedComponent.call()`이 `_`로 시작하는 메서드, 라이프사이클 메서드, 믹스인의 프레임워크 메서드를 부르면
`AssertionError`를 낸다. 메서드 자체를 시험하려던 테스트는 `await view.component.method(...)`로 직접 부른다.

### 4. 테스트: `view.wire.broadcasts`는 `view.broadcasts`로

`view.wire.broadcasts`·`view.wire.presence_broadcasts`는 `WireviewDeprecationWarning`을 내고 2.0에서 없어진다.

## 0.4에서 1.0으로

1.0 전에 API를 굳히면서 호환을 깨는 변경을 한 번에 모았다(#93). 아래 순서대로 하면 된다. 대부분은 틀리면
`TypeError`나 `manage.py check`의 경고로 드러나고, 조용히 달라지는 것은 따로 표시했다(**조용함**).

### 1. Django 5.2 이상

Django 5.0과 5.1은 Django의 지원이 끝나 지원 범위에서 빠졌다. 지원 범위는 Django가 보안 지원하는 버전과
Python 3.12 이상이다([호환성 정책](./COMPATIBILITY.md#지원-범위)).

### 2. import는 `wireview`에서

공개 API는 `wireview.__all__`뿐이다. 하위 모듈에서 import하던 이름은 모두 최상위에서 가져온다.

```python
# 전
from wireview.component import Component
from wireview.features.presence import PresenceMixin
from wireview.schemas import ModelAction
from wireview.testing import mount

# 후
from wireview import Component, ModelAction, PresenceMixin, mount
```

`wireview.component`는 `WireviewDeprecationWarning`을 내며 1.x 동안 동작하고 2.0에서 없어진다. 나머지 하위
모듈 경로는 약속이 아니므로 예고 없이 옮겨질 수 있다.

### 3. 컴포넌트 설정은 `class Meta:`로

밑줄 붙은 클래스 속성은 `class Meta:` 안의 키가 됐다. 옛 이름을 쓰면 새 위치를 알려 주는 `TypeError`가 난다.

```python
# 전
class Inbox(Component):
    _template_name = "mail/inbox.html"
    _subscriptions = {"mail"}
    _on_mount = [AuthHook]

# 후
class Inbox(Component):
    class Meta:
        template_name = "mail/inbox.html"
        subscriptions = {"mail"}
        on_mount = [AuthHook]
```

| 전 | 후 (`Meta` 키) |
|----|----------------|
| `_template_name` | `template_name` |
| `_subscriptions` | `subscriptions` |
| `_temporary_assigns` | `temporary_assigns` |
| `_exclude_fields` | `exclude_fields` |
| `_slots` | `slots` |
| `_on_mount` | `on_mount` |
| `_live_sessions` | `live_sessions` |
| `_presence_config` | `presence` |

- 하위 클래스는 자기 `Meta`에 적지 않은 키를 부모에게서 물려받는다.
- `exclude_fields`는 `user`·`wire`·`session`에 **더해진다**. 예전처럼 기본값을 함께 적을 필요가 없다.
- 상태에 따라 달라지는 구독은 `@property def _subscriptions`가 아니라 `get_subscriptions()`를 오버라이드한다.

```python
def get_subscriptions(self) -> set[str]:
    return {f"room.{self.room_id}"}
```

### 4. `deffer`는 `defer`

`self.deffer(self.handler, ...)`는 `self.defer(...)`다. `self.wire`에서 쓸 수 있는 것은 `params`,
`redirect_to`, `replace_to`, `push_to`뿐이다. flash, 제목, JS 명령은 `Component`의 메서드(`put_flash`,
`push_title`, `push_js`, `push_event`)로 한다.

### 5. 없어진 설정

`manage.py check`의 `wireview.W014`가 남은 키를 알려 준다.

| 설정 | 대신 |
|------|------|
| `STATE_ACCEPT_LEGACY` | 없음. v2 봉투 이전 상태를 가진 페이지는 한 번 새로 읽힌다 |
| `USE_HTML_DIFF` | 없음. diff는 항상 켜져 있다 |
| `USE_HMIN` | WebSocket 압축(permessage-deflate). hmin은 diff 마커를 지워 매번 HTML 전체를 보내게 했다 |

### 6. Origin 검사 (**조용함**)

컨슈머가 소켓을 받기 전에 `Origin` 헤더의 호스트를 `ALLOWED_HOSTS`와 대조한다. **페이지와 소켓의 호스트가
다른 배포**(예: 페이지 `www.example.com`, 소켓 `ws.example.com`)는 페이지의 호스트도 `ALLOWED_HOSTS`에
넣어야 한다. 빠뜨리면 소켓이 403으로 거절되고, 서버 로그에 `Refusing a WebSocket`이 WARNING으로 남는다.
[배포 가이드](./DEPLOYMENT.md#websocket의-origin).

### 7. 테스트에서 설정 바꾸기

wireview는 설정을 쓰는 시점에 읽는다. `override_settings(WIREVIEW={...})`가 그대로 동작한다.
`wireview.settings`에 값을 대입하던 테스트(monkeypatch 포함)는 `AttributeError`가 나므로 `override_settings`로
바꾼다.

### 8. 달라진 동작 (**조용함**)

코드를 고칠 필요는 없지만 알아 둘 것들이다.

- **핸들러 예외는 연결을 끊지 않는다.** 그 컴포넌트만 이벤트 전 상태로 다시 join한다. 던지기 전에 DB에
  쓴 것은 남는다. `wireview:error` 이벤트로 사용자에게 알릴 수 있다([오류 처리](./features/errors.md)).
- **join에 실패한 요소는 지워지지 않는다.** `wireview-error` 클래스가 붙는다.
- **async 작업은 컴포넌트가 떠나면 취소된다.** 재연결 뒤에도 필요하면 `joined()`에서 다시 시작한다
  ([비동기 작업](./features/async-operations.md#작업의-수명)).
- **연결이 끊긴 동안** `.prevent`가 붙은 폼과 링크는 제출·이동하지 않고, 훅의 `pushEvent`는 버려진다
  ([CSP 문서의 라이브가 아닐 때](./features/csp.md)).
- **컴포넌트의 자체 템플릿 캐시가 없어졌다.** Django의 cached loader(기본으로 켜져 있다)가 맡는다.
  `TEMPLATES`의 `loaders`를 직접 적으면서 cached loader로 감싸지 않았다면, 렌더마다 템플릿을 다시 컴파일한다.
- **마커가 없는 HTML**(다른 템플릿 엔진)은 바뀌면 전체가 나간다. 토큰 diff는 없어졌다.

### 9. 브라우저 번들

서버와 번들은 버전을 협상하므로(`vsn`) 배포 중에 옛 번들로 열린 페이지도 깨지지 않는다. 옛 번들은 핸들러 예외에
예전처럼 소켓을 닫고 다시 연결한다. 새로고침하면 새 번들을 받는다.
