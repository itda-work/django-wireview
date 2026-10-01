# Wireview - Django를 위한 Phoenix LiveView

Wireview는 Django Channels를 사용하여 실시간 서버 렌더링 인터랙티브 UI를 구축할 수 있게 해주는 라이브러리입니다. Phoenix Framework의 LiveView와 유사합니다.

![Wireview 아키텍처 개요](https://raw.githubusercontent.com/itda-work/django-wireview/main/overview.jpg)

## 무엇이 포함되어 있나요?

VueJS나 ReactJS를 대체하는 것은 아니지만, Django의 모든 잠재력을 활용하여 인터랙티브한 프론트엔드를 만들 수 있습니다. 모든 것이 서버 사이드에서 렌더링되므로, 첫 번째 요청에서 의미 있는 정보가 포함된 인터페이스가 제공됩니다. Django 템플릿과 ORM의 모든 기능을 컴포넌트에서 직접 사용하고, 이벤트 구독을 통해 실시간으로 인터페이스를 업데이트할 수 있습니다.

**주요 기능:**
- 실시간 업데이트가 가능한 서버 사이드 렌더링 컴포넌트
- 자동 검증이 포함된 Pydantic 기반 상태 관리
- Django Channels를 통한 WebSocket 통신
- 효율적인 대역폭 사용을 위한 HTML diff
- 자동 UI 업데이트를 위한 모델 구독
- 대규모 리스트를 효율적으로 처리하는 Streams API
- 온라인 사용자 및 타이핑 표시를 위한 Presence 추적
- 진행률 추적이 가능한 파일 업로드
- Chart.js, Mapbox 등 서드파티 라이브러리 통합을 위한 JavaScript Hooks

## django-reactor 대비 개선 사항

Wireview는 [django-reactor](https://github.com/edelvalle/reactor)의 현대적인 진화 버전으로, 다음과 같은 중요한 개선 사항이 있습니다:

### 새로운 기능

| 기능 | reactor | wireview | 설명 |
|------|---------|----------|------|
| **Streams API** | - | ✅ | `stream()`, `stream_insert()`, `stream_delete()`로 메모리 효율적인 대규모 리스트 처리 |
| **Presence API** | - | ✅ | `PresenceMixin`, `PresenceTrackerMixin`으로 실시간 사용자 추적 및 타이핑 표시 |
| **파일 업로드** | - | ✅ | 진행률 추적, 매직 바이트 검증이 포함된 청크 업로드 |
| **AsyncResult** | - | ✅ | 비동기 작업을 위한 로딩/성공/에러 상태 관리 |
| **JS 명령** | - | ✅ | `JS()` 빌더로 Phoenix LiveView.JS 스타일의 클라이언트 사이드 명령 |
| **테스트 유틸리티** | - | ✅ | WebSocket 없이 쉽게 컴포넌트 테스트를 위한 `mount()` 유틸리티 |
| **디버그 도구** | - | ✅ | `wireview.debug`로 브라우저 콘솔 디버깅 |
| **JavaScript Hooks** | - | ✅ | Chart.js, Mapbox 등 서드파티 JavaScript 라이브러리 통합 |
| **live_session** | - | ✅ | 페이지 단위 인증 경계. 같은 술어가 뷰와 join 양쪽에서 돌고, 경계를 넘는 이동은 전체 페이지 로드가 된다 ([문서](https://github.com/itda-work/django-wireview/blob/main/docs/features/live-session.md)) |

### 아키텍처 개선

| 항목 | reactor | wireview |
|------|---------|----------|
| **Pydantic** | v1 (레거시) | v2 (최신) |
| **DOM Morphing** | morphdom | idiomorph (더 나은 속성 보존) |
| **Python** | ≥3.9 | ≥3.12 |
| **Django** | 3.2+ | 5.2, 6.0, 6.1 |
| **모듈 구조** | 플랫 | 체계적 (`core/`, `features/`) |

### 새로운 컴포넌트 메서드

```python
# 라이프사이클
async def leaving(self):
    """컴포넌트 연결 해제 시 호출 - 정리 훅"""

# UI 제어
await self.scroll_into_view(element_id, behavior="smooth")
await self.push_js(JS().set_value("input", ""))

# Streams
await self.stream("items", items)
await self.stream_insert("items", item, at=0)
await self.stream_delete("items", item_id)

# Presence
await self.presence_join()
await self.presence_set_typing(True)

# 비동기 로딩
self.data = await self.assign_async(fetch_data())

# JavaScript Hooks
await self.push_event("update_chart", {"data": [1, 2, 3]})
```

### reactor에서 마이그레이션

대부분의 reactor 컴포넌트는 최소한의 변경으로 작동합니다:

```python
# reactor
from reactor.component import Component

class XCounter(Component):
    class Meta:
        subscriptions = {"counter"}

# wireview (동일한 API)
from wireview import Component

class XCounter(Component):
    class Meta:
        subscriptions = {"counter"}
```

주요 차이점:
- 패키지 이름: `reactor` → `wireview`
- 설정 접두사: `REACTOR_*` → `WIREVIEW` dict
- 템플릿 태그: `{% load reactor %}` → `{% load wireview %}`

## 목차

- [django-reactor 대비 개선 사항](#django-reactor-대비-개선-사항)
- [설치 및 설정](#설치-및-설정)
- [빠른 시작](#빠른-시작)
- [예제](#예제)
- [컴포넌트 라이프사이클](#컴포넌트-라이프사이클)
- [이벤트 바인딩](#이벤트-바인딩)
- [URL 상태 관리](#url-상태-관리)
- [모델 구독](#모델-구독)
- [Streams API](#streams-api)
- [Presence API](#presence-api)
- [파일 업로드](#파일-업로드)
- [AsyncResult](#asyncresult와-비동기-작업)
- [JS 명령 빌더](#js-명령-빌더)
- [JavaScript Hooks](#javascript-hooks)
- [컴포넌트 API 레퍼런스](#컴포넌트-api-레퍼런스)
- [템플릿 태그 레퍼런스](#템플릿-태그-레퍼런스)
- [JavaScript API](#프론트엔드-api)
- [테스트](#컴포넌트-테스트)
- [디버그 도구](#디버그-도구)
- [설정](#설정)

## 설치 및 설정

Wireview는 Python ≥3.12과 Django ≥5.2가 필요합니다 (Django 5.2 LTS, 6.0, 6.1 지원. 범위는 [호환성 정책](https://github.com/itda-work/django-wireview/blob/main/docs/COMPATIBILITY.md#지원-범위)).

```bash
pip install django-wireview daphne
```

새 프로젝트라면 아래 설정을 옮겨 적는 대신 스타터 템플릿으로 시작할 수 있습니다. 이 절의 배선이 모두 들어간
프로젝트와 [튜토리얼 01](https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/01-getting-started.md)의 첫 컴포넌트가 생기고, `manage.py check`는
아무것도 보고하지 않습니다.

macOS·Linux(bash, zsh):

```bash
django-admin startproject mysite --template "$(python -c "import wireview, os; print(os.path.join(os.path.dirname(wireview.__file__), 'project_template'))")"
```

Windows PowerShell:

```powershell
django-admin startproject mysite --template (python -c "import wireview, os; print(os.path.join(os.path.dirname(wireview.__file__), 'project_template'))")
```

Windows 명령 프롬프트(cmd.exe)에는 명령 치환이 없으므로 두 단계로 합니다. 첫 줄이 출력한 경로를 `--template` 뒤에 붙여 넣습니다.

```bat
python -c "import wireview, os; print(os.path.join(os.path.dirname(wireview.__file__), 'project_template'))"
django-admin startproject mysite --template C:\...\wireview\project_template
```

`daphne`는 개발 서버용입니다. Django의 `runserver`는 WSGI 서버라 WebSocket을 받지 못하고, `daphne` 앱이 `INSTALLED_APPS` 맨 위에 있을 때에만 ASGI로 바뀝니다. 빠뜨려도 오류는 나지 않고 페이지가 반응 없이 남습니다(`runserver` 기동 로그의 `wireview.W013` 경고가 유일한 신호입니다). daphne 대신 `uvicorn project_name.asgi:application --reload`로 띄워도 됩니다(Windows에서는 이쪽입니다 — [docs/DEPLOYMENT.md](https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md)). 그때는 아래 `asgi.py`의 `ASGIStaticFilesHandler` 줄이 필요합니다. uvicorn은 정적 파일을 서빙하지 않아서, 빠뜨리면 `wireview.min.js`가 404이고 페이지는 그려지지만 어떤 컴포넌트도 살아나지 않습니다.

Wireview는 `django-channels`를 사용하고, **채널 레이어가 반드시 있어야 합니다.** Channels에는 기본 레이어가 없어서 `CHANNEL_LAYERS`를 비워 두면 WebSocket 연결이 전부 거절됩니다(`manage.py check`의 `wireview.W012`). 개발과 단일 프로세스에는 아래 설정의 InMemory 레이어면 충분합니다. 다만 InMemory는 프로세스 하나 안에서만 통하므로, 프로세스를 여러 개 띄우면 브로드캐스트가 **오류 없이** 같은 프로세스의 연결에만 닿습니다. 프로덕션에서는 프로세스를 잇는 레이어를 씁니다.

- [channels-nats](https://github.com/itda-work/channels-nats) — 이 프로젝트가 목표로 하는 레이어입니다. NATS 서버는 Go 바이너리 하나이고 Linux·macOS·Windows 네이티브 빌드가 있어, Redis 없이 SQLite 단일 서버와 Windows까지 같은 구성으로 갑니다.
- [channels_redis](https://channels.readthedocs.io/en/latest/topics/channel_layers.html) — Redis가 이미 있다면 이쪽입니다. 실측상 성능은 대등합니다.

배포 구성은 [docs/DEPLOYMENT.md](https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md), 두 레이어의 실측 비교는 [docs/design/transport-abstraction.md](https://github.com/itda-work/django-wireview/blob/main/docs/design/transport-abstraction.md) §5-3에 있습니다.

Django 애플리케이션보다 먼저 `wireview`와 `channels`를 `INSTALLED_APPS`에 추가하세요:

```python
INSTALLED_APPS = [
    'daphne',      # 맨 위. runserver가 WebSocket을 받게 합니다
    'wireview',
    'channels',
    ...
]

ASGI_APPLICATION = 'project_name.asgi.application'

# 개발·단일 프로세스용. 프로세스를 늘릴 때는 위의 channels-nats나 channels_redis로 바꿉니다.
CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'},
}
```

`project_name/asgi.py`를 수정하세요:

```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_name.settings')

import django
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from wireview.urls import websocket_urlpatterns

http = get_asgi_application()
if settings.DEBUG:
    # runserver는 정적 파일을 스스로 서빙하지만 uvicorn은 이 application만 서빙한다
    http = ASGIStaticFilesHandler(http)

application = ProtocolTypeRouter({
    'http': http,
    'websocket': AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
})
```

wireview의 컨슈머는 소켓을 받기 전에 `Origin` 헤더를 `ALLOWED_HOSTS`와 대조한다. 다른 사이트의 페이지가 사용자의 쿠키로 소켓을 여는 것을 막기 위해서다([배포 가이드](https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md#websocket의-origin)). 그래서 `AllowedHostsOriginValidator`로 따로 감쌀 필요는 없다.

프로젝트의 `urls.py`에 wireview의 HTTP 경로를 **루트에** 넣으세요. 파일 업로드가 이 경로로 청크를 보냅니다.
빠뜨려도 다른 기능은 모두 동작하고 업로드만 조용히 404가 납니다. 경로가 `/__wireview_upload__/`로 고정되어
있어 `path("wireview/", ...)`처럼 접두사 아래에 두어도 404입니다.

```python
from django.urls import include, path

urlpatterns = [
    path("", include("wireview.urls")),
    ...
]
```

템플릿에 wireview JavaScript를 포함하세요:

```html
{% load wireview %}
<!doctype html>
<html>
    <head>
        {% wireview_header %}
    </head>
    ...
</html>
```

## 빠른 시작

`x-counter.html` 템플릿을 생성하세요:

```html
{% load wireview %}
<div {% tag_header %}>
  {{ amount }}
  <button {% on 'click' 'inc' %}>+</button>
  <button {% on 'click' 'dec' %}>-</button>
  <button {% on 'click' 'set_to' amount=0 %}>reset</button>
</div>
```

`live.py`에 컴포넌트를 생성하세요:

```python
from wireview import Component


class XCounter(Component):
    class Meta:
        template_name = 'x-counter.html'

    amount: int = 0

    async def inc(self):
        self.amount += 1

    async def dec(self):
        self.amount -= 1

    async def set_to(self, amount: int):
        self.amount = amount
```

뷰 템플릿에서 컴포넌트를 렌더링하세요:

```html
{% load wireview %}
<!doctype html>
<html>
    <head>
        {% wireview_header %}
    </head>
    <body>
        {% component 'XCounter' %}
        {% component 'XCounter' amount=100 %}
    </body>
</html>
```

## 예제

동작하는 예제 앱 11개가 [examples/](https://github.com/itda-work/django-wireview/tree/main/examples)에 있습니다. 각 디렉터리가 개념 하나이고,
테스트와 README를 함께 가지고 있으며, CI가 매번 실행합니다.

| 예제 | 개념 |
|------|------|
| [todo](https://github.com/itda-work/django-wireview/tree/main/examples/todo) | 모델 구독으로 여러 탭이 같은 목록을 함께 본다 |
| [poll](https://github.com/itda-work/django-wireview/tree/main/examples/poll) | 쓰기는 핸들러가, 다시 그리기는 브로드캐스트가 |
| [rating](https://github.com/itda-work/django-wireview/tree/main/examples/rating) | 잠깐 쓰는 상태와 남는 상태를 갈라 둔다 |
| [search](https://github.com/itda-work/django-wireview/tree/main/examples/search) | 디바운스한 입력과 키보드로 고르는 결과 |
| [quiz](https://github.com/itda-work/django-wireview/tree/main/examples/quiz) | 컴포넌트 상태로 굴리는 상태 머신 |
| [chat](https://github.com/itda-work/django-wireview/tree/main/examples/chat) | Streams와 Presence |
| [dashboard](https://github.com/itda-work/django-wireview/tree/main/examples/dashboard) | AsyncResult로 느린 조회를 미룬다 |
| [notifications](https://github.com/itda-work/django-wireview/tree/main/examples/notifications) | 이름 붙인 채널로 컴포넌트끼리 알린다 |
| [livecomp](https://github.com/itda-work/django-wireview/tree/main/examples/livecomp) | 연결을 공유하는 중첩 컴포넌트 |
| [slots](https://github.com/itda-work/django-wireview/tree/main/examples/slots) | 내용을 호출자가 채우는 레이아웃 컴포넌트 |
| [hooks](https://github.com/itda-work/django-wireview/tree/main/examples/hooks) | 브라우저만 할 수 있는 일을 컴포넌트에 붙인다 |

저장소를 받은 뒤 처음이면 아래 순서대로 실행합니다. `wireview.min.js`는 빌드 산출물이라 저장소에 없고,
예제의 모델 테이블은 `make migrate`가 만듭니다 — 빠뜨리면 chat·poll·rating·quiz 페이지가 500입니다.

```bash
make install && npm ci   # Python·JS 의존성 (처음 한 번)
make build-js            # wireview.min.js
make migrate             # 예제 모델의 테이블
make run-daphne          # http://localhost:8000
```

## 컴포넌트 라이프사이클

### 초기화 및 렌더링

컴포넌트는 템플릿에 포함될 때 초기화됩니다:

```html
{% component 'Component' param1=1 param2=2 %}
```

파라미터는 컴포넌트 인스턴스를 반환하는 `Component.new()`에 전달됩니다.

### 조인 (Joins)

컴포넌트가 프론트엔드에 도달하면 WebSocket을 통해 백엔드에 "조인"합니다. 직렬화된 상태가 백엔드로 전송되고, 백엔드는 컴포넌트를 재구성하고 `Component.joined()`를 호출합니다.

```python
class ChatRoom(Component):
    async def joined(self):
        # 컴포넌트가 WebSocket으로 연결될 때 호출됨
        await self.broadcast(f"room.{self.room_id}", action="joined", user=self.username)
```

### 퇴장 (Leaving)

컴포넌트가 파괴되거나 WebSocket 연결이 닫히면 `Component.leaving()`이 호출됩니다. 정리 작업에 사용하세요:

```python
class ChatRoom(Component):
    async def leaving(self):
        # 컴포넌트 연결이 해제될 때 호출됨
        await self.broadcast(f"room.{self.room_id}", action="left", user=self.username)
```

### 사용자 이벤트

조인 후 컴포넌트는 `{% on %}` 템플릿 태그를 통해 사용자 이벤트를 받을 수 있습니다. 이벤트는 백엔드로 전송되고, 핸들러가 실행되며, 컴포넌트가 다시 렌더링됩니다.

### 모델 변경 알림

컴포넌트는 모델 변경을 구독할 수 있습니다. 변경이 발생하면 `Component.mutation()`이 호출됩니다:

```python
class TodoList(Component):
    class Meta:
        subscriptions = {"todo.item"}  # todo 앱의 Item 모델 변경 구독

    async def mutation(self, channel: str, action: ModelAction, instance):
        # 구독한 모델이 변경될 때 호출됨
        self.items = await self.load_items()
```

### 알림

임의의 메시지에는 `broadcast()`와 `notification()`을 사용하세요:

```python
# 발신자
await self.broadcast("chat.room.1", message="Hello!", sender=self.username)

# 수신자 ("chat.room.1" 구독 중)
async def notification(self, channel: str, **kwargs):
    message = kwargs.get("message")
    sender = kwargs.get("sender")
```

## 이벤트 바인딩

### 기본 문법

```html
{% on <event.modifiers> <handler> [kwargs] %}
```

예제:

```html
<button {% on "click" "increment" %}>+1</button>
<button {% on "click" "increment" amount=5 %}>+5</button>
<button {% on "click.prevent" "submit" %}>제출</button>
<input {% on "keypress.enter" "search" %}>
<input {% on "input.debounce.300" "filter" %}>
```

### 사용 가능한 수정자

| 수정자 | 설명 |
|--------|------|
| `prevent` | `event.preventDefault()` 호출 |
| `stop` | `event.stopPropagation()` 호출 |
| `ctrl`, `alt`, `shift`, `meta` | 수정 키 필요 |
| `debounce.<ms>` | 이벤트 디바운스 (예: `debounce.300`) |
| `throttle.<ms>` | 이벤트 쓰로틀 (예: `throttle.100`) |
| `enter`, `tab`, `delete`, `backspace`, `space` | 키 별칭 |
| `up`, `down`, `left`, `right` | 화살표 키 별칭 |
| `key.<keycode>` | 특정 키 (예: `key.escape`) |

`{% on %}`은 인라인 JavaScript가 아니라 `wire-on-<이벤트>[.<수정자>…]` 데이터 속성을 렌더하고, 번들이 문서 루트에서
이벤트를 위임받아 처리합니다. 그래서 `'unsafe-inline'` 없는 Content Security Policy와 함께 돕니다. 수정자는 왼쪽부터
적용되므로 `prevent`는 `debounce`보다 앞에 둡니다. 한 요소에 `keyup.enter`와 `keyup.esc`처럼 같은 이벤트를 여러 번
걸 수 있습니다. 자세한 것은 [CSP](https://github.com/itda-work/django-wireview/blob/main/docs/features/csp.md)를 보세요.

### 암시적 인자

컴포넌트 내의 폼 입력은 자동으로 인자로 전송됩니다:

```html
<div {% tag_header %}>
  <input name="query">
  <button {% on "click" "search" %}>검색</button>
</div>
```

```python
async def search(self, query: str):
    self.results = await self.do_search(query)
```

## URL 상태 관리

URL 쿼리 문자열에 컴포넌트 상태를 저장하세요:

```python
class SearchList(Component):
    query: str = ""

    @classmethod
    def new(cls, wire, **kwargs):
        kwargs.setdefault("query", wire.params.get("query", ""))
        return cls(wire=wire, **kwargs)

    async def filter_results(self, query: str):
        self.query = query
        self.wire.params["query"] = query  # URL 업데이트
```

복잡한 값에는 `.json` 접미사를 사용하세요:

```python
class TreeView(Component):
    expanded: bool = False

    @classmethod
    def new(cls, wire, id: str, **kwargs):
        kwargs["expanded"] = id in wire.params.get("expanded.json", [])
        return cls(wire=wire, id=id, **kwargs)

    async def toggle_expanded(self):
        self.expanded = not self.expanded
        expanded = self.wire.params.setdefault("expanded.json", [])
        if self.expanded:
            expanded.append(self.id)
        elif self.id in expanded:
            expanded.remove(self.id)
```

## 모델 구독

자동 UI 업데이트를 위해 Django 모델 변경을 구독하세요:

```python
class TodoList(Component):
    class Meta:
        subscriptions = {"todo.item"}  # {app_label}.{model_name} 형식

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.CREATED:
            self.items.append(instance)
        elif action == ModelAction.DELETED:
            self.items = [i for i in self.items if i.id != instance.id]
```

설정에서 자동 브로드캐스트를 활성화하세요:

```python
WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,      # 모델 변경 시 브로드캐스트
        model_pk=True,   # 채널 이름에 PK 포함
        senders={("todo", "Item")},  # 알릴 모델. 비우면 아무것도 알리지 않는다
    ),
}
```

`senders`에 적은 모델만 알린다. 알림에는 인스턴스의 모든 필드가 직렬화되어 채널 레이어로 가므로,
민감한 필드가 있는 모델(`User` 등)은 넣지 말고 필요한 필드만 담은 별도 모델을 쓴다.

## Streams API

Streams는 아이템을 개별적으로 렌더링하고 증분 업데이트를 전송하여 대규모 리스트를 메모리 효율적으로 처리합니다.

### 기본 사용법

스트림 컨테이너가 있는 템플릿. 컨테이너는 비워 둔다 — 항목은 컴포넌트 상태가 아니라 스트림으로 들어온다:

```html
{% load wireview %}
<div {% tag_header %}>
  <ul wire-stream="messages"></ul>
</div>
```

아이템 템플릿 (`chat/message_list_item.html`). 기본 경로는 컴포넌트의 `template_name`에 `_item`을 붙인 것이고,
항목은 `item`이라는 이름으로 들어온다:

```html
<li id="messages-{{ item.pk }}">
  <strong>{{ item.sender }}:</strong> {{ item.text }}
</li>
```

컴포넌트:

```python
class MessageList(Component):
    class Meta:
        template_name = "chat/message_list.html"

    async def joined(self):
        # 스트림으로 초기 로드. 항목은 컴포넌트 상태가 아니라 클라이언트에 남는다
        messages = [m async for m in Message.objects.order_by('-created')[:50]]
        await self.stream("messages", reversed(messages))

    async def add_message(self, text: str):
        message = await Message.objects.acreate(sender=self.user, text=text)
        await self.stream_insert("messages", message, at=-1)  # 끝에 추가
        await self.scroll_into_view(f"messages-{message.pk}")

    async def delete_message(self, message_id: int):
        await Message.objects.filter(id=message_id).adelete()
        await self.stream_delete("messages", message_id)
```

### Stream 메서드

| 메서드 | 설명 |
|--------|------|
| `stream(name, items)` | 스트림 초기화/리셋 |
| `stream_insert(name, item, at=-1)` | 아이템 삽입 (-1=끝, 0=처음, n=인덱스) |
| `stream_delete(name, dom_id)` | DOM ID 또는 PK로 아이템 삭제 |

### DOM ID 규칙

기본적으로 DOM ID는 `{stream_name}-{item.pk}` 패턴을 따릅니다. 커스텀 ID 함수:

```python
await self.stream("items", items, dom_id=lambda item: f"item-{item.uuid}")
```

### 커스텀 아이템 템플릿

```python
await self.stream_insert("messages", message, template="chat/special_message.html")
```

## Presence API

온라인 사용자와 타이핑 표시를 실시간으로 추적합니다.

### PresenceMixin (프로듀서)

자신의 프레즌스를 브로드캐스트하는 컴포넌트용:

```python
from wireview import Component, PresenceMixin


class ChatInput(PresenceMixin, Component):
    class Meta:
        template_name = "chat/input.html"

    room_id: int
    username: str

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_user_id(self) -> str:
        return str(self.user.pk)

    def _presence_username(self) -> str:
        return self.username

    async def joined(self):
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def on_typing(self):
        await self.presence_set_typing(True)  # 3초 후 자동 해제
```

### PresenceTrackerMixin (컨슈머)

다른 사용자의 프레즌스를 표시하는 컴포넌트용:

```python
from wireview import PresenceTrackerMixin


class OnlineUsers(PresenceTrackerMixin, Component):
    class Meta:
        template_name = "chat/online_users.html"

    room_id: int
    username: str

    def _presence_topic(self) -> str:
        return f"room.{self.room_id}"

    def _presence_my_user_id(self) -> str:
        return str(self.user.pk)

    def get_subscriptions(self) -> set[str]:
        return {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.username)
```

템플릿:

```html
{% load wireview %}
<div {% tag_header %}>
  <h3>온라인 ({{ this.presence_online_count }})</h3>
  <ul>
    {% for user in this.presence_users %}
      <li>
        {{ user.username }}
        {% if user.is_typing %}<span class="typing">입력 중...</span>{% endif %}
      </li>
    {% endfor %}
  </ul>
</div>
```

### Presence 속성

| 속성 | 설명 |
|------|------|
| `presence_users` | 모든 추적된 사용자 목록 |
| `presence_online_count` | 온라인 사용자 수 |
| `presence_typing_users` | 현재 타이핑 중인 사용자 목록 |

### Presence 설정

```python
from wireview import PresenceConfig

class MyComponent(PresenceMixin, Component):
    class Meta:
        presence = PresenceConfig(
            typing_timeout=3.0,     # 타이핑 자동 해제까지 초
            sync_on_join=True,      # 조인 시 다른 사용자에게 동기화 요청
            channel_prefix="presence",
        )
```

## 파일 업로드

진행률 추적과 검증이 포함된 파일 업로드를 처리합니다.

### 기본 설정

```python
from wireview import Component


class FileUploader(Component):
    class Meta:
        template_name = "uploader.html"

    avatar_url: str = ""

    async def joined(self):
        self.allow_upload(
            "avatar",
            accept=[".jpg", ".png", ".gif"],
            max_file_size=5 * 1024 * 1024,  # 5MB
        )

    async def save_avatar(self):
        async for upload in self.consume_uploads("avatar"):
            path = await upload.save_to("avatars/", filename=f"{self.user.pk}.jpg")
            self.avatar_url = str(path)
```

템플릿:

```html
{% load wireview %}
<div {% tag_header %}>
  <input type="file" wire-upload="avatar" accept=".jpg,.png,.gif">

  {% for entry in this.uploads.avatar %}
    <div class="upload-entry">
      {{ entry.client_name }} - {{ entry.progress }}%
      {% if entry.errors %}
        <span class="error">{{ entry.errors|join:", " }}</span>
      {% endif %}
    </div>
  {% endfor %}

  <button {% on "click" "save_avatar" %}>저장</button>
</div>
```

### UploadConfig 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `name` | 필수 | 업로드 필드 식별자 |
| `accept` | `[]` | 허용된 확장자 (예: `[".jpg", ".png"]`) |
| `max_entries` | `1` | 최대 동시 업로드 수 |
| `max_file_size` | `10MB` | 최대 파일 크기 (바이트) |
| `chunk_size` | `64KB` | 업로드 청크 크기 |
| `auto_upload` | `True` | 선택 시 즉시 업로드 시작 |

### ConsumedUpload 메서드

| 메서드 | 설명 |
|--------|------|
| `read()` | 전체 파일을 메모리로 읽기 |
| `open(mode="rb")` | 파일 핸들 열기 |
| `save_to(directory, filename=None)` | Django 스토리지에 저장 |
| `name` | 원본 파일명 |
| `size` | 파일 크기 (바이트) |
| `content_type` | MIME 타입 |

### 보안

Wireview는 확장자 위조를 방지하기 위해 저장 전에 파일 시그니처(매직 바이트)를 검증합니다.

## AsyncResult와 비동기 작업

로딩/에러 상태와 함께 비동기 데이터 로딩을 처리합니다:

```python
from wireview import Component, AsyncResult


class Dashboard(Component):
    class Meta:
        template_name = "dashboard.html"

    stats: AsyncResult = None

    async def joined(self):
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        return await Stats.objects.aget()
```

템플릿:

```html
{% if stats.loading %}
  <div class="spinner">로딩 중...</div>
{% elif stats.ok %}
  <div>총계: {{ stats.result.total }}</div>
{% elif stats.failed %}
  <div class="error">{{ stats.error_message }}</div>
{% endif %}
```

### AsyncResult 속성

| 속성 | 설명 |
|------|------|
| `loading` | 작업 진행 중이면 True |
| `ok` | 작업 성공이면 True |
| `failed` | 작업 실패면 True |
| `done` | 완료되면 True (성공 또는 실패) |
| `result` | 결과 값 (성공 시) |
| `error` | 예외 (실패 시) |
| `error_message` | 에러의 문자열 표현 |

### AsyncResult 메서드

| 메서드 | 설명 |
|--------|------|
| `map(func)` | 결과 값 변환 |
| `get_or(default)` | 결과 또는 기본값 가져오기 |
| `get_or_raise()` | 결과 가져오기 또는 에러 발생 |

## JS 명령 빌더

서버 왕복 없이 실행되는 클라이언트 사이드 명령를 빌드합니다:

템플릿은 인자를 받는 호출을 쓸 수 없으므로, 체인은 컴포넌트의 속성이 만들고 템플릿은 그 이름을 쓴다:

```python
from wireview import JS, Component


class Toolbar(Component):
    @property
    def toggle_modal(self) -> JS:
        return JS().toggle("#modal")

    @property
    def save_with_feedback(self) -> JS:
        # 명령 체이닝: 클래스를 바로 붙이고, 이어서 서버 핸들러를 부른다
        return JS().add_class("#btn", "loading").push("save")

    @property
    def fade_away(self) -> JS:
        # 트랜지션과 함께: (클래스, 밀리초)
        return JS().hide(transition=("fade-out", 300))

    async def save(self):
        ...
```

```html
<button {% on "click" this.toggle_modal %}>모달 토글</button>
<button id="btn" {% on "click" this.save_with_feedback %}>저장</button>
<div {% on "click" this.fade_away %}></div>
```

### 서버에서 JS 푸시

이벤트 핸들러에서 JS 명령 전송:

```python
async def clear_input(self):
    await self.push_js(JS().set_value("input[name=search]", ""))
```

### 사용 가능한 명령

`selector`를 비우면 명령이 붙은 엘리먼트 자신이 대상이다. `*` 뒤의 인자는 키워드로만 넘긴다.
`transition`은 `("클래스", 밀리초)` 튜플이다.

**표시:**
- `show(selector=None, *, transition=None, display=None)`
- `hide(selector=None, *, transition=None)`
- `toggle(selector=None, *, show_transition=None, hide_transition=None, display=None)`

**CSS 클래스:**
- `add_class(selector=None, classes=None, *, transition=None)`
- `remove_class(selector=None, classes=None, *, transition=None)`
- `toggle_class(selector=None, classes=None, *, transition=None)`

**속성:**
- `set_attr(selector=None, attr=None, value=None)`
- `remove_attr(selector=None, attr=None)`
- `set_value(selector=None, value='')` - 입력 값 설정

**포커스:**
- `focus(selector=None)`
- `focus_first(selector=None, *, input_only=False)`

**트랜지션:**
- `transition(selector=None, transition=None, *, time=None)`

**서버 통신:**
- `push(event, *, value=None, target=None)` - 서버로 이벤트 전송. `value`는 dict다

**네비게이션:**
- `navigate(url, *, replace=False)`
- `dispatch(event, *, to=None, detail=None, bubbles=True)`

### 로딩 클래스

서버 요청 중 다음 클래스가 자동으로 추가됩니다:

| 클래스 | 설명 |
|--------|------|
| `wireview-loading` | 모든 요청 중에 추가 |
| `wireview-click-loading` | 클릭 이벤트에 추가 |
| `wireview-submit-loading` | 제출 이벤트에 추가 |

```css
.wireview-loading {
  opacity: 0.5;
  pointer-events: none;
}
```

## JavaScript Hooks

JavaScript Hooks를 사용하면 Chart.js, Mapbox, CodeMirror 등 서드파티 JavaScript 라이브러리를 wireview 컴포넌트와 통합할 수 있습니다. Phoenix LiveView의 Hooks API를 따릅니다.

### Hook 정의

훅 파일은 앱의 `static/<앱 라벨>/hooks/` 아래에 둔다. `{% wireview_header %}`가 설치된 앱 전부에서 이 디렉터리를
찾아 `defer`로 싣고, wireview는 그 파일들이 다 돈 뒤에 컴포넌트를 join한다. 템플릿에 인라인 `<script>`로
두지 않는다 — wireview보다 먼저 실행되어 `window.wireview`가 아직 없고, boost 이동으로 들어간 페이지에서는
아예 실행되지 않는다. 상세는 [JavaScript Hooks](https://github.com/itda-work/django-wireview/blob/main/docs/features/hooks.md#훅-파일을-어디에-두나).

```javascript
// myapp/static/myapp/hooks/chart.js
window.wireview.hooks.ChartHook = {
  mounted() {
    // 엘리먼트가 페이지에 추가되면 호출
    const config = JSON.parse(this.el.dataset.config);
    this.chart = new Chart(this.el, config);
  },

  updated() {
    // DOM 업데이트 후 호출
    this.chart.update();
  },

  destroyed() {
    // 엘리먼트가 제거되면 호출
    this.chart.destroy();
  },

  disconnected() {
    // WebSocket 연결이 끊기면 호출
    this.el.classList.add('offline');
  },

  reconnected() {
    // WebSocket이 재연결되면 호출
    this.el.classList.remove('offline');
  }
};
```

### 템플릿에서 사용

```html
<div wire-hook="ChartHook" data-config='{"type": "line", "data": {...}}'>
</div>
```

### 서버로 이벤트 전송 (pushEvent)

```javascript
window.wireview.hooks.InfiniteScroll = {
  mounted() {
    this.page = 1;
    this.observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) {
        this.loadMore();
      }
    });
    this.observer.observe(this.el.querySelector('.sentinel'));
  },

  loadMore() {
    this.pushEvent("load_more", { page: this.page }, (response) => {
      if (response.hasMore) {
        this.page++;
      } else {
        this.observer.disconnect();
      }
    });
  },

  destroyed() {
    this.observer.disconnect();
  }
};
```

### 서버에서 이벤트 받기 (handleEvent)

```javascript
window.wireview.hooks.Notification = {
  mounted() {
    this.handleEvent("show_toast", ({ message, type }) => {
      this.showToast(message, type);
    });
  },

  showToast(message, type) {
    // 토스트 표시 구현
  }
};
```

### 서버 사이드 핸들러

```python
class Dashboard(Component):
    class Meta:
        template_name = "dashboard.html"

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict):
        """JavaScript Hook에서 보낸 이벤트 처리"""
        if event == "load_more":
            items = await self.fetch_items(payload.get("page", 1))
            return {"hasMore": len(items) == 20}
        return None

    async def update_chart(self, data: list):
        """모든 Hook에 이벤트 전송"""
        await self.push_event("update_data", {"values": data})
```

### Hook 라이프사이클

| 콜백 | 호출 시점 |
|------|----------|
| `mounted()` | 엘리먼트가 조인되고 첫 렌더링 후 |
| `beforeUpdate()` | DOM morph 전 (동기) |
| `updated()` | DOM morph 완료 후 |
| `destroyed()` | 엘리먼트가 DOM에서 제거될 때 |
| `disconnected()` | WebSocket 연결이 닫힐 때 |
| `reconnected()` | WebSocket이 재연결될 때 |

### Hook 컨텍스트

| 속성/메서드 | 설명 |
|-------------|------|
| `this.el` | Hook이 연결된 DOM 엘리먼트 |
| `this.pushEvent(event, payload, callback)` | 서버로 이벤트 전송 |
| `this.handleEvent(event, callback)` | 서버 이벤트 핸들러 등록 |

자세한 내용은 [JavaScript Hooks 문서](https://github.com/itda-work/django-wireview/blob/main/docs/features/hooks.md)를 참조하세요.

## 컴포넌트 API 레퍼런스

### `class Meta:`

컴포넌트 설정은 클래스 안의 `class Meta:`에 둔다. 하위 클래스는 자기 Meta에 적지 않은 키를 부모에게서 물려받는다.
모르는 키는 `TypeError`다.

| 키 | 기본값 | 설명 |
|----|--------|------|
| `template_name` | 필수 | 템플릿 경로 |
| `subscriptions` | `set()` | 구독할 채널. 상태에 따라 달라지면 `get_subscriptions()`를 오버라이드한다 |
| `temporary_assigns` | `set()` | 렌더 뒤 기본값으로 되돌릴 필드 |
| `exclude_fields` | `user`·`wire`·`session` | 서명 상태에서 뺄 필드. `user`·`wire`·`session`은 항상 빠진다. 서명 상태는 암호화되지 않아 브라우저에서 읽힌다 — 비밀은 여기로 뺀다 |
| `slots` | `{}` | 슬롯 정의 |
| `on_mount` | `[]` | `joined()` 전에 도는 훅 |
| `live_sessions` | `set()` | 마운트될 수 있는 `live_session` 이름 |
| `presence` | `None` | `PresenceMixin` 설정(`PresenceConfig`) |
| `sticky` | `False` | boost 이동으로 같은 id가 있는 페이지에 가면 인스턴스·DOM·훅이 이어진다 ([boost](https://github.com/itda-work/django-wireview/blob/main/docs/features/boost.md)) |

### 메서드와 필드

오버라이드하는 콜백, 부르는 메서드, 필드의 전체 목록과 시그니처는 [Component API](https://github.com/itda-work/django-wireview/blob/main/docs/features/component-api.md)에
있다. 거기 없는 멤버는 밑줄이 없어도 내부다. 내비게이션은 `self.wire`의 `redirect_to`·`replace_to`·`push_to`,
모듈 수준 브로드캐스트는 `broadcast(channel, **kwargs)`(sync)·`abroadcast(channel, **kwargs)`(async)다.

## 템플릿 태그 레퍼런스

```html
{% load wireview %}
```

| 태그 | 설명 |
|------|------|
| `{% wireview_header %}` | 필요한 JavaScript 포함 (약 70KB, gzip 약 22KB) |
| `{% wireview_toasts %}` | `toast(user, ...)`로 보낸 토스트를 받아 띄운다. 레이아웃에 한 번 ([플래시와 토스트](https://github.com/itda-work/django-wireview/blob/main/docs/features/flash.md)) |
| `{% component 'Name' kwarg=value %}` | 컴포넌트 렌더링 |
| `{% on 'event.modifiers' 'handler' kwargs %}` | 이벤트 핸들러 바인딩. `myself`와 `_target`은 예약 인자라 핸들러 인자 이름으로 쓸 수 없다 |
| `{% tag_header %}` | 루트 요소에 컴포넌트 속성 추가 |
| `{% cond {'hidden': is_hidden} %}` | 조건부 속성 |
| `{% class {'active': is_active} %}` | 조건부 CSS 클래스 |
| `{{ value\|str }}`, `{{ a\|concat:b }}` | 문자열로 바꾸기, 이어 붙이기 (`{% on %}` 인자를 만들 때) |

## 프론트엔드 API

```javascript
// 컴포넌트에 이벤트 전송. 옵션: eventType(로딩 클래스), commit, target(LiveComponent id)
wireview.send(element, 'handler_name', {arg1: value1})
wireview.send(element, 'save', {}, {eventType: 'submit'})

// 링크처럼 이동 (BOOST_PAGES면 전체 로드 없이)
wireview.visit('/rooms/3/')

// Hook 정의
wireview.hooks.MyHook = {
  mounted() { /* ... */ },
  updated() { /* ... */ },
  destroyed() { /* ... */ }
}

// 디버그 유틸리티
wireview.debug.enable()
wireview.debug.disable()
wireview.debug.status()
```

## 컴포넌트 테스트

WebSocket 없이 컴포넌트 테스트:

```python
import pytest
from wireview import mount


@pytest.mark.asyncio
async def test_counter_increment():
    view = await mount(Counter, count=0)
    await view.call("increment", amount=5)
    assert view.component.count == 5


@pytest.mark.asyncio
async def test_redirect():
    view = await mount(MyComponent)
    await view.call("do_redirect", url="/dashboard")
    assert view.redirected_to == "/dashboard"
    assert view.is_frozen
```

### 테스트 API

| 메서드/속성 | 설명 |
|-------------|------|
| `mount(ComponentClass, **kwargs)` | 테스트용 컴포넌트 마운트 |
| `view.component` | 컴포넌트 인스턴스 접근 |
| `view.call(handler, **kwargs)` | 이벤트 핸들러 호출 |
| `view.sent_messages` | 전송될 메시지들 |
| `view.redirected_to` | 리다이렉트 URL (있는 경우) |
| `view.is_frozen` | 컴포넌트 동결 여부 |
| `view.clear_messages()` | 전송 메시지 초기화 |

## 디버그 도구

```javascript
// 디버그 로깅 활성화
wireview.debug.enable()

// 디버그 로깅 비활성화
wireview.debug.disable()

// 네트워크 지연 시뮬레이션
wireview.debug.latency(500)  // 500ms 지연

// 연결 상태 표시
wireview.debug.status()

// 모든 컴포넌트 나열
wireview.debug.components()

// 특정 컴포넌트 가져오기
wireview.debug.component("rx-123")
```

## 설정

`settings.WIREVIEW`의 키 전부와 기본값은 [설정 레퍼런스](https://github.com/itda-work/django-wireview/blob/main/docs/features/settings.md)에 있다. 설정은 쓰는 시점에
읽으므로 테스트에서는 `override_settings(WIREVIEW={...})`로 바꾸고, 모르는 키는 `manage.py check`가
`wireview.W014`로 알려 준다.

```python
from wireview import AutoBroadcast

WIREVIEW = {
    "BOOST_PAGES": True,
    "AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, senders={("todo", "Item")}),
}
```

## 성능 최적화

최적의 성능을 위해:

- **uvloop 사용**: Uvicorn에서 `--loop uvloop` 옵션으로 더 나은 비동기 성능 달성
- **개발 중 전환 추적**: `DEBUG_SYNC_TRANSITIONS=True`로 중첩 async/sync 전환 감지
- **알림은 공개 API로 보낸다**: 컴포넌트 안에서는 `await self.broadcast(...)`, 컴포넌트 밖의 async 코드에서는
  `await abroadcast(...)`, 동기 코드(시그널 수신자, 뷰)에서는 `broadcast(...)`(`from wireview import abroadcast, broadcast`).
  동기 `broadcast()`는 트랜잭션이 커밋된 뒤에 보낸다

```python
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": True,  # 개발 환경에서만
}
```

자세한 내용은 [성능 가이드](https://github.com/itda-work/django-wireview/blob/main/docs/PERFORMANCE.md)를 참조하세요.

## 문서

- [아키텍처](https://github.com/itda-work/django-wireview/blob/main/docs/ARCHITECTURE.md) - 내부 설계 및 패턴
- [배포 가이드](https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md) - 프로덕션 배포 설정
- [성능 가이드](https://github.com/itda-work/django-wireview/blob/main/docs/PERFORMANCE.md) - 성능 최적화 팁
- [튜토리얼](https://github.com/itda-work/django-wireview/tree/main/docs/tutorials) - 단계별 가이드
- [로드맵](https://github.com/itda-work/django-wireview/blob/main/docs/ROADMAP.md) - 향후 개발 계획
- [업그레이드 가이드](https://github.com/itda-work/django-wireview/blob/main/docs/UPGRADING.md) - 0.4에서 1.0으로
- [호환성 정책](https://github.com/itda-work/django-wireview/blob/main/docs/COMPATIBILITY.md) - 공개 API, 폐기 절차, 지원 범위

## 개발 및 기여

```bash
git clone git@github.com:itda-work/django-wireview.git
cd django-wireview
make install && npm ci
make build-js     # wireview.min.js. clone 직후와 wireview.js를 고친 뒤
make test
```

테스트 서버 실행([예제](#예제)와 같은 서버입니다):

```bash
make migrate
make run-daphne   # http://localhost:8000
```

## 라이선스

MIT 라이선스 - 자세한 내용은 [LICENSE](https://github.com/itda-work/django-wireview/blob/main/LICENSE)를 참조하세요.
