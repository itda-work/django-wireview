# 업그레이드 가이드

## 1.0.0rc4에서 1.0으로

- **`mutation()`이 받은 `instance`의 `save()`가 보통의 저장이 됐다**(**조용함**, #153). 전에는 Django 역직렬화기의 저장
  (`save_base(raw=True)`)이라 모델의 `save()` 오버라이드를 건너뛰고 시그널을 `raw=True`로 보냈다. 이제는 둘 다 정상으로
  돈다. m2m은 더 이상 페이로드 값으로 되돌리지 않는다(보통의 `save()`처럼 그대로 둔다). 받은 인스턴스를 저장하지 않으면 고칠 것이 없다. 저장한다면 [설정의 모델 알림](./features/settings.md#모델-알림)에서
  무엇을 쓰는지 확인한다 — 여전히 알림 때 페이로드에 실린 필드를 모두 쓴다. 다중 테이블 상속의 자식이면 부모 모델의
  필드는 이제 deferred다. 전에는 기본값(`''`·`None`)으로 오류 없이 읽혔으니, 그 값을 읽던 코드는 `arefresh_from_db()`로 먼저 읽는다.
- **픽스처 로드(`loaddata`)는 더 이상 모델 알림을 내지 않는다**(**조용함**). `raw=True` 저장과, `loaddata`가 그 객체에 이어서
  채우는 m2m을 거른다. 픽스처를 넣어 화면이 갱신되기를 기대하던 코드나 테스트는 행을 보통으로 저장하거나 알림을 직접 보낸다.

## 1.0.0rc1에서 1.0으로

1.0이 공개 API를 굳히기 전에 모양을 한 번 더 정리했다(#119, 1.0.0rc2). 0.7이나 1.0.0rc1을 쓰던 프로젝트는 아래를 확인한다.
대부분은 `TypeError`·`ImportError`·`manage.py check`로 드러나고, 조용히 달라지는 것은 **조용함**으로 표시했다.
전체 목록은 [CHANGELOG](../CHANGELOG.md).

### 1. `handle_async`는 `AsyncResult`를 받는다

```python
# 전
async def handle_async(self, name, result):
    if result[0] == "ok":
        self.data = result[1]

# 후
async def handle_async(self, name, result):
    if result.ok:
        self.data = result.result      # 실패면 result.failed, result.error
```

옛 코드는 `TypeError`를 던지고, 컨슈머가 그 컴포넌트를 버리고 다시 join시킨다(로그에 남는다).

### 2. m2m 채널 이름 (**조용함**)

`AUTO_BROADCAST.m2m`의 알림은 이제 양쪽 행 모두 `<app_label>.<model>.<pk>.<field>`로 간다. 끝에 상대 pk가 붙은
`<...>.<field>.<pk>` 형식을 구독했다면 아무것도 받지 못한다. 접미사 없는 형식으로 바꾼다.

### 3. 테스트의 `mount()`

옵션은 키워드로만 받는다. `mount(Cls, user)`는 `mount(Cls, user=user)`로. 이름이 옵션과 같은 필드(`params` 등)는
`state={"params": ...}`로 준다. 필드 값은 전처럼 키워드로 줘도 된다 — `mount(Counter, count=3)`은 그대로 동작하고,
`state=`는 옵션과 이름이 겹칠 때만 쓴다. `view.dom_actions`와 `view.clear_dom_actions()`는 없어졌다(항상 비어 있었다).

### 4. 없어진 이름

| 옛 것 | 대신 |
|-------|------|
| `from wireview import send_notification`, `asend_notification` | `broadcast`, `abroadcast` |
| `from wireview import ComponentNotFound`, `list_function_components` | 없음 (내부) |
| `telemetry.span`, `telemetry.payload_size` | 없음 (내부) |
| `Component.dom()` | 없음 |
| `wireview.debounce()`, `wireview.throttle()` (브라우저) | `{% on "input.debounce.300" ... %}` 또는 직접 만든 타이머 |

### 5. 템플릿의 `{% upload_button %}`

```html
<!-- 전 -->
{% upload_button "images" class="btn" %}Select</button>
<!-- 후 -->
<button type="button" {% upload_button "images" %} class="btn">Select</button>
```

### 6. 브라우저 API (**조용함**)

- `wireview.send(el, name, args, "click")`처럼 넷째 인자로 이벤트 종류를 넘겼다면 `{eventType: "click"}`로 바꾼다.
  문자열은 무시되어 로딩 클래스가 붙지 않는다. 대상 LiveComponent는 `args._target` 대신 `{target: id}`.
- `wireview.dom.onBeforeElUpdated(cb)`는 콜백을 **더한다**. `null`을 넘겨 지우던 코드는 돌려받은 함수를 부른다.
- 업로드 이벤트는 `wireview:upload-progress` 등으로도 나간다. 옛 `upload:progress`도 2.0까지 나가므로 급하지 않다.

### 7. 그 밖의 시그니처

- `self.scroll_into_view(id, "smooth")` → `self.scroll_into_view(id, behavior="smooth")`
- `self.wire.redirect_to(to="/x")` → `self.wire.redirect_to("/x")`. `push_to`·`replace_to`도 같다.

### 8. 설정

`SYNC_TRANSITION_WARNING_THRESHOLD`·`SYNC_TRANSITION_ERROR_THRESHOLD`는
`DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD`·`DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD`로 바뀌었고
`TRANSPILER_CACHE_SIZE`는 없어졌다. 남아 있으면 `manage.py check`가 `wireview.W014`로 알린다.

### 9. `AUTO_BROADCAST`는 `senders`에 적은 모델만 알린다

`senders`를 비워 두면 전에는 모든 모델을 알렸고, 이제는 **아무것도 알리지 않는다.** 구독한 컴포넌트의
`mutation()`이 더는 불리지 않으므로, 알릴 모델을 적는다.

```python
# 전
"AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True)

# 후
"AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, senders={("todo", "Item")})
```

플래그를 켜고 `senders`를 비워 두면 `manage.py check`가 `wireview.W015`로 알린다. 설치되지 않은 모델을 적으면
기동 때 `ImproperlyConfigured`다. `senders`에 적은 모델은 모든 필드가 채널 레이어로 직렬화되므로 `User`처럼
민감한 필드가 있는 모델은 넣지 않는다.

m2m 변경은 바꾼 쪽의 모델이 `senders`에 있을 때 알린다(**조용함**). `user.groups.add(g)`와 `group.user_set.add(u)`를
모두 알리려면 두 모델을 다 적는다. 상세는 [설정](./features/settings.md#모델-알림).

## 0.6에서 0.7, 1.0 릴리스 후보로

고칠 것이 없다. 0.7.0과 1.0.0rc1은 호환을 깨는 변경이 없다([CHANGELOG](../CHANGELOG.md)).

릴리스 후보는 사전 릴리스라 버전 범위를 평소처럼 적으면 설치되지 않는다. 하한에 rc를 적는다. **하한은 쓰는 기능이
들어간 rc 중 가장 이른 것, 단 철회(yank)된 rc는 건너뛴다.** rc2는 rc1과 호환되지 않고(위 절), rc3는 pydantic 2.13
이상에서 import가 실패해 철회됐다(#127). 그래서 지금은 rc4가 하한이다.

```toml
dependencies = ["django-wireview>=1.0.0rc4,<1.1"]
```

`>=1.0,<1.1`로 적으면 uv는 해를 찾지 못한다. pip는 `pip install --pre django-wireview` 또는
`pip install django-wireview==1.0.0rc4`로 설치한다. 1.0.0이 나온 뒤에는 하한을 `1.0`으로 바꿔도 된다.

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

함께 `channels>=4.2.1`, `pydantic>=2.7,!=2.9.0`이 필요하다. 1.0 rc까지 선언했던 하한 중 pydantic의 옛 버전은
설치되지 않거나 import에서 실패했고, channels 4.2.1 미만은 channels-nats 레이어에서 실패했다(#132). 둘 중 하나를
옛 버전에 고정했다면 풀어 준다.

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
