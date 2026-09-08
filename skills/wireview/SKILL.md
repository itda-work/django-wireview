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
2. **컴포넌트 클래스**를 만든다: `from wireview import Component`, `_template_name`, Pydantic 필드, `async def` 핸들러.
3. **페이지 템플릿**에서 `{% component 'XCounter' id="counter" %}`로 심는다. 베이스 템플릿 `<head>`에 `{% wireview_header %}`.
4. **`manage.py check`를 돌린다.** 아래 함정 중 여섯 개를 여기서 잡는다.

```python
from wireview import Component

class XCounter(Component):
    _template_name = "myapp/x-counter.html"
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
- **`_`로 시작하지 않고 사용자 코드가 정의한 메서드만** 이벤트 핸들러로 노출된다. 내부 헬퍼에는 반드시 `_` 접두사를 붙인다. 프레임워크·Pydantic 소유 이름(`mount`, `update`, `joined`, `model_post_init` 등)은 오버라이드해도 클라이언트가 부를 수 없다.
- **핸들러 인자는 `validate_call`로 검증된다.** 타입 힌트를 정확히 쓴다. 클라이언트가 보내는 값은 신뢰하지 않는다 — 권한 검사는 핸들러 안에서 직접 한다.
- **상태 필드는 JSON 직렬화 가능해야 한다.** 매 이벤트마다 서명된 `data-state`로 왕복한다. 큰 QuerySet을 필드에 담지 말고 `@property`로 매번 조회하거나 Streams를 쓴다.
- **컴포넌트 ID는 페이지 안에서 고유해야 한다.** 반복 렌더에서는 `id="item-"|concat:item.id`처럼 만든다.
- **컴포넌트 이름은 클래스명으로 전역 등록된다.** 다른 앱에 같은 클래스명이 있으면 경고가 나고 템플릿은 둘 중 하나로만 해석된다(`wireview.W003`). 그럴 때는 `{% component 'myapp:XCounter' %}`처럼 앱 이름을 붙인다.
- **`{% tag_header %}`가 없으면 그 컴포넌트는 살아나지 않는다.** 이벤트도 diff도 붙을 자리가 없다.
- **JS가 로드되지 않으면 페이지는 조용히 정적으로 남는다.** `{% wireview_header %}`와 staticfiles 설정을 확인한다(`wireview.W004`).
- **프로덕션에서 InMemory 채널 레이어는 조용히 깨진다.** 프로세스를 둘 이상 띄우면 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는 나지 않는다. `channels-nats`나 `channels_redis`를 쓴다(`manage.py check --deploy`의 `wireview.W006`).
- **블로킹 ORM 호출을 핸들러에서 그냥 하지 않는다.** 핸들러는 async 컨텍스트다. `await Model.objects.aget(...)` 같은 async ORM API를 쓰거나 `sync_to_async`로 감싼다. 템플릿 안에서 지연 평가되는 QuerySet도 같은 문제를 만든다.

## 정본 문서

이 스킬은 정본을 복제하지 않는다. 세부는 여기서 읽는다 (저장소 안에서 작업 중이라면 같은 문서가 `docs/features/`와 `docs/tutorials/` 아래에 있다).

| 무엇 | 정본 |
|---|---|
| 기능 레퍼런스 인덱스 | https://github.com/itda-work/django-wireview/blob/main/docs/features/README.md |
| 튜토리얼 15편 (학습 순서) | https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/README.md |
| 시스템 체크 W001~W006 | https://github.com/itda-work/django-wireview/blob/main/docs/features/checks.md |
| 설치·설정·API 전체 | https://github.com/itda-work/django-wireview/blob/main/README.md |
| 배포 (채널 레이어, Windows) | https://github.com/itda-work/django-wireview/blob/main/docs/DEPLOYMENT.md |
| 동작하는 예제 앱 | https://github.com/itda-work/django-wireview/tree/main/tests/testproj |
