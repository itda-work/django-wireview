# System Checks

wireview의 함정 중 상당수는 **에러를 내지 않는다.** sync 핸들러는 클라이언트가 부를 때까지
조용하고, `wireview.min.js`가 없으면 페이지가 그냥 정적으로 남고, `WIREVIEW`의 키를 잘못 쓰면
말없이 무시된다. 신호가 없으면 사람도 에이전트도 고칠 수 없다.

이 검사들을 Django의 `checks` 프레임워크에 등록해 두면 `manage.py check`, `runserver`, CI에
**자동으로** 걸린다. 새 명령을 기억할 필요가 없다는 것이 핵심이다.

```console
$ python manage.py check
System check identified some issues:

WARNINGS:
<class 'todo.live.XTodoList'>: (wireview.W001) Event handler 'todo.live.XTodoList.total' is not async.
	HINT: The client can call 'total', and wireview awaits the result, so the call fails
	with TypeError. Make it 'async def', or rename it to '_total' if it is an internal
	helper rather than a handler.
```

## 검사 목록

| ID | 무엇을 잡나 | 조용히 실패하는 방식 |
|----|------------|---------------------|
| `wireview.W001` | 이벤트 핸들러가 async가 아님 | 클라이언트가 부르면 `TypeError`. 부르기 전까지는 아무 신호도 없다 |
| `wireview.W002` | wireview가 await하는 콜백의 오버라이드가 async가 아님 (`joined`, `leaving`, `update`, `destroy`, `mutation`, `notification`, `params_changed`, `handle_async`, `handle_hook_event`) | wireview가 `await`하므로 콜백이 아예 실행되지 않는다 |
| `wireview.W003` | 두 클래스가 같은 단순 이름으로 등록됨 | import 시 경고 한 번뿐. 템플릿은 둘 중 하나로만 해석된다 |
| `wireview.W004` | `wireview/wireview.min.js`를 staticfiles가 못 찾음 | JS가 로드되지 않아 페이지가 정적으로 남는다. 404 외에는 신호가 없다 |
| `wireview.W005` | (없어짐) | `USE_HMIN`과 함께 #100에서 없어졌다. 번호는 다시 쓰지 않는다 |
| `wireview.W006` | 기본 채널 레이어가 `InMemoryChannelLayer` | 다중 프로세스에서 브로드캐스트가 같은 프로세스에만 닿고 오류는 나지 않는다 |
| `wireview.W007` | `Meta.on_mount`에 올린 클래스에 `on_mount`가 없거나 async가 아님 | 훅이 말없이 건너뛰어져, 인증 가드로 올린 훅이 아무것도 막지 않는다 |
| `wireview.W008` | `UPLOAD_TEMP_DIR`이 가리키는 경로에 임시 파일을 만들 수 없음 | 설정은 첫 청크가 올 때에야 읽힌다. 기동 시에는 아무 신호가 없고, 업로드가 하나씩 `ImproperlyConfigured`로 실패한다 |
| `wireview.W009` | `SIGNING_KEY`가 빈 문자열이거나, 키 없이 fallback만 설정됨 | `Signer(key="")`는 조용히 `SECRET_KEY`로 되돌아간다. 아무것도 깨지지 않는 것이 문제다 — `SECRET_KEY`를 돌리면 진행 중인 업로드와 열린 페이지의 `data-state`가 같이 죽는다 |
| `wireview.W010` | 경계가 선언됐는데 `context_processors.request`가 꺼져 있거나, `Meta.live_sessions`가 아무도 선언하지 않은 이름을 가리키거나, 경계가 있는 프로젝트에서 `Meta.on_mount`로만 자신을 지키는 컴포넌트가 소속을 선언하지 않음 | 프로세서가 없으면 경계가 통째로 조용히 꺼진다. 오타는 join 거절과 reload로 나타나 서명 문제처럼 보인다. 선언이 없는 컴포넌트는 경계 밖 페이지에서도 마운트된다 |
| `wireview.W011` | 템플릿의 `wire-hook="X"`를 등록하는 훅 파일이 수집된 것 중에 없음 | 클라이언트가 콘솔 경고 한 줄만 남긴다. 컴포넌트는 정상으로 렌더되고 동작 하나가 빠진다 |
| `wireview.W012` | `CHANNEL_LAYERS`에 `default` 레이어가 없음 | 페이지는 HTTP로 정상 렌더되는데 WebSocket 연결이 전부 거절되어 어떤 컴포넌트도 살아나지 않는다. Channels에는 기본 레이어가 없다 |
| `wireview.W013` | `runserver`로 기동하는데 그 명령이 Django의 WSGI 서버 그대로임 (`daphne`가 없거나 `INSTALLED_APPS`에서 너무 아래에 있음) | 페이지는 그려지고 오류도 없다. WebSocket 업그레이드가 거절되어 버튼이 아무 반응도 하지 않고, 흔적은 브라우저 콘솔 한 줄뿐이다 |
| `wireview.W014` | `settings.WIREVIEW`에 wireview가 읽지 않는 키가 있음 | 오타나 업그레이드로 없어진 키는 조용히 무시된다. 비슷한 키 이름이나 없어진 키의 대안을 알려 준다(#100) |
| `wireview.W015` | `AUTO_BROADCAST`의 `model`·`model_pk`·`related`·`m2m` 중 하나를 켰는데 `senders`가 비어 있음 | 비어 있는 `senders`는 아무 모델도 알리지 않는다. 구독한 컴포넌트의 `mutation()`이 한 번도 불리지 않고 오류도 없다. 알릴 모델을 적으라고 알려 준다([설정](./settings.md#모델-알림)) |
| `wireview.W016` | `RECONNECT_*` 값을 클라이언트가 쓸 수 없음(음수, `None`, 1보다 작은 `RECONNECT_GROW_FACTOR`), int·float가 아닌 값(문자열 등), 또는 대기가 설정대로 되지 않음: 대기가 0이거나, 첫 재연결 대기(`RECONNECT_MIN_DELAY_MS` + `RECONNECT_JITTER_MS`)가 `RECONNECT_MAX_DELAY_MS`를 넘거나, 대기가 브라우저 타이머 상한(2³¹−1ms)을 넘음 | 클라이언트는 읽을 수 없는 값을 기본값으로 바꿔 쓰므로 설정이 아무 일도 하지 않는다. 대기 0은 서버가 죽은 동안 쉬지 않는 재연결이다. 상한을 넘는 대기는 상한으로 잘려, 흩으려던 페이지들이 상한에서 다시 한꺼번에 붙는다([배포](../DEPLOYMENT.md#롤링-배포와-재연결)) |

전부 `Warning`이다. `manage.py check`의 기본 `--fail-level`은 `ERROR`이므로 이 검사들이
빌드를 깨지 않는다. **오탐 하나면 팀 전체가 검사를 무시하기 시작하므로** 확신이 설 때까지
등급을 올리지 않는다.

### W006만 `--deploy`인 이유

단일 프로세스 개발 환경에서 InMemory 레이어는 **옳은 선택**이다. 검사 시점에는 배포 시
프로세스가 몇 개일지 알 수 없다. 그래서 `manage.py check --deploy`에서만 뜨는 배포 검사로
등록했다. 개발 중에 매번 뜨는 경고는 정보가 아니라 소음이다.

```console
$ python manage.py check --deploy
?: (wireview.W006) The default channel layer is InMemoryChannelLayer.
```

### W012는 왜 `--deploy`가 아닌가

W006과 같은 설정을 보지만 묻는 것이 다르다. InMemory는 프로세스가 하나면 옳고, **레이어가 아예
없는 것은 프로세스가 하나여도 틀리다.** Channels는 `CHANNEL_LAYERS`에 `default`가 없으면 InMemory로
물러서지 않고 `None`을 돌려주며, 그 경우 컨슈머에 `channel_name`도 만들지 않는다. 예전에는
`connect()`가 소켓을 accept한 뒤 그 속성을 읽다가 `AttributeError`로 죽었고, 그 트레이스백은 설정도
해법도 말해 주지 않았다([#87](https://github.com/itda-work/django-wireview/issues/87)).

지금은 두 곳에서 같은 문장으로 말한다. 검사가 기동 전에 알려 주고, 검사를 거치지 않는 경로
(`uvicorn myproject.asgi:application`을 직접 띄운 경우)에서는 컨슈머가 accept 전에
`ImproperlyConfigured`로 연결을 거절한다. 문장의 정본은 `wireview/core/transport.py`의
`NO_CHANNEL_LAYER` 하나다.

단일 프로세스라면 고칠 것은 세 줄이다.

```python
CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}
```

### W013은 `runserver`를 띄울 때만 뜬다

Django의 `runserver`는 WSGI 서버다. ASGI로 바뀌는 것은 `daphne` 같은 앱이 명령을 **갈아 끼울** 때뿐이고,
명령은 `INSTALLED_APPS`에서 먼저 나오는 앱의 것이 이기므로 `daphne`는 `django.contrib.staticfiles`와
`whitenoise.runserver_nostatic`보다 위에 있어야 한다. 아래에 두면 설치는 됐는데 여전히 WSGI다.

이 검사는 프로세스가 `runserver`로 시작됐을 때만 본다(`sys.argv[1]`, Django가 하위 명령을 읽는 방식
그대로). `INSTALLED_APPS`의 `runserver`는 uvicorn으로 띄우는 프로젝트에 대해 아무것도 말해 주지 않기
때문이다 — 평소의 `manage.py check`나 CI에서는 뜨지 않는다. `runserver`는 기동할 때 검사를 돌리므로
경고는 기동 로그 맨 위에 나온다.

```console
$ python manage.py runserver
?: (wireview.W013) runserver (from 'django.contrib.staticfiles') is a WSGI server, so no WebSocket connection reaches wireview.
```

판정은 제공자 이름 목록이 아니라 **로드되는 명령이 Django의 `inner_run`을 그대로 쓰는가**다.
staticfiles와 whitenoise는 stock 명령을 감싸기만 하므로 잡히고, ASGI 서버는 `inner_run`을 바꿔야
하므로 이 검사가 모르는 ASGI 제공자도 오탐하지 않는다.

### W016은 클라이언트와 같은 규칙으로 본다

`{% wireview_header %}`는 `RECONNECT_*` 값을 문자열로 메타 태그에 싣고, 클라이언트(`reconnect.mjs`)는
유한한 숫자이고 하한(대기는 0, `RECONNECT_GROW_FACTOR`는 1) 이상인 값만 받는다. 나머지는 경고 없이
기본값으로 바꾼다 — 잘못된 설정이 촘촘한 재연결 루프가 되는 것보다 낫지만, 그래서 설정이 먹지 않았다는
신호가 아무 데도 없다([#134](https://github.com/itda-work/django-wireview/issues/134)).

int·float, `None`, bool에 대해서는 두 판정이 같다: 검사가 통과시킨 값은 클라이언트가 같은 숫자로 쓰고,
검사가 경고하는 값만 클라이언트가 버린다. `tests/test_checks.py`가 실제 헤더를 렌더해 `reconnect.mjs`에 넣고
이것을 확인한다.

그 밖의 값, 특히 환경 변수에서 `int()` 없이 읽은 문자열도 경고하지만 클라이언트가 무엇을 하는지는 말하지 않는다.
클라이언트는 JavaScript의 `Number()`로 읽어 `"30000"`은 30000으로 쓰고 `"1,000"`·`"30s"`는 버린다. 검사가 그 문법을
흉내 내지 않으므로 "기본값을 쓴다"고 단정하지 않고, 아래의 관계 판정도 하지 않는다 — 클라이언트가 쓰는 값을 모르기 때문이다.

나머지 경고는 클라이언트가 실제로 쓰는 값(버린 값 대신 기본값)으로 대기를 따진다.

- **대기 0.** 첫 대기의 끝(`RECONNECT_MIN_DELAY_MS + RECONNECT_JITTER_MS`)이나 `RECONNECT_MAX_DELAY_MS`가 0이면
  모든 재시도가 곧바로 나간다. 서버가 죽어 있는 동안 페이지마다 쉬지 않고 연결을 시도한다.
- **상한에 잘리는 첫 대기.** 첫 대기는 `RECONNECT_MIN_DELAY_MS`부터 그 값에 `RECONNECT_JITTER_MS`를 더한 값까지인데
  어떤 대기도 `RECONNECT_MAX_DELAY_MS`를 넘지 못한다. 넘는 쪽을 뽑은 페이지는 모두 상한에서 함께 다시 붙어, 지터가
  흩으려던 무리가 그대로 돌아온다. 최소 대기부터 상한을 넘으면 모든 대기가 상한이고 앞의 두 설정은 아무 효과가 없다.
- **브라우저 타이머 상한.** 브라우저는 `setTimeout`의 대기를 32비트 정수로 받는다(WebIDL `long`). 2³¹−1ms(약 24.8일)를
  넘는 대기는 2³²로 나눈 나머지가 되어, 2³¹부터 2³²ms까지는 곧바로 나가고 그 위는 엉뚱하게 짧아진다(Chromium 실측).
  `RECONNECT_GROW_FACTOR`가 1보다 크면 대기가 결국 상한까지 자라므로 상한을, 1이면 첫 대기의 끝을 본다.

```console
$ python manage.py check
?: (wireview.W016) The first reconnect waits 1000 to 21000 ms but no wait is longer than RECONNECT_MAX_DELAY_MS (10000 ms), so pages that draw a wait above 10000 ms all reconnect at 10000 ms together, which the jitter was to spread apart.
```

### W010이 세 가지를 보는 이유

첫째는 전제 조건이다. 템플릿 태그는 페이지의 경계를 `context["request"]`에서 읽으므로
`django.template.context_processors.request`가 꺼져 있으면 **기능 전체가 말없이 꺼진다** — 헤더는 빈
이름을 심고, 모든 상태가 경계 없이 서명되고, `Meta.live_sessions`를 선언한 컴포넌트는 자기 페이지에서
사라진다. 페이지는 200으로 그려지고 아무것도 예외를 던지지 않는다.


경계는 **옵트인**이다. 그 결과 실수가 두 방향으로 난다.

이름이 어긋나면 조용하지 않지만 엉뚱하게 보인다. `Meta.live_sessions = {"admn"}`인 컴포넌트가 실린
페이지는 join에서 "unknown live_session"으로 거절되고 브라우저가 reload한다 — 화면에는 서명이
깨진 것처럼 보인다. 검사가 오타를 이름으로 짚는다.

반대로 선언을 빠뜨리면 아무 신호가 없다. `Meta.on_mount` 훅으로만 자신을 지키는 컴포넌트는 경계 밖
페이지에서도 마운트되고, 훅은 돌지만 "여기 있으면 안 된다"고 말하는 것은 아무것도 없다. 이 경고는
**프로젝트가 live_session을 하나라도 선언한 뒤에만** 뜬다. 선언하기 전에는 속할 곳이 없으므로 모든
컴포넌트가 원래 있던 자리에 있는 것이다.

```console
$ python manage.py check
<class 'billing.live.XInvoice'>: (wireview.W010) billing.live.XInvoice guards itself with
Meta.on_mount but declares no Meta.live_sessions.
```

정말로 어디서나 마운트되어도 되는 컴포넌트라면 그것이 옳은 상태다 — 그때는
`SILENCED_SYSTEM_CHECKS`가 아니라 그 사실을 코드에 적는 편이 낫다.

**이 검사가 보호 대상을 전부 찾아 주지는 않는다.** 자기 훅이 없고 페이지의 `authorize`에만 의존하던
컴포넌트는 여기에 걸리지 않는다 — 검사가 볼 수 있는 것은 클래스이지 그 클래스가 어느 페이지에
얹히는지가 아니다. 경계를 도입하는 배포에서는 목록을 직접 확인해야 한다
([live_session](./live-session.md)의 전환 절차).

### W011이 놓치는 것

양쪽을 **소스에서 읽는다.** 템플릿에서 `wire-hook="..."`를 정규식으로 찾고, 수집된 훅 파일에서
`wireview.hooks.<이름> =` 을 정규식으로 찾는다. 그래서 둘 다 못 보는 것이 있다.

- 렌더 시점에 만들어지는 이름(`wire-hook="{{ name }}"`)은 건너뛴다.
- 자기 번들로 훅을 등록하는 프로젝트의 이름은 알 수 없다.

첫째 때문에 **놓치는 쪽**이 있고, 둘째 때문에 **오탐**이 날 수 있다. 그래서 경고이고,
힌트가 `SILENCED_SYSTEM_CHECKS` 를 알려 준다. 어느 앱도 규약 경로에 훅 파일을 두지 않았다면
비교할 대상이 없다는 뜻이므로 이 검사는 아무 말도 하지 않는다.

### W007이 보안 검사인 이유

`Meta.on_mount` 훅은 [라이프사이클 훅 문서](./lifecycle-hooks.md)의 첫 예제부터 **인증 가드**다.
그런데 `_run_on_mount_hooks`는 `on_mount`가 없는 항목을 예외 없이 건너뛴다. 메서드 이름 오타
하나(`onmount`)면 **가드가 사라진 컴포넌트가 정상적으로 마운트된다.** 클라이언트에는 아무
신호도 없다. `async`를 빠뜨린 훅은 조용하지는 않지만 마운트 도중 `TypeError`로 터진다.

```console
$ python manage.py check
<class 'billing.live.XInvoice'>: (wireview.W007) 'AuthHook.on_mount' in billing.live.XInvoice.Meta.on_mount is not async.
	HINT: wireview awaits every on_mount hook, so a sync one fails with TypeError while
	the component is mounting. Declare it as 'async def on_mount'.
```

## 특정 검사만 돌리기

모든 검사에 `wireview` 태그가 붙어 있다.

```bash
python manage.py check --tag wireview
```

## 끄기

Django 표준대로 `SILENCED_SYSTEM_CHECKS`를 쓴다.

```python
SILENCED_SYSTEM_CHECKS = ["wireview.W006"]  # InMemory 레이어를 쓰는 단일 프로세스 배포라면
```

## 검사는 디스패처와 같은 규칙을 쓴다

W001은 "노출되는 핸들러"를 자체 판정하지 않는다. `ComponentRepository._is_valid_event_handler`와
`_is_user_defined_method` — **클라이언트 이벤트를 실제로 받는 그 코드** — 를 그대로 호출한다.
판정 로직을 복사했다면 규칙이 바뀔 때 검사가 조용히 거짓말을 하게 된다.

노출 규칙 자체는 [LiveComponent 문서](./live-component.md#update-콜백)에 있다. 요약하면
`_`로 시작하지 않으면서 **사용자 코드가 정의한** 이름만 노출되고, 프레임워크(`wireview.*`)와
Pydantic이 소유한 이름은 오버라이드해도 노출되지 않는다.

노출 목록을 직접 보고 싶으면 같은 헬퍼를 쓴다.

```python
from wireview import iter_exposed_handlers
from myapp.live import TodoList

print([name for name, _ in iter_exposed_handlers(TodoList)])
```

## 검사하지 않는 것

**상태 필드의 JSON 직렬화 가능성**은 뺐다. 상태는 `model_dump_json`으로 직렬화되고
Pydantic은 커스텀 serializer, `field_serializer`, wireview의 Django 모델 지원까지 고려해
런타임에 판단한다. 정적으로 흉내 내면 오탐이 나온다 — 정상 컴포넌트에 경고를 띄우는 검사는
없느니만 못하다. 이건 런타임에 터지므로 조용한 실패도 아니다.

## 관련 문서

- [LiveComponent](./live-component.md) — 노출 규칙과 라이프사이클 콜백
- [HTML Diff](./html-diff.md) — 부분 diff가 무엇에 기대는지
- [라이프사이클 훅](./lifecycle-hooks.md) — W007이 지키는 `Meta.on_mount` 경계
- [live_session](./live-session.md) — W010이 지키는 페이지 경계
- [배포](../DEPLOYMENT.md) — W006과 채널 레이어 선택
