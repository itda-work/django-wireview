---
name: wireview
description: django-wireview로 실시간 앱을 만들 때 쓴다. 컴포넌트를 새로 만들거나 고칠 때, 이벤트 핸들러·라이프사이클·상태 필드를 작성할 때, 템플릿에 {% on %}·{% component %}·슬롯을 쓸 때, Streams·Presence·파일 업로드·비동기 로딩을 붙일 때, 컴포넌트 테스트를 쓸 때 사용한다. 조용히 실패하는 함정(핸들러가 async가 아님, 이름 충돌, JS 미로드, InMemory 레이어)을 미리 피하고 manage.py check로 확인하는 절차를 담는다. django-wireview 라이브러리 자체를 고치는 절차가 아니다.
---

# django-wireview로 앱 만들기

Phoenix LiveView 스타일의 서버 렌더링 실시간 컴포넌트. 상태는 서버에 있고,
이벤트는 WebSocket으로 올라오고, 서버가 렌더한 HTML의 diff가 내려와 DOM을 갱신한다.
**클라이언트 상태를 따로 두지 않는다** — 이 점이 React식 설계와 갈리는 지점이다.

## 무엇을 언제 읽나

이 문서는 라우팅과 금지선만 담는다. 실제 작업 직전에 해당 참조를 읽는다.

| 지금 하려는 일 | 읽을 것 |
|---|---|
| 컴포넌트 클래스, 상태 필드, 이벤트 핸들러, 라이프사이클, 브로드캐스트, 비동기 | [references/component.md](./references/component.md) |
| 템플릿 태그, `{% on %}` 수정자, 슬롯, 조건부 클래스, 리다이렉트 | [references/templates.md](./references/templates.md) |
| 대용량 리스트(Streams), 온라인 표시(Presence), 파일 업로드 | [references/streams-uploads.md](./references/streams-uploads.md) |
| 테스트 작성 | [references/testing.md](./references/testing.md) |

## 컴포넌트 하나를 추가하는 절차

```
myapp/
├── live.py                              컴포넌트 클래스 (파일 이름은 자유. 앱 로드 시 import되기만 하면 된다)
└── templates/myapp/x-counter.html       컴포넌트 템플릿
```

1. **템플릿**을 만든다. 루트 엘리먼트에 `{% tag_header %}`가 반드시 있어야 한다.
2. **컴포넌트 클래스**를 만든다: `from wireview import Component`, `Meta.template_name`, Pydantic 필드, `async def` 핸들러.
3. **페이지 템플릿**에서 `{% component 'XCounter' id="counter" %}`로 심는다. 베이스 템플릿 `<head>`에 `{% wireview_header %}`.
4. **`manage.py check`를 돌린다.** 아래 함정 중 넷(`W001`·`W003`·`W004`·`W012`)을 여기서 잡는다. `W013`은 `runserver`가 뜰 때, `W006`은 `check --deploy`에서 나온다.

```python
from wireview import Component

class XCounter(Component):
    class Meta:
        template_name = "myapp/x-counter.html"

    amount: int = 0

    async def inc(self):
        self.amount += 1
```

```html
{% load wireview %}
<div {% tag_header %}>
  {{ amount }}
  <button {% on 'click' 'inc' %}>+</button>
</div>
```

## 함정 (여기서 대부분의 시간을 잃는다)

- **핸들러는 반드시 `async def`.** 동기 메서드는 클라이언트가 부르는 순간 터지고, 그전까지는 아무 신호가 없다. `wireview.W001`이 잡는다.
- **`_`로 시작하지 않고 사용자 코드가 정의한 메서드만** 이벤트 핸들러로 노출된다. 내부 헬퍼에는 반드시 `_` 접두사를 붙인다. 프레임워크·Pydantic 소유 이름(`joined`, `leaving`, `update`, `mutation`, `notification`, `params_changed`, `handle_async`, `on_upload_complete`, `model_post_init`, Pydantic의 `validate`·`copy`·`json` 등)은 오버라이드해도 클라이언트가 부를 수 없다. Phoenix의 `phx-change="validate"`를 옮겨 `validate`라는 핸들러를 쓰면 `{% on %}`이 렌더 때 거절한다. `mount`는 프레임워크 이름이 아니다 — 쓰면 아무도 부르지 않는 초기화가 아니라 클라이언트가 부를 수 있는 핸들러가 된다. 초기화는 `joined()`에서.
- **핸들러 인자는 `validate_call`로 검증된다.** 타입 힌트를 정확히 쓴다. 클라이언트가 보내는 값은 신뢰하지 않는다 — 권한 검사는 핸들러 안에서 직접 한다.
- **핸들러가 예외를 던지면 그 컴포넌트는 이벤트 전 상태로 돌아간다.** 던지기 전에 바꾼 필드는 남지 않지만, 이미 DB에 쓴 것은 남는다. 함께 성공해야 하는 쓰기는 `transaction.atomic`으로 묶는다. 브라우저에서는 `wireview:error` 이벤트로 알릴 수 있다(https://github.com/itda-work/django-wireview/blob/main/docs/features/errors.md).
- **상태 필드는 JSON 직렬화 가능해야 한다.** 매 이벤트마다 서명된 `data-state`로 왕복한다. 모델 인스턴스는 예외다 — 어디에 있든(`Item`, `list[Item]`, `dict[str, Item]`, `AsyncResult[Item]`) pk로 서명되고 재join 때 타입 표기를 따라 다시 읽힌다(삭제된 행은 목록에서 빠지고 단일 필드는 `None`). 그래서 타입을 `list`가 아니라 `list[Item]`으로 적는다. 큰 QuerySet을 필드에 담지 말고 `@property`로 매번 조회하거나 Streams를 쓴다.
- **컴포넌트 ID는 페이지 안에서 고유해야 한다.** 반복 렌더에서는 `id="item-"|concat:item.id`처럼 만든다.
- **컴포넌트 이름은 클래스명으로 전역 등록된다.** 다른 앱에 같은 클래스명이 있으면 경고가 나고 템플릿은 둘 중 하나로만 해석된다(`wireview.W003`). 그럴 때는 `{% component 'myapp:XCounter' %}`처럼 앱 이름을 붙인다.
- **`{% tag_header %}`가 없으면 그 컴포넌트는 살아나지 않는다.** 이벤트도 diff도 붙을 자리가 없다.
- **JS가 로드되지 않으면 페이지는 조용히 정적으로 남는다.** `{% wireview_header %}`와 staticfiles 설정을 확인한다(`wireview.W004`).
- **`runserver`는 `daphne` 앱이 `INSTALLED_APPS` 맨 위에 있을 때에만 WebSocket을 받는다.** 없으면 WSGI 서버가 뜨고, 페이지는 그려지는데 아무것도 반응하지 않는다. 오류도 없다. 기동 로그의 `Starting ASGI/Daphne`로 확인하고, WSGI로 뜨면 `wireview.W013`이 기동 로그에 경고한다. daphne를 안 쓰면 `uvicorn <project>.asgi:application --reload`. 이때 uvicorn은 정적 파일을 서빙하지 않으므로 `<project>/asgi.py`에서 `DEBUG`일 때 HTTP 앱을 `ASGIStaticFilesHandler`로 감싼다(스타터 템플릿이 그렇게 한다). 빠뜨리면 `wireview.min.js`가 404이고 역시 아무것도 반응하지 않는다.
- **`CHANNEL_LAYERS`가 없으면 아무것도 살아나지 않는다.** Channels에는 기본 레이어가 없어서, 비워 두면 WebSocket 연결이 전부 `ImproperlyConfigured`로 거절된다(`wireview.W012`). 개발과 단일 프로세스에는 `{'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}`면 된다.
- **새 프로젝트는 스타터 템플릿으로 시작하면 위 두 배선이 이미 들어 있다.** `django-admin startproject mysite --template <wireview 패키지>/project_template` — 경로와 만든 뒤 할 일은 튜토리얼 01의 첫 절.
- **프로덕션에서 InMemory 채널 레이어는 조용히 깨진다.** 프로세스를 둘 이상 띄우면 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는 나지 않는다. `channels-nats`나 `channels_redis`를 쓴다(`manage.py check --deploy`의 `wireview.W006`).
- **브로드캐스트 채널·구독(`Meta.subscriptions`·`get_subscriptions()`)·Presence 토픽 이름은 영숫자·`-`·`_`·`.`만, 100자 미만.** 채널 그룹 이름 규칙이다. `room:42`처럼 `:`가 들어가면 모든 채널 레이어가 `TypeError`를 던진다 — 구독이면 join이 실패한다. `mount()`의 테스트도 셋 모두 같은 이름을 거절하므로 단위 테스트에서 드러난다. `room.42`로 쓴다.
- **블로킹 ORM 호출을 핸들러에서 그냥 하지 않는다.** 핸들러와 라이프사이클 메서드는 이벤트 루프 위에서 돈다. `await Model.objects.aget(...)` 같은 async ORM API를 쓰거나 `sync_to_async`로 감싼다. 템플릿과 `@property`는 렌더 때 워커 스레드에서 읽히므로 동기 ORM을 써도 되지만, **핸들러가 그 property를 읽으면 루프 위에서 돈다** — 핸들러용으로는 async 헬퍼를 따로 둔다. `stream()`에는 QuerySet을 그대로 넘겨도 된다. 테스트를 `DJANGO_ALLOW_ASYNC_UNSAFE=1`로 돌리면 이 실패가 전부 가려진다.

## 정본 문서

이 스킬은 정본을 복제하지 않는다. 세부는 여기서 읽는다 (저장소 안에서 작업 중이라면 같은 문서가 `docs/features/`와 `docs/tutorials/` 아래에 있다).

| 무엇 | 정본 |
|---|---|
| 기능 레퍼런스 인덱스 | https://github.com/itda-work/django-wireview/blob/main/docs/features/README.md |
| 튜토리얼 15편 (학습 순서) | https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/README.md |
| 시스템 체크 W001~W016 | https://github.com/itda-work/django-wireview/blob/main/docs/features/checks.md |
| 설치·설정·API 전체 | https://github.com/itda-work/django-wireview/blob/main/README.md |
| 배포 (채널 레이어, Windows) | https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md |
| 동작하는 예제 앱 (개념 하나에 앱 하나) | https://github.com/itda-work/django-wireview/blob/main/examples/README.md |
