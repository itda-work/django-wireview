# Component API

`Component`와 `LiveComponent`의 공개 멤버 전부다. 여기 없는 멤버는 밑줄이 없어도 **내부**이고,
마이너 릴리스에서 바뀔 수 있다([호환성 정책](../COMPATIBILITY.md)). 사용법은 표의 링크에 있다.

`tests/test_public_api.py`가 이 표와 클래스를 대조한다. 밑줄 없는 멤버를 새로 만들면 여기 적거나
아래 "내부" 목록에 넣어야 테스트가 통과한다.

## 이름

클래스 선언의 키워드 두 개가 컴포넌트의 이름을 정한다. `class XCounter(Component)`는 클래스명 `XCounter`로
등록되고 템플릿이 `{% component "XCounter" %}`, `"myapp:XCounter"`, FQN으로 찾는다. `name="..."`을 주면
클래스명 대신 그 이름으로 등록된다.

`public=False`면 등록하지 않는다. 템플릿과 join이 이 클래스를 찾지 못한다. 테스트의 컴포넌트나 공통 베이스가
흔히 이렇게 선언한다. 그래도 로그·계측·서명 상태가 쓸 이름은 있어야 하고, 그 이름은 아래 순서로 정한다.

1. `name="..."`을 줬으면 그 이름
2. 등록된 클래스를 상속했으면 그 클래스의 이름
3. 둘 다 아니면 자기 클래스명. 등록되지 않은 베이스(`LiveComponent` 포함)의 이름은 물려받지 않는다

## 필드

| 이름 | 뜻 |
|------|----|
| `id` | 페이지 안에서 고유한 컴포넌트 id |
| `user` | 연결의 사용자 (`AnonymousUser` 포함) |
| `session` | Django 세션의 읽기 전용 뷰 ([session](./session.md)) |
| `wire` | 내비게이션과 쿼리. 공개는 `params`, `redirect_to`, `replace_to`, `push_to`뿐이다 ([navigation](./navigation.md)) |
| `uploads` | 업로드 항목. 템플릿의 `this.uploads.<name>` ([external-uploads](./external-uploads.md)) |

선언한 필드는 상태다. JSON으로 직렬화되어야 하고 모델 인스턴스는 pk로 서명된다. 설정은 `class Meta:`에
둔다(README의 Meta 표).

## 오버라이드하는 것

wireview가 부르는 콜백이다. `get_subscriptions()`와 `new()` 말고는 모두 `async def`로 쓴다 —
sync로 쓰면 실행되지 않고 `manage.py check`가 `wireview.W002`로 알린다.

이 문서의 모든 멤버는 프레임워크의 것이라 오버라이드해도 클라이언트가 이벤트로 부를 수 없다. 클라이언트가 부를 수
있는 것은 사용자 코드가 정의한, `_`로 시작하지 않는 메서드뿐이다. `{% on "click" "joined" %}`처럼 그 밖의 이름에
바인딩하면 렌더가 `AssertionError`로 멈춘다.

| 메서드 | 언제 |
|--------|------|
| `joined()` | 소켓에 연결되어 첫 렌더를 보내기 전. 이미 연결된 페이지에서 부모의 렌더가 새로 그린 `{% component %}`는 부모 템플릿 안에서 인라인으로 그려져 나가고, 페이지가 그 요소를 받아 join할 때 돈다 — 그 join에 답하는 렌더가 `joined()`가 바꾼 상태를 그린다. LiveComponent는 부모의 렌더가 자기 렌더 전에 부른다 ([live-component](./live-component.md)) |
| `leaving()` | 컴포넌트가 떠날 때 (소켓이 닫힘, 페이지 이동, 부모가 뺌). 부모가 `{% component %}`로 그리던 것을 더는 그리지 않으면 그 렌더와 함께 은퇴한다 — id 없이 그린 것은 부모가 다시 그릴 때마다 새 인스턴스라 옛 것이 은퇴한다. 은퇴한 인스턴스에는 그때부터 브로드캐스트·패치·작업의 결과가 닿지 않는다. 페이지가 그 렌더를 보기 전에 보낸 이벤트는 그래도 핸들러를 돌리고(그 렌더는 화면에 가지 않는다), `leaving()`은 페이지가 그 요소를 놓았다고 알릴 때(`wire-update="ignore"`처럼 알리지 않으면 은퇴하고 10초가 지난 뒤 끝나는 첫 렌더, 그런 렌더가 없으면 연결의 끝) 돈다. 핸들러가 있는 중첩 컴포넌트는 id를 주면 렌더마다 같은 인스턴스다. `joined()`와 짝이다 — `joined()`가 돈 인스턴스만 받는다(예외로 끝났어도). 페이지가 join하기 전에 떠난 중첩 `{% component %}`, 부모의 렌더가 `joined()`를 부르기 전에 사라진 LiveComponent는 받지 않는다. 그 인스턴스의 비동기 작업은 그래도 취소된다. 그래서 `joined()`에서 등록하고 여기서 해제하면 짝이 맞는다 |
| `params_changed(params, uri)` | URL 쿼리가 바뀌었을 때. `joined()` 뒤에만 — 부모의 렌더가 새로 그리고 페이지가 아직 join하지 않은 `{% component %}`는 건너뛴다. 그 join이 `joined()` 뒤에 그때의 쿼리로 부른다 |
| `mutation(channel, action, instance)` | 구독한 모델이 바뀌었을 때 (`AUTO_BROADCAST`). `instance`는 알림에 실려 온 값에서 복원한 것이다 — DB에서 다시 읽지 않고, 관계는 id만 있다. 같은 알림을 받는 컴포넌트마다 따로 복원하므로 고쳐도 다른 컴포넌트가 받는 값은 그대로다. 페이로드에 없는 필드(다중 테이블 상속의 부모 모델 필드, `senders` 매핑에 적지 않은 필드 등)는 deferred라 읽으면 DB를 조회한다 — async에서는 `SynchronousOnlyOperation`이므로 `await instance.arefresh_from_db(fields=[...])`로 불러온다. 저장하면 보통의 저장이다(모델의 `save()`, `raw=False` 시그널). 무엇을 쓰는지는 [설정의 모델 알림](./settings.md#모델-알림) |
| `notification(channel, **kwargs)` | 구독한 채널로 브로드캐스트가 왔을 때. `joined()` 뒤에만 받는다(`mutation()`도) — 부모의 렌더가 새로 그린 `{% component %}`가 페이지의 join을 기다리는 동안 온 알림은 그 인스턴스에 모아 두었다가 `joined()`와 join의 렌더 뒤에 온 순서대로 넘긴다. 그때 그 채널을 더는 구독하지 않으면 넘기지 않고, 페이지가 join하기 전에 그 컴포넌트를 놓으면 함께 버린다 |
| `handle_async(name, result)` | `start_async()`가 끝났을 때. `result`는 끝난 `AsyncResult`(`ok`/`failed`) ([async-operations](./async-operations.md)) |
| `handle_hook_event(hook_id, event, payload)` | 클라이언트 훅이 `pushEvent`로 보냈을 때 ([hooks](./hooks.md)) |
| `on_upload_complete(name, entry)` | 업로드 항목 하나가 끝났을 때, 다시 렌더하기 전. 청크 업로드는 파일이 서버에 다 있을 때만 불린다 ([chunked-uploads](./chunked-uploads.md), [external-uploads](./external-uploads.md)) |
| `get_subscriptions()` | 구독 채널을 상태에 따라 정할 때. 기본은 `Meta.subscriptions`. 세션이 언제 다시 읽는지는 아래 [구독을 다시 맞추는 때](#구독을-다시-맞추는-때) |
| `new(**kwargs)` (classmethod) | 인스턴스를 만들 때. 페이지 렌더, join, 테스트의 `mount()` 모두 이것을 거친다. `wire`·`user`·`session`과 상태 필드를 키워드로 받고 `cls(**kwargs)`를 돌려줘야 한다 |
| `LiveComponent.update(**assigns)` | 부모가 새 값을 줄 때 ([live-component](./live-component.md)) |
| `LiveComponent.update_many(updates)` (classmethod) | 부모 렌더 한 번에 값이 바뀐 같은 클래스 자식 전부를 `[(component, assigns), ...]`로. 기본은 각자의 `update()` |

## 부르는 것

| 메서드 | 하는 일 |
|--------|---------|
| `skip_render()` | 이번 이벤트의 렌더를 건너뛴다 |
| `force_render()` | 바뀐 것이 없어도 렌더한다 |
| `await send_render()` | 지금 렌더를 보낸다 (긴 작업의 중간 진행률) |
| `freeze()` | 이후 렌더를 보내지 않는다 |
| `await destroy()` | 컴포넌트를 페이지에서 뺀다 |
| `await broadcast(channel, **kwargs)` | 채널로 보낸다. `joined()` 전이면 모았다가 보낸다. 그 밖에는 즉시 보내고 트랜잭션 커밋을 기다리지 않는다 |
| `await push_event(event, payload=None, hook_id=None)` | 클라이언트 훅으로 보낸다 |
| `await push_js(js)` | `JS()` 명령을 실행한다. 같은 핸들러의 렌더가 아직 패치되지 않았으면 그 패치 뒤에 돈다 — 렌더로 드러낸 요소를 `to=`로 겨냥해도 된다 |
| `await push_title(title)` | 문서 제목을 바꾼다 |
| `await put_flash(flash_type, message, *, timeout=5000, dismissible=True)` / `await clear_flash(flash_id=None)` | 플래시 ([flash](./flash.md)). `clear_flash()`는 모두 닫는다. id는 브라우저가 만들어 서버는 모른다 |
| `await focus_on(selector)` | 요소에 포커스 |
| `await scroll_into_view(element_id, *, behavior="auto", block="start", inline="nearest")` | 요소를 보이게 스크롤 |
| `await defer(f, *args, **kwargs)` | 지금 이벤트가 끝난 뒤 `f`를 부른다. 호출은 클라이언트 이벤트처럼 연결을 한 바퀴 돌아 이름으로 다시 들어오므로, `f`는 **클라이언트가 부를 수 있는 이 컴포넌트의 핸들러**여야 한다. `_` 헬퍼나 프레임워크 메서드를 넘기면 오류 없이 경고 로그 한 줄만 남기고 버려진다 |
| `await start_async(name, coro)` / `await cancel_async(name)` | 백그라운드 작업 ([async-operations](./async-operations.md)) |
| `await assign_async(coro, *, on_error=None)` | 결과를 `AsyncResult` 필드로 받는다 |
| `await stream(name, items, *, template=None, dom_id=None, limit=0, at=-1, reset=True)` | 스트림을 채우거나 다시 채운다 ([Streams API 심화 튜토리얼](../tutorials/06-streams-api.md)). `reset=False`면 화면의 것을 두고 항목들을 `at` 자리에 주어진 순서대로 연산 하나로 넣는다([배치 삽입](../tutorials/06-streams-api.md#1-배치-삽입)) |
| `await stream_insert(name, item, *, at=-1, template=None, dom_id=None, limit=0, update_only=False)` / `await stream_delete(name, dom_id)` | 스트림 항목. `update_only=True`는 그 id가 화면에 있을 때만 제자리에서 바꾸고, 없으면 아무것도 넣지 않는다([Streams 튜토리얼](../tutorials/06-streams-api.md#이미-화면에-있는-항목이면-제자리-갱신)). 셋 다 첫 HTTP 렌더에서는 아무것도 하지 않는다 — 받을 소켓이 없다([dead view](./dead-view.md#약속하지-않는-것)) |
| `allow_upload(name, *, accept=None, max_entries=1, max_file_size=None, chunk_size=None, auto_upload=True, external=None)` | 업로드를 받는다 ([chunked-uploads](./chunked-uploads.md)) |
| `consume_uploads(name)` / `await cancel_upload(name, ref)` | 완료된 업로드를 꺼낸다 / 취소한다 |
| `attach_hook(name, stage, callback)` / `detach_hook(name, stage=None)` | 이 인스턴스에 수명주기 훅을 단다 ([lifecycle-hooks](./lifecycle-hooks.md)) |
| `await send_update(live_component_id, **assigns)` | 자식 LiveComponent에 값을 준다 |
| `await LiveComponent.send_to_parent(event, **kwargs)` | 부모의 핸들러를 부른다 |

컴포넌트 밖에서 알릴 때는 모듈 함수 `wireview.broadcast(channel, **kwargs)`와 `wireview.abroadcast(channel, **kwargs)`를
쓴다. 둘은 보내는 때가 다르다.

- `broadcast()`(동기): 트랜잭션 안이면 **커밋된 뒤**에 보낸다. 롤백되면 보내지 않는다. 트랜잭션 밖이면 즉시 보낸다
- `abroadcast()`(async): 트랜잭션과 상관없이 **즉시** 보낸다

알림을 받은 쪽이 DB를 다시 읽는다면 그 쓰기가 커밋된 뒤에 보내야 한다. 동기 코드에서는 `broadcast()`가 이미
그렇게 하므로 `transaction.on_commit()`으로 다시 감쌀 필요가 없다. async 코드에서 `abroadcast()`(또는 컴포넌트의
`broadcast()`)를 부를 때는 커밋 뒤에 부른다. 상세는 [broadcast](./broadcast.md).

## 구독을 다시 맞추는 때

세션은 아래 일을 처리한 뒤 연결의 모든 컴포넌트에서 `get_subscriptions()`를 다시 읽는다. 새로 생긴 토픽은 구독하고,
어느 컴포넌트도 원하지 않게 된 토픽은 구독을 해제한다. 해제한 토픽의 알림과 [Broadcast](./broadcast.md) 패치는 그 뒤로
그 연결에 오지 않는다.

- join. 첫 렌더를 보낸 뒤, `joined()`가 모아 둔 `broadcast()`를 보내기 전
- 클라이언트 이벤트와 훅 이벤트, `defer()`와 `send_to_parent()`가 다시 들어온 호출
- `notification()`·`mutation()` 전달. 그 알림을 받은 컴포넌트를 모두 처리한 뒤
- URL 이동(`params_changed()`), 페이지에서 컴포넌트가 떠남, 핸들러 예외로 컴포넌트를 버림

그래서 상태로 토픽을 정하는 컴포넌트는 핸들러나 `notification()`에서 그 상태를 바꾸기만 하면 된다. 권한이 사라졌다는
알림을 받고 그 객체의 토픽을 빼는 모양이 그 예다([live_session](./live-session.md#세션-백엔드가-정하는-한계)).
`start_async()` 작업과 `handle_async()`, `on_upload_complete()`, `send_render()`, `send_update()`가 바꾼 상태는
그 자리에서 다시 읽지 않는다 — 다음 이벤트나 알림을 처리할 때 반영된다.

## 렌더가 읽는 것

렌더는 템플릿을 그리기 전에 컴포넌트의 **공개 속성을 모두** 읽어 템플릿 컨텍스트에 넣는다. 밑줄로 시작하지 않는
필드와 property 전부이고, 메서드는 빠진다. 템플릿이 그 이름을 쓰는지는 보지 않는다. 그래서 템플릿이 쓰지 않는
property도 렌더마다 계산되고, 그 안의 쿼리도 렌더마다 돈다. 템플릿에서 쓰지 않을 값을 계산하는 property는
밑줄 이름(`_stats`)으로 두거나 메서드로 만든다. 렌더가 무엇을 쿼리하는지는 [렌더의 SQL 찾기](./render-queries.md)가
property 이름으로 보여 준다.

컴포넌트의 이름 말고 컨텍스트에 있는 것은 `this`, `slots`, 그리고 `csrf_token`이다. `csrf_token`은 보는 사람의 CSRF
토큰이라 `{% csrf_token %}`을 쓴 일반 POST 폼이 Django 페이지에서처럼 통과한다([dead view](./dead-view.md#폼의-csrf-토큰)).
같은 이름의 필드나 property가 있으면 그것이 이긴다.

`Meta.lazy_properties = True`이면 sync property(`property`, `functools.cached_property`)는 미리 읽지 않고
**템플릿이 그 이름을 읽을 때** 렌더마다 한 번 읽는다(#187). 템플릿이 쓰지 않는 property는 돌지 않고, 그 SQL도
나가지 않는다. 필드와 async property는 지금처럼 템플릿 전에 읽는다.

```python
class Dashboard(Component):
    class Meta:
        template_name = "dashboard.html"
        lazy_properties = True

    @property
    def open_orders(self):  # 템플릿이 {{ open_orders }}를 읽을 때만 쿼리한다
        return Order.objects.filter(status="open").count()
```

- 이름은 어디서 읽혀도 된다. `{% include %}`한 템플릿, `{% extends %}`한 부모, `{% with %}`, `{% class %}`·`{% cond %}`,
  컨텍스트를 받는 사용자 태그(`context["x"]`)가 읽어도 그때 계산한다. 컨텍스트를 통째로 순회하는 코드
  (`context.flatten()`, `dict(context)`)는 남은 property를 모두 읽는다 — 결과는 같고 아끼는 것만 사라진다.
- **`{{ this.x }}`는 컨텍스트가 아니라 컴포넌트를 읽는다.** 그래서 `{{ x }}`와 `{{ this.x }}`를 함께 쓰면 두 번
  계산된다. 템플릿이 `{{ this.x }}`만 쓰면 한 번이다.
- property가 던진 예외는 지금처럼 렌더를 실패시킨다. Django는 변수 조회에서 `AttributeError`나
  `{% if a and b %}` 안의 예외를 삼켜 빈 값으로 그리지만, 렌더가 끝난 뒤 그 예외를 다시 던진다.
- temporary assign이 초기화된 뒤의 렌더는 지금처럼 모든 이름을 미리 읽는다. 어느 부분이 초기화된 값만
  읽었는지 가리려면 다 읽어야 한다.
- **property가 순수 조회일 때만 켠다.** 템플릿이 쓰지 않는 property의 부작용과 예외는 사라진다. 렌더
  중에 필드를 바꾸는 property는 `{% tag_header %}`가 이미 상태를 서명한 뒤에 돌 수 있어, 서명 상태와
  본문이 서로 다른 값을 말하게 된다.

## 내부

밑줄이 없지만 공개가 아닌 것: `LiveComponent.myself`. Pydantic `BaseModel`에서 온 멤버
(`model_dump` 등)는 Pydantic의 것이고 wireview가 약속하지 않는다.
