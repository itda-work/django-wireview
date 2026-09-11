# System Checks

wireview의 함정 중 상당수는 **에러를 내지 않는다.** sync 핸들러는 클라이언트가 부를 때까지
조용하고, `wireview.min.js`가 없으면 페이지가 그냥 정적으로 남고, `USE_HMIN`은 부분 diff를
말없이 토큰 diff로 되돌린다. 신호가 없으면 사람도 에이전트도 고칠 수 없다.

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
| `wireview.W002` | 라이프사이클 오버라이드가 async가 아님 (`joined`, `update`, `destroy`) | wireview가 `await`하므로 콜백이 아예 실행되지 않는다 |
| `wireview.W003` | 두 클래스가 같은 단순 이름으로 등록됨 | import 시 경고 한 번뿐. 템플릿은 둘 중 하나로만 해석된다 |
| `wireview.W004` | `wireview/wireview.min.js`를 staticfiles가 못 찾음 | JS가 로드되지 않아 페이지가 정적으로 남는다. 404 외에는 신호가 없다 |
| `wireview.W005` | `USE_HMIN`이 켜져 있고 `USE_HTML_DIFF`도 켜짐 | django-hmin이 diff 마커(HTML 주석)를 지워 부분 diff가 토큰 diff로 퇴화한다 |
| `wireview.W006` | 기본 채널 레이어가 `InMemoryChannelLayer` | 다중 프로세스에서 브로드캐스트가 같은 프로세스에만 닿고 오류는 나지 않는다 |
| `wireview.W007` | `_on_mount`에 올린 클래스에 `on_mount`가 없거나 async가 아님 | 훅이 말없이 건너뛰어져, 인증 가드로 올린 훅이 아무것도 막지 않는다 |
| `wireview.W008` | `UPLOAD_TEMP_DIR`이 가리키는 경로에 임시 파일을 만들 수 없음 | 설정은 첫 청크가 올 때에야 읽힌다. 기동 시에는 아무 신호가 없고, 업로드가 하나씩 `ImproperlyConfigured`로 실패한다 |
| `wireview.W009` | `SIGNING_KEY`가 빈 문자열이거나, 키 없이 fallback만 설정됨 | `Signer(key="")`는 조용히 `SECRET_KEY`로 되돌아간다. 아무것도 깨지지 않는 것이 문제다 — `SECRET_KEY`를 돌리면 진행 중인 업로드와 열린 페이지의 `data-state`가 같이 죽는다 |
| `wireview.W010` | 경계가 선언됐는데 `context_processors.request`가 꺼져 있거나, `_live_sessions`가 아무도 선언하지 않은 이름을 가리키거나, 경계가 있는 프로젝트에서 `_on_mount`로만 자신을 지키는 컴포넌트가 소속을 선언하지 않거나, `STATE_ACCEPT_LEGACY`가 경계와 함께 켜져 있음 | 프로세서가 없으면 경계가 통째로 조용히 꺼진다. 오타는 join 거절과 reload로 나타나 서명 문제처럼 보인다. 선언이 없는 컴포넌트는 경계 밖 페이지에서도 마운트된다. 롤아웃 플래그는 경계가 있으면 적용되지 않는데, 켜 둔 쪽은 창이 열려 있다고 믿는다 |
| `wireview.W011` | 템플릿의 `wire-hook="X"`를 등록하는 훅 파일이 수집된 것 중에 없음 | 클라이언트가 콘솔 경고 한 줄만 남긴다. 컴포넌트는 정상으로 렌더되고 동작 하나가 빠진다 |

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

### W010이 네 가지를 보는 이유

첫째는 전제 조건이다. 템플릿 태그는 페이지의 경계를 `context["request"]`에서 읽으므로
`django.template.context_processors.request`가 꺼져 있으면 **기능 전체가 말없이 꺼진다** — 헤더는 빈
이름을 심고, 모든 상태가 경계 없이 서명되고, `_live_sessions`를 선언한 컴포넌트는 자기 페이지에서
사라진다. 페이지는 200으로 그려지고 아무것도 예외를 던지지 않는다.


경계는 **옵트인**이다. 그 결과 실수가 두 방향으로 난다.

이름이 어긋나면 조용하지 않지만 엉뚱하게 보인다. `_live_sessions = {"admn"}`인 컴포넌트가 실린
페이지는 join에서 "unknown live_session"으로 거절되고 브라우저가 reload한다 — 화면에는 서명이
깨진 것처럼 보인다. 검사가 오타를 이름으로 짚는다.

반대로 선언을 빠뜨리면 아무 신호가 없다. `_on_mount` 훅으로만 자신을 지키는 컴포넌트는 경계 밖
페이지에서도 마운트되고, 훅은 돌지만 "여기 있으면 안 된다"고 말하는 것은 아무것도 없다. 이 경고는
**프로젝트가 live_session을 하나라도 선언한 뒤에만** 뜬다. 선언하기 전에는 속할 곳이 없으므로 모든
컴포넌트가 원래 있던 자리에 있는 것이다.

```console
$ python manage.py check
<class 'billing.live.XInvoice'>: (wireview.W010) billing.live.XInvoice guards itself with
_on_mount but declares no _live_sessions.
```

정말로 어디서나 마운트되어도 되는 컴포넌트라면 그것이 옳은 상태다 — 그때는
`SILENCED_SYSTEM_CHECKS`가 아니라 그 사실을 코드에 적는 편이 낫다.

**이 검사가 보호 대상을 전부 찾아 주지는 않는다.** 자기 훅이 없고 페이지의 `authorize`에만 의존하던
컴포넌트는 여기에 걸리지 않는다 — 검사가 볼 수 있는 것은 클래스이지 그 클래스가 어느 페이지에
얹히는지가 아니다. 경계를 도입하는 배포에서는 목록을 직접 확인해야 한다
([live_session](./live-session.md)의 전환 절차).

세 번째는 설정 둘이 서로를 무효화하는 경우다. `STATE_ACCEPT_LEGACY`는 봉투 이전 상태를 받아 주는
롤아웃 창인데, live_session이 선언되면 적용되지 않는다 — 옛 토큰은 경계를 담고 있지 않고, 토큰
안에는 그 페이지에 경계가 있었는지도 없다. 켜 둔 쪽은 창이 열려 있다고 믿으므로 말해 준다.
[live_session](./live-session.md) 참고.

### W011이 놓치는 것

양쪽을 **소스에서 읽는다.** 템플릿에서 `wire-hook="..."`를 정규식으로 찾고, 수집된 훅 파일에서
`wireview.hooks.<이름> =` 을 정규식으로 찾는다. 그래서 둘 다 못 보는 것이 있다.

- 렌더 시점에 만들어지는 이름(`wire-hook="{{ name }}"`)은 건너뛴다.
- 자기 번들로 훅을 등록하는 프로젝트의 이름은 알 수 없다.

첫째 때문에 **놓치는 쪽**이 있고, 둘째 때문에 **오탐**이 날 수 있다. 그래서 경고이고,
힌트가 `SILENCED_SYSTEM_CHECKS` 를 알려 준다. 어느 앱도 규약 경로에 훅 파일을 두지 않았다면
비교할 대상이 없다는 뜻이므로 이 검사는 아무 말도 하지 않는다.

### W007이 보안 검사인 이유

`_on_mount` 훅은 [라이프사이클 훅 문서](./lifecycle-hooks.md)의 첫 예제부터 **인증 가드**다.
그런데 `_run_on_mount_hooks`는 `on_mount`가 없는 항목을 예외 없이 건너뛴다. 메서드 이름 오타
하나(`onmount`)면 **가드가 사라진 컴포넌트가 정상적으로 마운트된다.** 클라이언트에는 아무
신호도 없다. `async`를 빠뜨린 훅은 조용하지는 않지만 마운트 도중 `TypeError`로 터진다.

```console
$ python manage.py check
<class 'billing.live.XInvoice'>: (wireview.W007) 'AuthHook.on_mount' in billing.live.XInvoice._on_mount is not async.
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
SILENCED_SYSTEM_CHECKS = ["wireview.W005"]  # hmin 대역폭 손익을 실측하고 켜기로 결정했다면
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
from wireview.checks import iter_exposed_handlers
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
- [HTML Diff](./html-diff.md) — W005가 무엇을 지키는지
- [라이프사이클 훅](./lifecycle-hooks.md) — W007이 지키는 `_on_mount` 경계
- [live_session](./live-session.md) — W010이 지키는 페이지 경계
- [배포](../DEPLOYMENT.md) — W006과 채널 레이어 선택
